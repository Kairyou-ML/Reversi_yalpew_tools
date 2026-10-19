from __future__ import annotations

import io
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image


DEFAULT_INSTALLATIONS = (
    Path(r"C:\LDPlayer\LDPlayer14"),
    Path(r"C:\LDPlayer\LDPlayer9"),
    Path(r"C:\Program Files\LDPlayer\LDPlayer9"),
    Path(r"C:\Program Files\LDPlayer9"),
)


@dataclass(frozen=True)
class LdInstance:
    index: int
    name: str
    running: bool
    width: int
    height: int
    dpi: int


class LdPlayerClient:
    def __init__(self, installation: str | Path | None = None, index: int | None = None):
        self.installation = Path(installation) if installation else self._find_installation()
        self.console = self.installation / "ldconsole.exe"
        self.adb = self.installation / "adb.exe"
        self.index = index
        self._active_serial: str | None = None
        self._last_capture_size: tuple[int, int] | None = None
        # On the target LDPlayer machine a warmed PNG capture averages ~209 ms
        # while raw RGBA averages ~389 ms (larger ADB transfer). Keep raw support
        # available, but prefer the measured faster transport.
        self._prefer_raw = False
        self._remote_capture = "/sdcard/reversi_bot_frame.png"
        self._local_capture = Path(tempfile.gettempdir()) / f"reversi_bot_ld_{index if index is not None else 'auto'}.png"

    @staticmethod
    def _find_installation() -> Path:
        for directory in DEFAULT_INSTALLATIONS:
            if (directory / "ldconsole.exe").exists():
                return directory
        raise FileNotFoundError("Không tìm thấy LDPlayer. Hãy truyền đường dẫn bằng --ld-dir.")

    def _run(self, arguments: list[str], timeout: float = 10) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [str(self.console), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )

    def instances(self) -> list[LdInstance]:
        result = self._run(["list2"])
        if result.returncode != 0:
            raise RuntimeError(result.stdout.decode(errors="replace").strip())
        instances: list[LdInstance] = []
        for raw_line in result.stdout.decode(errors="replace").splitlines():
            parts = [part.strip() for part in raw_line.split(",")]
            if len(parts) < 10 or not parts[0].isdigit():
                continue
            instances.append(
                LdInstance(
                    index=int(parts[0]),
                    name=parts[1],
                    running=parts[4] == "1",
                    width=int(parts[7]),
                    height=int(parts[8]),
                    dpi=int(parts[9]),
                )
            )
        return instances

    def selected_instance(self) -> LdInstance:
        instances = self.instances()
        if self.index is None:
            running = [instance for instance in instances if instance.running]
            if len(running) == 1:
                return running[0]
            if len(instances) == 1:
                return instances[0]
            if len(running) > 1:
                raise RuntimeError("Có nhiều LDPlayer đang chạy; hãy chọn --index cụ thể")
            raise RuntimeError("Không tìm thấy LDPlayer instance")
        for instance in instances:
            if instance.index == self.index:
                return instance
        raise RuntimeError(f"Không có LDPlayer instance index {self.index}")

    def capture(self, fast: bool = False) -> Image.Image:
        # Direct ADB is both faster and safer than writing a shared PNG on the
        # emulated SD card.  Mode 2 asks Android for raw RGBA pixels, avoiding
        # PNG compression/decompression entirely.
        direct = self._capture_direct_adb(fast=fast)
        if direct is not None:
            return direct
        instance = self.selected_instance()
        index = instance.index
        shot = self._run(
            ["adb", "--index", str(index), "--command", f"shell screencap -p {self._remote_capture}"],
            timeout=8,
        )
        if shot.returncode != 0 or not instance.running:
            detail = shot.stdout.decode(errors="replace").strip()
            raise RuntimeError(
                f"Đã thấy '{instance.name}' nhưng ADB chưa thấy Android đang chạy. "
                f"Hãy mở LDPlayer, đợi vào màn hình Android rồi thử lại. {detail}"
            )
        pull = self._run(
            ["pull", "--index", str(index), "--remote", self._remote_capture, "--local", str(self._local_capture)],
            timeout=8,
        )
        if pull.returncode != 0 or not self._local_capture.exists():
            raise RuntimeError(pull.stdout.decode(errors="replace").strip() or "Không kéo được screenshot từ LDPlayer")
        data = self._local_capture.read_bytes()
        try:
            image = Image.open(io.BytesIO(data)).convert("RGB")
            self._last_capture_size = image.size
            return image
        except OSError as exc:
            raise RuntimeError("LDPlayer trả về screenshot không hợp lệ") from exc

    def tap(self, x: int, y: int) -> None:
        if self._last_capture_size is None:
            raise RuntimeError("Chưa có screenshot để kiểm tra tọa độ click")
        width, height = self._last_capture_size
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(f"Tọa độ click ({x}, {y}) nằm ngoài ảnh {width}x{height}")
        serial = self._active_serial
        if serial:
            result = subprocess.run(
                [str(self.adb), "-s", serial, "shell", "input", "tap", str(x), str(y)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=5,
                check=False,
            )
        else:
            instance = self.selected_instance()
            result = self._run(
                ["adb", "--index", str(instance.index), "--command", f"shell input tap {x} {y}"],
                timeout=5,
            )
        if result.returncode != 0:
            detail = result.stdout.decode(errors="replace").strip()
            raise RuntimeError(detail or "ADB tap thất bại")

    @staticmethod
    def _decode_raw_screencap(data: bytes) -> Image.Image | None:
        if len(data) < 12:
            return None
        width, height, pixel_format = struct.unpack_from("<III", data)
        if not (100 <= width <= 4096 and 100 <= height <= 4096):
            return None
        byte_count = width * height * 4
        # Android releases use either a 12-byte header or append a 4-byte
        # colour-space field. RGBA_8888/RGBX_8888 are both usable as RGB here.
        offsets = (16, 12) if len(data) >= 16 + byte_count else (12,)
        for offset in offsets:
            if len(data) >= offset + byte_count:
                try:
                    return Image.frombytes(
                        "RGBA", (width, height), data[offset:offset + byte_count], "raw", "RGBA"
                    ).convert("RGB")
                except (OSError, ValueError):
                    return None
        return None

    def _discover_serial(self) -> str | None:
        if self._active_serial:
            return self._active_serial
        devices = subprocess.run(
            [str(self.adb), "devices"], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=5, check=False,
        )
        serials = [
            parts[0]
            for line in devices.stdout.decode(errors="replace").splitlines()[1:]
            if len(parts := line.split()) >= 2 and parts[1] == "device"
        ]
        if len(serials) == 1:
            self._active_serial = serials[0]
            return serials[0]
        return None

    def _capture_direct_adb(self, fast: bool = False) -> Image.Image | None:
        serial = self._discover_serial()
        if serial is None:
            return None
        if fast and self._prefer_raw:
            raw = subprocess.run(
                [str(self.adb), "-s", serial, "exec-out", "screencap"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=8, check=False,
            )
            image = self._decode_raw_screencap(raw.stdout) if raw.returncode == 0 else None
            if image is not None:
                self._last_capture_size = image.size
                return image
        shot = subprocess.run(
            [str(self.adb), "-s", serial, "exec-out", "screencap", "-p"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=8,
            check=False,
        )
        if shot.returncode != 0 or not shot.stdout.startswith(b"\x89PNG"):
            return None
        try:
            image = Image.open(io.BytesIO(shot.stdout)).convert("RGB")
            self._last_capture_size = image.size
            return image
        except OSError:
            return None
