import threading
import time
import unittest
from collections import deque
from pathlib import Path

from PIL import Image

from reversi_bot.gui import PonderBook, ReversiMonitor, infer_transition
from reversi_bot.engine import SearchResult, apply_move, legal_moves
from reversi_bot.model import Disc, Move, make_board
from reversi_bot.weplay_vision import (
    FrameAnalysis, GridDetection, ScreenState, WePlayRecognizer, board_signature,
)


FIXTURES = Path(__file__).parent / "fixtures" / "weplay"


class FakeClient:
    def __init__(self):
        self.taps = []

    def tap(self, x, y):
        self.taps.append((x, y))


class FakeFastEngine:
    def __init__(self):
        self.calls = []
        self.edax = self

    def choose_move_fast(self, board, player, budget_seconds=0.7):
        move = legal_moves(board, player)[0] if legal_moves(board, player) else None
        self.calls.append((board, player, move))
        return SearchResult(move, 0.0, 1, 1, 1, "fake-fast")

    def choose_move(self, board, player, **_kwargs):
        return self.choose_move_fast(board, player)


class FixedRecognizer:
    def __init__(self, analysis):
        self.analysis = analysis

    def analyze(self, *_args, **_kwargs):
        return self.analysis


class DelayedEngine(FakeFastEngine):
    def __init__(self, delay):
        super().__init__()
        self.delay = delay

    def choose_move(self, board, player, **_kwargs):
        time.sleep(self.delay)
        return super().choose_move(board, player)


class AutoLoopTests(unittest.TestCase):
    def make_monitor(self):
        monitor = object.__new__(ReversiMonitor)
        monitor.recognizer = WePlayRecognizer.from_fixture_directory(FIXTURES)
        monitor.client = FakeClient()
        monitor.auto_click_enabled = threading.Event()
        monitor.auto_click_enabled.set()
        monitor.inferred_player = None
        monitor.recent_boards = deque(maxlen=3)
        monitor.recent_states = deque(maxlen=3)
        monitor.recent_grids = deque(maxlen=2)
        monitor.locked_game_grid = None
        monitor.last_clicked_signature = None
        monitor.pending_signature = None
        monitor.expected_after_click_signature = None
        monitor.confirmed_own_signature = None
        monitor.pending_since = 0.0
        monitor.pending_early_click = False
        monitor.early_retry_signatures = set()
        monitor.pending_warning_emitted = False
        monitor.last_screen_action = None
        monitor.mode_lock = threading.Lock()
        monitor.requested_play_mode = "normal"
        monitor.active_play_mode = "normal"
        monitor.current_state = None
        monitor.ponder_future = None
        monitor.ponder_book = None
        monitor.ponder_source_signature = None
        monitor.ponder_lock = threading.Lock()
        monitor.ponder_engines = []
        monitor.ponder_init_lock = threading.Lock()
        monitor.ponder_ready = threading.Event()
        monitor.ponder_warm_future = None
        monitor.pass_lock = threading.Lock()
        monitor.pass_generation = 0
        monitor.pass_source_signature = None
        monitor.pass_replies = {}
        monitor.pass_future = None
        return monitor

    def feed(self, monitor, name, count):
        with Image.open(FIXTURES / name) as opened:
            image = opened.convert("RGB")
        return [
            monitor._analyze(image, None, require_stability=True, live=True)
            for _ in range(count)
        ]

    def test_lobby_and_result_click_once_while_matching_never_clicks(self):
        monitor = self.make_monitor()

        self.feed(monitor, "06_lobby_loop.png", 4)
        self.assertEqual(len(monitor.client.taps), 1)

        self.feed(monitor, "07_matching_loop.png", 4)
        self.assertEqual(len(monitor.client.taps), 1)

        self.feed(monitor, "10_loading.png", 4)
        self.assertEqual(len(monitor.client.taps), 1)
        self.assertIsNone(monitor.locked_game_grid)

        self.feed(monitor, "09_result_loop.png", 4)
        self.assertEqual(len(monitor.client.taps), 2)

        lobby_tap, replay_tap = monitor.client.taps
        self.assertTrue(180 <= lobby_tap[0] <= 300 and 390 <= lobby_tap[1] <= 470)
        self.assertTrue(180 <= replay_tap[0] <= 300 and 720 <= replay_tap[1] <= 790)

    def test_locked_valid_board_uses_two_frame_fast_path_and_clicks_once(self):
        monitor = self.make_monitor()
        self.feed(monitor, "08_playing_loop.png", 1)
        self.assertEqual(monitor.client.taps, [])

        self.feed(monitor, "08_playing_loop.png", 1)
        self.assertEqual(len(monitor.client.taps), 1)

        self.feed(monitor, "08_playing_loop.png", 2)
        self.assertEqual(len(monitor.client.taps), 1)

    def test_dry_run_explains_why_result_does_not_click_replay(self):
        monitor = self.make_monitor()
        monitor.auto_click_enabled.clear()

        updates = self.feed(monitor, "09_result_loop.png", 4)

        self.assertEqual(monitor.client.taps, [])
        self.assertIn("AUTO LOOP OFF nên không bấm Chơi lại", updates[-1].text)

    def test_opponent_left_clicks_exit_once_then_waits_for_lobby(self):
        monitor = self.make_monitor()

        updates = self.feed(monitor, "12_opponent_left.png", 5)

        self.assertEqual(len(monitor.client.taps), 1)
        x, y = monitor.client.taps[0]
        self.assertTrue(150 <= x <= 250 and 380 <= y <= 440)
        self.assertTrue(any("đã click Thoát" in update.text for update in updates))

    def test_transition_recognizes_each_exact_opponent_move(self):
        monitor = self.make_monitor()
        with Image.open(FIXTURES / "03_playing_initial.png") as image:
            analysis = monitor.recognizer.analyze(image, Disc.BLACK)
        before = analysis.board
        self.assertIsNotNone(before)
        for move in legal_moves(before, Disc.BLACK):
            after = apply_move(before, Disc.BLACK, move)
            self.assertEqual(infer_transition(before, after, Disc.BLACK), move)

    def test_mode2_builds_replies_for_every_opponent_branch_best_first(self):
        monitor = self.make_monitor()
        monitor.ponder_engines = [FakeFastEngine()]
        with Image.open(FIXTURES / "03_playing_initial.png") as image:
            analysis = monitor.recognizer.analyze(image, Disc.BLACK)
        before = analysis.board
        opponent_moves = legal_moves(before, Disc.WHITE)
        monitor.ponder_source_signature = "source"
        book = monitor._build_ponder_book(before, Disc.BLACK, "source")
        self.assertEqual(len(book.responses), len(opponent_moves))
        self.assertEqual(book.predicted_best, opponent_moves[0])
        self.assertEqual(monitor.ponder_engines[0].calls[0][1], Disc.WHITE)
        self.assertEqual(monitor.ponder_engines[0].calls[1][0], apply_move(before, Disc.WHITE, book.predicted_best))

    def test_mode2_publishes_finished_case_without_waiting_for_slow_cases(self):
        monitor = self.make_monitor()
        monitor.ponder_engines = [
            DelayedEngine(0.03), DelayedEngine(0.45),
            DelayedEngine(0.45), DelayedEngine(0.45),
        ]
        with Image.open(FIXTURES / "03_playing_initial.png") as image:
            before = monitor.recognizer.analyze(image, Disc.BLACK).board
        signature = board_signature(before)
        monitor.ponder_source_signature = signature
        worker = threading.Thread(
            target=monitor._build_ponder_book,
            args=(before, Disc.BLACK, signature),
        )
        worker.start()
        time.sleep(0.15)
        with monitor.ponder_lock:
            ready = len(monitor.ponder_book.responses)
            finished = monitor.ponder_book.finished_branches
            total = monitor.ponder_book.total_branches
        self.assertGreaterEqual(ready, 1)
        self.assertLess(finished, total)
        self.assertTrue(worker.is_alive())
        worker.join(timeout=2.0)
        self.assertFalse(worker.is_alive())

    def test_pass_chain_publishes_next_move_only_when_opponent_has_no_move(self):
        monitor = self.make_monitor()
        monitor.ponder_engines = [FakeFastEngine()]
        rows = [
            "WWWBB..W",
            "WBWWBBBB",
            "BBWBWBBB",
            "BBWBBBBB",
            "BBBBBWWW",
            "BBBBWWWW",
            ".BWWWBWW",
            "BWWWWWWW",
        ]
        symbols = {".": Disc.EMPTY, "B": Disc.BLACK, "W": Disc.WHITE}
        before = make_board([[symbols[cell] for cell in row] for row in rows])
        after = apply_move(before, Disc.WHITE, Move(6, 0))
        self.assertEqual(legal_moves(after, Disc.BLACK), [])
        self.assertTrue(legal_moves(after, Disc.WHITE))
        monitor._schedule_ponder(after, Disc.WHITE, board_signature(after))
        self.assertIsNone(monitor.ponder_source_signature)

        monitor._build_pass_chain(after, Disc.WHITE, generation=0)

        entry = monitor.pass_replies[board_signature(after)]
        self.assertIn(entry.reply.move, legal_moves(after, Disc.WHITE))
        self.assertEqual(entry.step, 1)

    def test_ready_branch_clicks_on_first_exact_board_frame_before_highlights(self):
        monitor = self.make_monitor()
        monitor.active_play_mode = "fast"
        monitor.requested_play_mode = "fast"
        source = make_board([
            [Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY],
            [Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY],
            [Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY],
            [Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.WHITE, Disc.BLACK, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY],
            [Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.BLACK, Disc.WHITE, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY],
            [Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY],
            [Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY],
            [Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY, Disc.EMPTY],
        ])
        opponent_move = Move(2, 3)
        child = apply_move(source, Disc.BLACK, opponent_move)
        reply_move = legal_moves(child, Disc.WHITE)[0]
        reply = SearchResult(reply_move, 0.0, 22, 1, 20, "instant-test")
        signature = board_signature(child)
        monitor.ponder_source_signature = board_signature(source)
        monitor.ponder_book = PonderBook(
            board_signature(source), source, opponent_move,
            responses={signature: (opponent_move, reply)},
            elapsed_ms=20, total_branches=4, finished_branches=1,
        )
        grid = GridDetection(50, 300, 45, 20.0)
        analysis = FrameAnalysis(
            ScreenState.PLAYING_WAIT, grid, child, Disc.WHITE,
            (), (), False, "board exact, highlights not visible yet",
        )
        monitor.recognizer = FixedRecognizer(analysis)
        monitor.locked_game_grid = grid
        image = Image.new("RGB", (540, 960), "black")

        update = monitor._analyze(image, None, require_stability=True, live=True)

        self.assertEqual(len(monitor.client.taps), 1)
        self.assertTrue(monitor.pending_early_click)
        self.assertIn("click early", update.text)

        time.sleep(0.36)
        legal = tuple(legal_moves(child, Disc.WHITE))
        monitor.recognizer = FixedRecognizer(FrameAnalysis(
            ScreenState.PLAYING_MY_TURN, grid, child, Disc.WHITE,
            legal, legal, True, "turn confirmed",
        ))
        retry = monitor._analyze(image, None, require_stability=True, live=True)
        monitor._analyze(image, None, require_stability=True, live=True)
        self.assertEqual(len(monitor.client.taps), 2)
        self.assertIn("confirmed retry", retry.text)


if __name__ == "__main__":
    unittest.main()
