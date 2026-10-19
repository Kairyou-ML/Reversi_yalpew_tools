import tempfile
import unittest
from pathlib import Path

from reversi_bot.demo import render_ppm
from reversi_bot.model import Disc, initial_board
from reversi_bot.pipeline import analyze_screenshot
from reversi_bot.vision import VisionConfig


class VisionPipelineTests(unittest.TestCase):
    def test_static_image_to_move_and_pixel(self):
        config = VisionConfig(
            board_rect=(80, 80, 720, 720),
            sample_radius_ratio=0.18,
            max_color_distance=95.0,
            min_cell_confidence=0.35,
            colors={
                "empty": (45, 145, 82),
                "black": (25, 28, 31),
                "white": (235, 235, 228),
            },
        )
        with tempfile.TemporaryDirectory() as folder:
            screenshot = Path(folder) / "board.ppm"
            render_ppm(screenshot, initial_board())
            result = analyze_screenshot(screenshot, config, Disc.BLACK, depth=2, time_limit_ms=1000)
        self.assertEqual(result.board, initial_board())
        self.assertIsNotNone(result.search.move)
        self.assertIsNotNone(result.target_pixel)
        self.assertGreaterEqual(result.recognition.min_confidence, 0.99)


if __name__ == "__main__":
    unittest.main()

