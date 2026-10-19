import unittest

from reversi_bot.engine import apply_move, choose_move, legal_moves
from reversi_bot.model import Disc, Move, initial_board


class EngineTests(unittest.TestCase):
    def test_initial_legal_moves_for_black(self):
        self.assertEqual(
            {move.algebraic for move in legal_moves(initial_board(), Disc.BLACK)},
            {"d3", "c4", "f5", "e6"},
        )

    def test_apply_move_flips_disc(self):
        board = apply_move(initial_board(), Disc.BLACK, Move(2, 3))
        self.assertEqual(board[2][3], Disc.BLACK)
        self.assertEqual(board[3][3], Disc.BLACK)

    def test_engine_returns_legal_move(self):
        board = initial_board()
        result = choose_move(board, Disc.BLACK, max_depth=3, time_limit_ms=1000)
        self.assertIn(result.move, legal_moves(board, Disc.BLACK))
        self.assertGreaterEqual(result.completed_depth, 1)

    def test_invalid_search_budget_is_rejected(self):
        with self.assertRaises(ValueError):
            choose_move(initial_board(), Disc.BLACK, max_depth=0)


if __name__ == "__main__":
    unittest.main()
