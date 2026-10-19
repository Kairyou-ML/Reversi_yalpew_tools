import unittest
from pathlib import Path

from reversi_bot.egaroucid import EgaroucidConsole, HybridMoveEngine, board_to_egaroucid, parse_move
from reversi_bot.engine import SearchResult, apply_move, legal_moves
from reversi_bot.model import Disc, Move, initial_board


class EgaroucidAdapterTests(unittest.TestCase):
    @staticmethod
    def position_after(plies):
        board, player = initial_board(), Disc.BLACK
        for _ in range(plies):
            moves = legal_moves(board, player)
            if not moves:
                player = player.opponent
                moves = legal_moves(board, player)
            board = apply_move(board, player, moves[0])
            player = player.opponent
        if not legal_moves(board, player):
            player = player.opponent
        return board, player

    def test_initial_board_serialization_matches_console_protocol(self):
        self.assertEqual(
            board_to_egaroucid(initial_board(), Disc.BLACK),
            "---------------------------WB------BW--------------------------- B",
        )

    def test_parse_move_ignores_prompts(self):
        self.assertEqual(parse_move("> > e6\n"), Move(5, 4))

    def test_official_binary_returns_a_legal_move(self):
        project_root = Path(__file__).resolve().parents[1]
        executable = project_root / "engines" / "egaroucid" / "Egaroucid_for_Console_7_8_1_SIMD.exe"
        if not executable.exists():
            self.skipTest("Bundled Egaroucid binary is absent")
        engine = EgaroucidConsole(executable, level=5, threads=2, timeout_seconds=10.0)
        try:
            result = engine.choose_move(initial_board(), Disc.BLACK)
        finally:
            engine.close()
        self.assertIn(result.move, legal_moves(initial_board(), Disc.BLACK))
        self.assertTrue(result.engine.startswith("Egaroucid"))

    def test_hybrid_falls_back_if_external_engine_fails(self):
        external = EgaroucidConsole(Path("missing.exe"))
        result = HybridMoveEngine(external).choose_move(initial_board(), Disc.BLACK)
        self.assertIn(result.move, legal_moves(initial_board(), Disc.BLACK))
        self.assertTrue(result.engine.startswith("Python fallback"))

    def test_opening_uses_edax_instead_of_python_when_egaroucid_fails(self):
        class BrokenEgaroucid:
            def choose_move(self, *args, **kwargs):
                raise OSError("simulated pipe failure")

        class WorkingEdax:
            def __init__(self):
                self.levels = []

            def choose_move(self, board, player, *, level=None, timeout_seconds=None):
                self.levels.append(level)
                return SearchResult(legal_moves(board, player)[0], 0.0, level, 0, 1, f"Edax L{level}")

        edax = WorkingEdax()
        result = HybridMoveEngine(BrokenEgaroucid(), edax).choose_move(initial_board(), Disc.BLACK)
        self.assertIn(result.move, legal_moves(initial_board(), Disc.BLACK))
        self.assertEqual(edax.levels, [16])
        self.assertTrue(result.engine.startswith("Opening fallback Edax"))

    def test_adaptive_policy_uses_book_opening_and_rush_endgame(self):
        class FakeConsole:
            def __init__(self):
                self.levels = []

            def choose_move(self, board, player, *, level=None, timeout_seconds=None):
                self.levels.append(level)
                return SearchResult(legal_moves(board, player)[0], 0.0, 0, 0, 1, f"L{level}")

        opening = FakeConsole()
        HybridMoveEngine(opening).choose_move(initial_board(), Disc.BLACK)
        self.assertEqual(opening.levels, [6])

        late_board, late_player = self.position_after(46)
        late = FakeConsole()
        result = HybridMoveEngine(late).choose_move(late_board, late_player)
        self.assertEqual(late.levels, [9])
        self.assertIn(result.move, legal_moves(late_board, late_player))

    def test_verified_squeeze_can_choose_lower_opponent_mobility(self):
        board, player = self.position_after(20)
        moves = legal_moves(board, player)
        ranked = sorted(
            moves,
            key=lambda move: HybridMoveEngine._opponent_mobility(board, player, move)[0],
        )
        low, high = ranked[0], ranked[-1]
        low_count = HybridMoveEngine._opponent_mobility(board, player, low)[0]
        high_count = HybridMoveEngine._opponent_mobility(board, player, high)[0]
        if low_count + 2 > high_count:
            self.skipTest("Deterministic fixture has no 2-move mobility gap")
        engine = HybridMoveEngine(object())
        result = engine._pick_verified_squeeze(
            board,
            player,
            SearchResult(low, 0.0, 12, 0, 10, "Egaroucid"),
            SearchResult(high, 0.0, 28, 0, 10, "Edax"),
            20,
        )
        self.assertEqual(result.move, low)
        self.assertIn("mobility", result.engine)

    def test_forced_move_skips_external_engine(self):
        board, player = self.position_after(16)
        self.assertEqual(len(legal_moves(board, player)), 1)

        class MustNotRun:
            def choose_move(self, *args, **kwargs):
                raise AssertionError("External engine should not run for a forced move")

        result = HybridMoveEngine(MustNotRun()).choose_move(board, player)
        self.assertEqual(result.move, legal_moves(board, player)[0])
        self.assertIn("bắt buộc", result.engine)


if __name__ == "__main__":
    unittest.main()
