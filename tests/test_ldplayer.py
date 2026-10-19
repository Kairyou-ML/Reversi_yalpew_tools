import unittest
import struct
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from reversi_bot.ldplayer import LdPlayerClient


class LdPlayerSafetyTests(unittest.TestCase):
    def make_client(self):
        client = object.__new__(LdPlayerClient)
        client.installation = Path(r"C:\fake")
        client.console = client.installation / "ldconsole.exe"
        client.adb = client.installation / "adb.exe"
        client.index = 0
        client._active_serial = None
        client._last_capture_size = None
        return client

    def test_tap_requires_a_prior_screenshot(self):
        with self.assertRaises(RuntimeError):
            self.make_client().tap(100, 100)

    def test_tap_rejects_coordinates_outside_last_screenshot(self):
        client = self.make_client()
        client._last_capture_size = (540, 960)
        with self.assertRaises(ValueError):
            client.tap(540, 100)

    @patch("reversi_bot.ldplayer.subprocess.run")
    def test_tap_targets_the_same_adb_device_used_for_capture(self, run):
        run.return_value = CompletedProcess(args=[], returncode=0, stdout=b"")
        client = self.make_client()
        client._active_serial = "emulator-5554"
        client._last_capture_size = (540, 960)

        client.tap(123, 456)

        command = run.call_args.args[0]
        self.assertEqual(
            command,
            [str(client.adb), "-s", "emulator-5554", "shell", "input", "tap", "123", "456"],
        )

    def test_raw_screencap_decoder_accepts_android_rgba_header(self):
        pixels = bytes([10, 20, 30, 255]) * (100 * 120)
        image = LdPlayerClient._decode_raw_screencap(struct.pack("<III", 100, 120, 1) + pixels)
        self.assertIsNotNone(image)
        self.assertEqual(image.size, (100, 120))
        self.assertEqual(image.getpixel((0, 0)), (10, 20, 30))


if __name__ == "__main__":
    unittest.main()
