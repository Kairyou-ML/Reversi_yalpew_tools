import json
import unittest
from pathlib import Path

from PIL import Image

from reversi_bot.model import Disc
from reversi_bot.engine import legal_moves
from reversi_bot.weplay_vision import (
    ScreenState,
    GridDetection,
    WePlayRecognizer,
    board_signature,
    infer_player,
    locate_lobby_primary_button,
    locate_opponent_left_exit_button,
    locate_replay_button,
    read_board,
)


FIXTURES = Path(__file__).parent / "fixtures" / "weplay"


class WePlayVisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recognizer = WePlayRecognizer.from_fixture_directory(FIXTURES)
        cls.truth = json.loads((FIXTURES / "ground_truth.json").read_text(encoding="utf-8"))

    def analyze(self, name):
        with Image.open(FIXTURES / name) as image:
            return self.recognizer.analyze(image, Disc.BLACK)

    def test_non_board_states(self):
        self.assertEqual(self.analyze("01_lobby.png").state, ScreenState.LOBBY)
        self.assertEqual(self.analyze("02_matching.png").state, ScreenState.MATCHING)
        self.assertEqual(self.analyze("06_lobby_loop.png").state, ScreenState.LOBBY)
        self.assertEqual(self.analyze("07_matching_loop.png").state, ScreenState.MATCHING)
        self.assertEqual(self.analyze("10_loading.png").state, ScreenState.LOADING)

    def test_loading_screen_never_exposes_a_grid_or_board(self):
        loading = self.analyze("10_loading.png")
        self.assertIsNone(loading.grid)
        self.assertIsNone(loading.board)
        self.assertFalse(loading.board_validated)

    def test_opponent_left_modal_is_detected_before_board_logic(self):
        result = self.analyze("12_opponent_left.png")
        with Image.open(FIXTURES / "12_opponent_left.png") as image:
            target = locate_opponent_left_exit_button(image)
        self.assertEqual(result.state, ScreenState.OPPONENT_LEFT)
        self.assertIsNone(result.grid)
        self.assertIsNone(result.board)
        self.assertIsNotNone(target)
        self.assertTrue(150 <= target[0] <= 250 and 380 <= target[1] <= 440)

    def test_opponent_left_detector_does_not_match_existing_screens(self):
        for path in FIXTURES.glob("*.png"):
            if path.name == "12_opponent_left.png":
                continue
            with self.subTest(name=path.name), Image.open(path) as image:
                self.assertIsNone(locate_opponent_left_exit_button(image))

    def test_loop_action_buttons_are_inside_expected_controls(self):
        with Image.open(FIXTURES / "06_lobby_loop.png") as image:
            lobby = locate_lobby_primary_button(image)
            self.assertIsNotNone(lobby)
            self.assertTrue(180 <= lobby[0] <= 300 and 390 <= lobby[1] <= 470)
        result = self.analyze("09_result_loop.png")
        with Image.open(FIXTURES / "09_result_loop.png") as image:
            replay = locate_replay_button(image, result.grid)
            self.assertIsNotNone(replay)
            self.assertTrue(180 <= replay[0] <= 300 and 720 <= replay[1] <= 790)

    def test_new_game_and_result_layouts(self):
        playing = self.analyze("08_playing_loop.png")
        result = self.analyze("09_result_loop.png")
        self.assertEqual(playing.state, ScreenState.PLAYING_MY_TURN)
        self.assertTrue(playing.board_validated)
        self.assertEqual(result.state, ScreenState.RESULT)

    def test_shifted_false_grid_inside_a_game_is_not_a_result(self):
        with Image.open(FIXTURES / "08_playing_loop.png") as image:
            false_grid = GridDetection(left=63, top=390, spacing=45, strength=12.0)
            result = self.recognizer.analyze(image, Disc.BLACK, grid_override=false_grid)
        self.assertNotEqual(result.state, ScreenState.RESULT)

    def test_playing_boards_match_ground_truth(self):
        for name in ("03_playing_initial.png", "04_playing_midgame.png"):
            with self.subTest(name=name):
                result = self.analyze(name)
                expected = self.truth[name]
                self.assertEqual(result.state.value, expected["state"])
                self.assertEqual(board_signature(result.board), expected["board"])
                self.assertEqual([move.algebraic for move in result.highlighted_moves], expected["highlights"])
                self.assertTrue(result.board_validated)

    def test_player_color_is_inferred_from_highlights_and_rules(self):
        with Image.open(FIXTURES / "03_playing_initial.png") as image:
            result = self.recognizer.analyze(image)
        self.assertEqual(result.detected_player, Disc.BLACK)
        self.assertTrue(result.board_validated)

    def test_white_can_be_inferred_without_a_white_screenshot(self):
        black_frame = self.analyze("03_playing_initial.png")
        white_highlights = legal_moves(black_frame.board, Disc.WHITE)
        self.assertEqual(infer_player(black_frame.board, white_highlights), Disc.WHITE)

    def test_result_board_and_counts(self):
        result = self.analyze("05_result.png")
        expected = self.truth["05_result.png"]
        self.assertEqual(result.state.value, expected["state"])
        self.assertEqual(board_signature(result.board), expected["board"])
        self.assertEqual(sum(cell == Disc.BLACK for row in result.board for cell in row), expected["black_count"])
        self.assertEqual(sum(cell == Disc.WHITE for row in result.board for cell in row), expected["white_count"])

    def test_gray_lose_result_and_replay_button(self):
        # This fixture is a crop of the monitor preview, so its cyan annotation
        # prevents normal grid discovery. Supplying the grid isolates the exact
        # regression: gray LOSE banner/button must still be RESULT + clickable.
        grid = GridDetection(left=78, top=370, spacing=49, strength=14.2)
        with Image.open(FIXTURES / "11_result_lose_monitor_crop.png") as image:
            result = self.recognizer.analyze(image, Disc.BLACK, grid_override=grid)
            replay = locate_replay_button(image, grid)
        self.assertEqual(result.state, ScreenState.RESULT)
        self.assertIsNotNone(replay)
        self.assertTrue(220 <= replay[0] <= 330 and 790 <= replay[1] <= 860)

    def test_coloured_opponent_skin_is_mapped_against_classic_local_white(self):
        grid = GridDetection(left=5, top=11, spacing=30, strength=99.0)
        with Image.open(FIXTURES / "13_skin_blue_board_crop.png") as image:
            board, highlights = read_board(image, grid, Disc.WHITE)
        self.assertEqual(highlights, ())
        self.assertEqual(sum(cell == Disc.BLACK for row in board for cell in row), 8)
        self.assertEqual(sum(cell == Disc.WHITE for row in board for cell in row), 7)
        self.assertEqual(
            board_signature(board),
            "......../......../.WW..B../..WBB.../..BWB.../..WBBWW./....B.../........",
        )


if __name__ == "__main__":
    unittest.main()
