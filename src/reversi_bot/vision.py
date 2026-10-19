from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from .model import Board, Disc, make_board

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class VisionConfig:
    board_rect: tuple[int, int, int, int]
    sample_radius_ratio: float
    max_color_distance: float
    min_cell_confidence: float
    colors: dict[str, RGB]

    @classmethod
    def load(cls, path: str | Path) -> "VisionConfig":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            board_rect=tuple(payload["board_rect"]),
            sample_radius_ratio=float(payload.get("sample_radius_ratio", 0.18)),
            max_color_distance=float(payload.get("max_color_distance", 95.0)),
            min_cell_confidence=float(payload.get("min_cell_confidence", 0.35)),
            colors={key: tuple(value) for key, value in payload["colors"].items()},
        )


@dataclass(frozen=True)
class RecognitionResult:
    board: Board
    confidences: tuple[tuple[float, ...], ...]
    min_confidence: float


class RGBImage:
    def __init__(self, width: int, height: int, pixels: list[RGB]):
        if len(pixels) != width * height:
            raise ValueError("Pixel count does not match image dimensions")
        self.width, self.height, self.pixels = width, height, pixels

    def pixel(self, x: int, y: int) -> RGB:
        return self.pixels[y * self.width + x]


def _read_ppm(path: Path) -> RGBImage:
    data = path.read_bytes()
    index = 0

    def token() -> bytes:
        nonlocal index
        while index < len(data):
            if data[index:index + 1] == b"#":
                index = data.find(b"\n", index)
                if index < 0:
                    raise ValueError("Invalid PPM comment")
            elif data[index:index + 1].isspace():
                index += 1
            else:
                break
        start = index
        while index < len(data) and not data[index:index + 1].isspace():
            index += 1
        return data[start:index]

    magic = token()
    width, height, maximum = int(token()), int(token()), int(token())
    if magic != b"P6" or maximum != 255:
        raise ValueError("Only binary P6 PPM with max value 255 is supported")
    if index >= len(data) or not data[index:index + 1].isspace():
        raise ValueError("PPM header is missing its payload delimiter")
    if data[index:index + 2] == b"\r\n":
        index += 2
    else:
        index += 1
    raw = data[index:]
    if len(raw) != width * height * 3:
        raise ValueError("Invalid PPM payload length")
    pixels = [tuple(raw[pos:pos + 3]) for pos in range(0, len(raw), 3)]
    return RGBImage(width, height, pixels)


def load_image(path: str | Path) -> RGBImage:
    source = Path(path)
    if source.suffix.lower() == ".ppm":
        return _read_ppm(source)
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("PNG/JPEG requires Pillow. Run: python -m pip install -e .[images]") from exc
    with Image.open(source) as image:
        rgb = image.convert("RGB")
        return RGBImage(rgb.width, rgb.height, list(rgb.getdata()))


def _distance(left: RGB, right: RGB) -> float:
    return sum((a - b) ** 2 for a, b in zip(left, right)) ** 0.5


def recognize_board(image: RGBImage, config: VisionConfig) -> RecognitionResult:
    left, top, right, bottom = config.board_rect
    if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
        raise ValueError("board_rect lies outside the image")
    cell_width, cell_height = (right - left) / 8, (bottom - top) / 8
    radius_x = max(1, int(cell_width * config.sample_radius_ratio))
    radius_y = max(1, int(cell_height * config.sample_radius_ratio))
    labels = {"empty": Disc.EMPTY, "black": Disc.BLACK, "white": Disc.WHITE}
    rows: list[list[Disc]] = []
    confidence_rows: list[list[float]] = []

    for row in range(8):
        board_row: list[Disc] = []
        confidence_row: list[float] = []
        center_y = int(top + (row + 0.5) * cell_height)
        for col in range(8):
            center_x = int(left + (col + 0.5) * cell_width)
            samples = [
                image.pixel(x, y)
                for y in range(center_y - radius_y, center_y + radius_y + 1)
                for x in range(center_x - radius_x, center_x + radius_x + 1)
            ]
            observed = tuple(int(median(pixel[channel] for pixel in samples)) for channel in range(3))
            ranked = sorted((_distance(observed, color), name) for name, color in config.colors.items())
            best_distance, best_name = ranked[0]
            confidence = max(0.0, 1.0 - best_distance / config.max_color_distance)
            if confidence < config.min_cell_confidence:
                raise ValueError(
                    f"Low-confidence cell {chr(97 + col)}{row + 1}: RGB={observed}, confidence={confidence:.2f}"
                )
            board_row.append(labels[best_name])
            confidence_row.append(confidence)
        rows.append(board_row)
        confidence_rows.append(confidence_row)

    confidence_tuple = tuple(tuple(row) for row in confidence_rows)
    return RecognitionResult(make_board(rows), confidence_tuple, min(min(row) for row in confidence_tuple))


def cell_center(config: VisionConfig, row: int, col: int) -> tuple[int, int]:
    left, top, right, bottom = config.board_rect
    return (
        round(left + (col + 0.5) * (right - left) / 8),
        round(top + (row + 0.5) * (bottom - top) / 8),
    )
