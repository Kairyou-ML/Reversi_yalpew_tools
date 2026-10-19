import unittest
from pathlib import Path

from reversi_bot.edax import EdaxConsole, board_to_edax
from reversi_bot.engine import legal_moves
from reversi_bot.model import Disc, initial_board


class EdaxAdapterTests(unittest.TestCase):
    def test_initial_board_serialization_matches_edax_protocol(self):
        self.assertEqual(
            board_to_edax(initial_board(), Disc.BLACK),
            "---------------------------OX------XO--------------------------- X",
        )

    def test_official_binary_returns_a_legal_move(self):
        project_root = Path(__file__).resolve().parents[1]
        engine_dir = project_root / "engines" / "edax"
        executable = engine_dir / "wEdax-x86-64-v3.exe"
        if not executable.exists():
            self.skipTest("Bundled Edax binary is absent")
        engine = EdaxConsole(executable, engine_dir / "data" / "eval.dat", level=10, timeout_seconds=5.0)
        try:
            result = engine.choose_move(initial_board(), Disc.BLACK)
        finally:
            engine.close()
        self.assertIn(result.move, legal_moves(initial_board(), Disc.BLACK))
        self.assertTrue(result.engine.startswith("Edax"))


if __name__ == "__main__":
    unittest.main()
