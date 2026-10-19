from __future__ import annotations

import argparse
import queue
import threading
import time
import tkinter as tk
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

from .engine import SearchResult, apply_move, choose_move, legal_moves
from .edax import EdaxConsole
from .egaroucid import EgaroucidConsole, HybridMoveEngine
from .ldplayer import LdPlayerClient
from .model import Board, Disc, Move, format_board, initial_board
from .weplay_vision import (
    FrameAnalysis,
    GridDetection,
    ScreenState,
    WePlayRecognizer,
    annotate,
    board_signature,
    locate_lobby_primary_button,
    locate_opponent_left_exit_button,
    locate_replay_button,
    moves_text,
)


@dataclass(frozen=True)
class MonitorUpdate:
    image: Image.Image | None
    analysis: FrameAnalysis | None
    search: SearchResult | None
    text: str


@dataclass
class PonderBook:
    source_signature: str
    source_board: Board
    predicted_best: Move
    responses: dict[str, tuple[Move, SearchResult]] = field(default_factory=dict)
    elapsed_ms: int = 0
    total_branches: int = 0
    finished_branches: int = 0


@dataclass(frozen=True)
class PassReply:
    board: Board
    reply: SearchResult
    step: int


def infer_transition(before: Board, after: Board, player: Disc) -> Move | None:
    """Return the unique legal move that transforms before into after."""
    matches = [move for move in legal_moves(before, player) if apply_move(before, player, move) == after]
    return matches[0] if len(matches) == 1 else None


class ReversiMonitor:
    def __init__(self, root: tk.Tk, fixture_dir: Path, ld_dir: Path | None, index: int | None):
        self.root = root
        self.fixture_dir = fixture_dir
        self.recognizer = WePlayRecognizer.from_fixture_directory(fixture_dir)
        self.client = LdPlayerClient(ld_dir, index)
        self.project_root = Path(__file__).resolve().parents[2]
        self.move_engine = self._new_move_engine()
        self.move_engine.prewarm()
        self.ponder_engines: list[HybridMoveEngine] = []
        self.ponder_init_lock = threading.Lock()
        self.ponder_ready = threading.Event()
        self.ponder_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reversi-ponder")
        self.ponder_warm_future: Future[None] | None = None
        self.ponder_future: Future[PonderBook | None] | None = None
        self.ponder_book: PonderBook | None = None
        self.ponder_source_signature: str | None = None
        self.ponder_lock = threading.Lock()
        self.opening_result: SearchResult | None = None
        self.pass_lock = threading.Lock()
        self.pass_generation = 0
        self.pass_source_signature: str | None = None
        self.pass_replies: dict[str, PassReply] = {}
        self.pass_future: Future[None] | None = None
        self.mode_lock = threading.Lock()
        self.requested_play_mode = "normal"
        self.active_play_mode = "normal"
        self.current_state = ScreenState.UNKNOWN
        self.play_mode_var = tk.StringVar(value="normal")
        self.play_mode_status_var = tk.StringVar(value="Mode 1 — ổn định")

        self.events: queue.Queue[MonitorUpdate] = queue.Queue()
        self._finish_init()

    def _new_move_engine(self, fast: bool = False) -> HybridMoveEngine:
        project_root = self.project_root
        engine_exe = project_root / "engines" / "egaroucid" / "Egaroucid_for_Console_7_8_1_SIMD.exe"
        edax_dir = project_root / "engines" / "edax"
        return HybridMoveEngine(
            EgaroucidConsole(
                engine_exe, level=6, threads=1 if fast else 2,
                timeout_seconds=0.35 if fast else 5.0,
            ),
            EdaxConsole(
                edax_dir / "wEdax-x86-64-v3.exe",
                edax_dir / "data" / "eval.dat",
                level=21,
                threads=1 if fast else 2,
                timeout_seconds=0.35 if fast else 5.0,
            ),
        )

    def _finish_init(self) -> None:
        """Kept separate only to make the constructor state easy to audit."""
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.photo: ImageTk.PhotoImage | None = None
        self.recent_boards: deque[str] = deque(maxlen=3)
        self.recent_states: deque[ScreenState] = deque(maxlen=3)
        self.recent_grids: deque[GridDetection] = deque(maxlen=2)
        self.search_cache: dict[tuple[str, Disc], SearchResult] = {}
        self.signature_first_seen: dict[str, float] = {}
        self.locked_game_grid: GridDetection | None = None
        self.player_var = tk.StringVar(value="auto")
        self.auto_click_var = tk.BooleanVar(value=False)
        self.auto_click_enabled = threading.Event()
        self.inferred_player: Disc | None = None
        self.last_clicked_signature: str | None = None
        self.pending_signature: str | None = None
        self.expected_after_click_signature: str | None = None
        self.confirmed_own_signature: str | None = None
        self.pending_since = 0.0
        self.pending_early_click = False
        self.early_retry_signatures: set[str] = set()
        self.pending_warning_emitted = False
        self.last_screen_action: ScreenState | None = None
        self.state_var = tk.StringVar(value="Sẵn sàng — DRY RUN")
        self.detail_var = tk.StringVar(value="Chương trình chưa gửi bất kỳ thao tác nào.")
        self.mode_var = tk.StringVar(value="AUTO LOOP: OFF")
        self._build()
        self.root.after(100, self._poll_events)
        # v16 prepares speculative workers immediately in the background so
        # opening replies are ready before the user starts matchmaking.
        self._start_ponder_warmup()

    @property
    def player(self) -> Disc:
        return Disc.BLACK if self.player_var.get() == "black" else Disc.WHITE

    @property
    def configured_player(self) -> Disc | None:
        if self.player_var.get() == "auto":
            return None
        return self.player

    def _build(self) -> None:
        self.root.title("WePlay Reversi Auto Loop")
        self.root.geometry("920x720")
        self.root.minsize(760, 600)

        controls = ttk.Frame(self.root, padding=10)
        controls.pack(fill="x")
        ttk.Label(controls, text="Màu của bạn:").pack(side="left")
        ttk.Radiobutton(controls, text="Tự động", value="auto", variable=self.player_var).pack(side="left", padx=(6, 0))
        ttk.Radiobutton(controls, text="Đen", value="black", variable=self.player_var).pack(side="left", padx=(6, 0))
        ttk.Radiobutton(controls, text="Trắng", value="white", variable=self.player_var).pack(side="left", padx=(6, 14))
        ttk.Button(controls, text="Test ảnh mẫu", command=self.start_fixtures).pack(side="left", padx=4)
        ttk.Button(controls, text="Theo dõi LDPlayer", command=self.start_live).pack(side="left", padx=4)
        ttk.Button(controls, text="Dừng", command=self.stop).pack(side="left", padx=4)
        ttk.Checkbutton(
            controls,
            text="Tự động toàn vòng lặp",
            variable=self.auto_click_var,
            command=self._toggle_auto_click,
        ).pack(side="left", padx=(10, 0))
        ttk.Label(controls, textvariable=self.mode_var, foreground="#b00020").pack(side="right")

        play_modes = ttk.Frame(self.root, padding=(10, 0, 10, 8))
        play_modes.pack(fill="x")
        ttk.Label(play_modes, text="Lối chạy:").pack(side="left")
        ttk.Radiobutton(
            play_modes, text="Mode 1 — ổn định", value="normal",
            variable=self.play_mode_var, command=self._request_play_mode,
        ).pack(side="left", padx=(8, 12))
        ttk.Radiobutton(
            play_modes, text="Mode 2 — phản công nhanh", value="fast",
            variable=self.play_mode_var, command=self._request_play_mode,
        ).pack(side="left")
        ttk.Label(play_modes, textvariable=self.play_mode_status_var, foreground="#005a9c").pack(side="right")

        status = ttk.Frame(self.root, padding=(10, 0, 10, 8))
        status.pack(fill="x")
        ttk.Label(status, textvariable=self.state_var, font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(status, textvariable=self.detail_var).pack(anchor="w")

        content = ttk.Panedwindow(self.root, orient="horizontal")
        content.pack(fill="both", expand=True, padx=10)
        preview_frame = ttk.Labelframe(content, text="Ảnh nhận diện", padding=8)
        data_frame = ttk.Labelframe(content, text="Bàn cờ và quyết định", padding=8)
        content.add(preview_frame, weight=3)
        content.add(data_frame, weight=2)
        self.preview = ttk.Label(preview_frame, anchor="center")
        self.preview.pack(fill="both", expand=True)
        self.board_text = tk.Text(data_frame, width=32, height=28, font=("Consolas", 11), state="disabled")
        self.board_text.pack(fill="both", expand=True)

        log_frame = ttk.Labelframe(self.root, text="Nhật ký", padding=6)
        log_frame.pack(fill="both", padx=10, pady=10)
        self.log_text = tk.Text(log_frame, height=7, font=("Consolas", 9), state="disabled")
        self.log_text.pack(fill="both", expand=True)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _request_play_mode(self) -> None:
        requested = self.play_mode_var.get()
        with self.mode_lock:
            self.requested_play_mode = requested
            if self.current_state not in (ScreenState.PLAYING_MY_TURN, ScreenState.PLAYING_WAIT):
                self.active_play_mode = requested
                pending = False
            else:
                pending = self.active_play_mode != requested
        label = "Mode 2 — phản công nhanh" if requested == "fast" else "Mode 1 — ổn định"
        if pending:
            self.play_mode_status_var.set(f"{label} (áp dụng ván sau)")
            self._log(f"Đã chọn {label}; sẽ đổi khi sang ván kế tiếp")
        else:
            self.play_mode_status_var.set(label)
            self._log(f"Đã chọn {label}")
        if requested == "fast":
            self._start_ponder_warmup()

    def _latch_mode_between_games(self, state: ScreenState) -> None:
        self.current_state = state
        if state in (ScreenState.PLAYING_MY_TURN, ScreenState.PLAYING_WAIT):
            return
        with self.mode_lock:
            self.active_play_mode = self.requested_play_mode
            mode = self.active_play_mode
        self.ponder_book = None
        self.ponder_source_signature = None
        self._clear_pass_chain()
        if mode == "fast":
            self._start_ponder_warmup()

    def start_fixtures(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Đang chạy", "Hãy bấm Dừng trước khi bắt đầu nguồn khác.")
            return
        self.stop_event.clear()
        paths = sorted(self.fixture_dir.glob("*.png"))
        selected_player = self.configured_player
        self.worker = threading.Thread(target=self._fixture_loop, args=(paths, selected_player), daemon=True)
        self.worker.start()

    def start_live(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Đang chạy", "Monitor LDPlayer đã chạy hoặc đang test ảnh mẫu.")
            return
        if not self.auto_click_enabled.is_set():
            run_automatically = messagebox.askyesno(
                "Chọn chế độ chạy",
                "Bạn muốn bot TỰ ĐÁNH và tự bấm Chơi lại không?\n\n"
                "Chọn Yes: AUTO LOOP — bot có click vào LDPlayer.\n"
                "Chọn No: DRY RUN — chỉ quan sát và gợi ý, tuyệt đối không click.",
            )
            if run_automatically:
                self.auto_click_var.set(True)
                self._enable_auto_loop()
            else:
                self.auto_click_var.set(False)
                self.mode_var.set("DRY RUN — KHÔNG CLICK")
                self._log("CẢNH BÁO: đang chạy DRY RUN; bot không đánh và không bấm Chơi lại")
        self.stop_event.clear()
        self.recent_boards.clear()
        self.recent_states.clear()
        self.recent_grids.clear()
        self.search_cache.clear()
        if self.opening_result is not None:
            self.search_cache[(board_signature(initial_board()), Disc.BLACK)] = self.opening_result
        self.signature_first_seen.clear()
        self.locked_game_grid = None
        self.inferred_player = None
        self.last_clicked_signature = None
        self.pending_signature = None
        self.expected_after_click_signature = None
        self.confirmed_own_signature = None
        self.pending_early_click = False
        self.early_retry_signatures.clear()
        self.pending_warning_emitted = False
        self.last_screen_action = None
        self.ponder_book = None
        self.ponder_source_signature = None
        self._clear_pass_chain()
        with self.mode_lock:
            self.active_play_mode = self.requested_play_mode
            fast_selected = self.active_play_mode == "fast"
        if fast_selected:
            self._start_ponder_warmup()
        selected_player = self.configured_player
        self.worker = threading.Thread(target=self._live_loop, args=(selected_player,), daemon=True)
        self.worker.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.auto_click_enabled.clear()
        self.auto_click_var.set(False)
        self.mode_var.set("AUTO LOOP: OFF")
        self.state_var.set("Đã dừng")
        self._log("Đã yêu cầu dừng monitor")

    def _toggle_auto_click(self) -> None:
        if self.auto_click_var.get():
            confirmed = messagebox.askyesno(
                "Bật tự động toàn vòng lặp?",
                "Bot sẽ bấm Trận sơ cấp, chờ ghép, tự đánh và bấm Chơi lại. "
                "Bàn đã khóa và khớp luật cần 2 frame; trường hợp khác cần 3 frame. "
                "Mỗi thế cờ chỉ được bấm một lần. "
                "Bạn có thể bấm Dừng bất kỳ lúc nào.",
            )
            if not confirmed:
                self.auto_click_var.set(False)
                return
            self._enable_auto_loop()
        else:
            self.auto_click_enabled.clear()
            self.mode_var.set("DRY RUN — KHÔNG CLICK")
            self._log("Đã tắt tự động toàn vòng lặp; chuyển sang DRY RUN, bot không click")

    def _enable_auto_loop(self) -> None:
        self.auto_click_enabled.set()
        self.last_screen_action = None
        self.last_clicked_signature = None
        self.mode_var.set("AUTO LOOP: ON")
        self._log("Đã bật tự động toàn vòng lặp; bot sẽ click nước cờ và Chơi lại")

    def _fixture_loop(self, paths: list[Path], player: Disc | None) -> None:
        if not paths:
            self.events.put(MonitorUpdate(None, None, None, "Không tìm thấy ảnh fixture"))
            return
        for path in paths:
            if self.stop_event.is_set():
                return
            try:
                with Image.open(path) as opened:
                    image = opened.convert("RGB")
                update = self._analyze(image, player, require_stability=False, live=False)
                self.events.put(MonitorUpdate(update.image, update.analysis, update.search, f"{path.name}: {update.text}"))
            except Exception as exc:  # GUI boundary: report and continue to next fixture
                self.events.put(MonitorUpdate(None, None, None, f"{path.name}: lỗi {exc}"))
            time.sleep(1.2)
        self.events.put(MonitorUpdate(None, None, None, "Đã test xong ảnh mẫu"))

    def _live_loop(self, player: Disc | None) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                self.frame_started_at = started
                with self.mode_lock:
                    fast_capture = self.active_play_mode == "fast"
                image = self.client.capture(fast=fast_capture)
                update = self._analyze(image, player, require_stability=True, live=True)
                self.events.put(update)
            except Exception as exc:  # keep monitor alive for transient emulator/ADB failures
                self.events.put(MonitorUpdate(None, None, None, f"LDPlayer: {exc}"))
                self.stop_event.wait(1.5)
            elapsed = time.monotonic() - started
            target_period = 0.07 if fast_capture else 0.2
            self.stop_event.wait(max(0.0, target_period - elapsed))

    def _ensure_ponder_engines(self) -> list[HybridMoveEngine]:
        with self.ponder_init_lock:
            if not self.ponder_engines:
                # Eight one-thread workers cover the observed opening branch
                # counts; idle workers sleep and do not contend with midgame.
                self.ponder_engines = [self._new_move_engine(fast=True) for _ in range(8)]
                for engine in self.ponder_engines:
                    engine.prewarm()
        return self.ponder_engines

    def _warm_ponder_engines(self) -> None:
        engines = self._ensure_ponder_engines()
        # Speculative workers only need Edax. Do not make their readiness wait
        # for Egaroucid. The first successful result also becomes the instant
        # initial move, so the local-black opening never pays first-call cost.
        with ThreadPoolExecutor(max_workers=len(engines), thread_name_prefix="reversi-warm") as pool:
            futures: list[Future[SearchResult]] = []
            for engine in engines:
                if engine.edax is not None:
                    futures.append(pool.submit(
                        engine.edax.choose_move,
                        initial_board(), Disc.BLACK, level=16, timeout_seconds=3.0,
                    ))
            for future in futures:
                try:
                    result = future.result()
                    if self.opening_result is None:
                        self.opening_result = SearchResult(
                            result.move, result.score, result.completed_depth, result.nodes,
                            result.elapsed_ms, "Warm opening Edax L16",
                        )
                        self.search_cache[(board_signature(initial_board()), Disc.BLACK)] = self.opening_result
                except (OSError, RuntimeError, TimeoutError, ValueError):
                    pass
        self.ponder_ready.set()

    def _start_ponder_warmup(self) -> None:
        if self.ponder_ready.is_set():
            return
        if self.ponder_warm_future is None or self.ponder_warm_future.done():
            self.ponder_warm_future = self.ponder_executor.submit(self._warm_ponder_engines)

    def _build_ponder_book(self, board: Board, player: Disc, signature: str) -> PonderBook | None:
        started = time.monotonic()
        engines = self._ensure_ponder_engines()
        opponent_moves = list(legal_moves(board, player.opponent))
        prediction = engines[0].choose_move_fast(board, player.opponent, budget_seconds=0.55)
        predicted_best = prediction.move if prediction.move in opponent_moves else opponent_moves[0]
        ordered = [predicted_best, *[move for move in opponent_moves if move != predicted_best]]
        book = PonderBook(
            signature, board, predicted_best,
            total_branches=len(ordered),
        )
        with self.ponder_lock:
            if signature != self.ponder_source_signature:
                return None
            self.ponder_book = book

        def solve_branch(index: int, opponent_move: Move) -> tuple[str, Move, SearchResult] | None:
            child = apply_move(board, player.opponent, opponent_move)
            engine = engines[index % len(engines)]
            try:
                if engine.edax is None:
                    return None
                reply = engine.edax.choose_move(
                    child, player, level=22, timeout_seconds=3.0
                )
                reply = SearchResult(
                    reply.move, reply.score, reply.completed_depth, reply.nodes,
                    reply.elapsed_ms, "Instant reply Edax L22 (3s/case)",
                )
            except (OSError, RuntimeError, TimeoutError, ValueError):
                return None
            return board_signature(child), opponent_move, reply

        pool = ThreadPoolExecutor(max_workers=len(engines), thread_name_prefix="reversi-branch")
        futures = [pool.submit(solve_branch, index, move) for index, move in enumerate(ordered)]
        for future in as_completed(futures):
            result = future.result()
            with self.ponder_lock:
                if signature != self.ponder_source_signature:
                    pool.shutdown(wait=False, cancel_futures=True)
                    return None
                book.finished_branches += 1
                book.elapsed_ms = int((time.monotonic() - started) * 1000)
                if result is not None:
                    child_signature, opponent_move, reply = result
                    book.responses[child_signature] = (opponent_move, reply)
        pool.shutdown(wait=False)
        return book

    def _collect_ponder_plan(self) -> None:
        future = self.ponder_future
        if future is None or not future.done():
            return
        self.ponder_future = None
        try:
            book = future.result()
        except (OSError, RuntimeError, TimeoutError, ValueError):
            self.ponder_book = None
            return
        if book is not None and book.source_signature == self.ponder_source_signature:
            with self.ponder_lock:
                self.ponder_book = book

    def _schedule_ponder(self, board: Board, player: Disc, signature: str) -> None:
        occupied = sum(cell != Disc.EMPTY for row in board for cell in row)
        # Mode 2 is intentionally an opening accelerator. Later positions keep
        # the normal hybrid engine budget instead of guessing across branches.
        if not legal_moves(board, player.opponent):
            return
        with self.pass_lock:
            if self.pass_source_signature is not None:
                self.pass_generation += 1
                self.pass_source_signature = None
                self.pass_replies.clear()
        if occupied > 22:
            return
        if not self.ponder_ready.is_set():
            self._start_ponder_warmup()
            return
        self._collect_ponder_plan()
        if self.ponder_source_signature == signature:
            return
        if self.ponder_future is not None and not self.ponder_future.done():
            return
        self.ponder_source_signature = signature
        self.ponder_book = None
        self.ponder_future = self.ponder_executor.submit(
            self._build_ponder_book, board, player, signature
        )

    def _clear_pass_chain(self) -> None:
        pass_lock = getattr(self, "pass_lock", None)
        if pass_lock is None:
            return
        with pass_lock:
            self.pass_generation += 1
            self.pass_source_signature = None
            self.pass_replies.clear()

    def _build_pass_chain(self, board_after_move: Board, player: Disc, generation: int) -> None:
        engines = self._ensure_ponder_engines()
        engine = engines[-1]
        current = board_after_move
        for step in range(1, 9):
            # These conditions are mutually exclusive with opponent pondering:
            # only our side has a move, so the remote side must pass.
            if legal_moves(current, player.opponent):
                return
            own_moves = legal_moves(current, player)
            if not own_moves:
                return
            if len(own_moves) == 1:
                reply = SearchResult(own_moves[0], 0.0, 0, 0, 0, "Pass-chain: nước bắt buộc")
            else:
                empties = sum(cell == Disc.EMPTY for row in current for cell in row)
                level = 60 if empties < 18 else 26
                timeout = 1.5 if empties <= 10 else 2.2 if empties < 18 else 3.0
                try:
                    if engine.edax is None:
                        raise RuntimeError("Edax unavailable")
                    result = engine.edax.choose_move(
                        current, player, level=level, timeout_seconds=timeout
                    )
                    reply = SearchResult(
                        result.move, result.score, result.completed_depth, result.nodes,
                        result.elapsed_ms, f"Pass-chain Edax L{level}",
                    )
                except (OSError, RuntimeError, TimeoutError, ValueError):
                    reply = engine.choose_move_fast(current, player, budget_seconds=0.9)
            if reply.move not in own_moves:
                return
            signature = board_signature(current)
            with self.pass_lock:
                if generation != self.pass_generation:
                    return
                self.pass_replies[signature] = PassReply(current, reply, step)
            current = apply_move(current, player, reply.move)

    def _schedule_pass_chain(
        self,
        board: Board,
        player: Disc,
        chosen: Move,
        source_signature: str,
        from_pass_cache: bool,
    ) -> None:
        if from_pass_cache:
            # The existing builder is already walking farther down this exact
            # deterministic chain and publishes each next reply incrementally.
            return
        board_after_move = apply_move(board, player, chosen)
        if legal_moves(board_after_move, player.opponent) or not legal_moves(board_after_move, player):
            return
        # A pass chain and an opponent-response book cannot both describe this
        # state. Drop the old response book before publishing pass continuations.
        self.ponder_book = None
        self.ponder_source_signature = None
        if not self.ponder_ready.is_set():
            self._start_ponder_warmup()
            return
        with self.pass_lock:
            if self.pass_source_signature == source_signature:
                return
            self.pass_generation += 1
            generation = self.pass_generation
            self.pass_source_signature = source_signature
            self.pass_replies.clear()
        if self.pass_future is not None and not self.pass_future.done():
            # A stale builder will observe the generation change and stop after
            # its current engine call; the new job is queued behind it.
            pass
        self.pass_future = self.ponder_executor.submit(
            self._build_pass_chain, board_after_move, player, generation
        )

    def _analyze(
        self,
        image: Image.Image,
        player: Disc | None,
        require_stability: bool,
        live: bool,
    ) -> MonitorUpdate:
        active_player = player or self.inferred_player
        raw_analysis = self.recognizer.analyze(image, active_player)
        analysis = raw_analysis
        grid_lock_note = ""
        playing_states = (ScreenState.PLAYING_MY_TURN, ScreenState.PLAYING_WAIT)
        if self.locked_game_grid is not None and raw_analysis.state in playing_states:
            analysis = self.recognizer.analyze(image, active_player, grid_override=self.locked_game_grid)
            if raw_analysis.grid != self.locked_game_grid:
                grid_lock_note = " — dùng khung bàn đã khóa"
        elif (
            self.locked_game_grid is None
            and raw_analysis.state in playing_states
            and raw_analysis.grid
            and raw_analysis.board
            and sum(cell != Disc.EMPTY for row in raw_analysis.board for cell in row) >= 4
            and any(cell == Disc.BLACK for row in raw_analysis.board for cell in row)
            and any(cell == Disc.WHITE for row in raw_analysis.board for cell in row)
        ):
            self.recent_grids.append(raw_analysis.grid)
            if len(self.recent_grids) == self.recent_grids.maxlen:
                first, second = self.recent_grids
                if (
                    abs(first.left - second.left) <= 3
                    and abs(first.top - second.top) <= 3
                    and abs(first.spacing - second.spacing) <= 1
                ):
                    self.locked_game_grid = second
                    grid_lock_note = " — đã khóa vị trí bàn cho ván này"
        if raw_analysis.state not in playing_states:
            self.recent_grids.clear()
            self.locked_game_grid = None
        if player is None and analysis.highlighted_moves and not analysis.board_validated:
            analysis = self.recognizer.analyze(image, None, grid_override=analysis.grid)
        if player is None and analysis.detected_player is not None:
            self.inferred_player = analysis.detected_player
        if player is None and analysis.state in (
            ScreenState.LOBBY,
            ScreenState.MATCHING,
            ScreenState.LOADING,
            ScreenState.OPPONENT_LEFT,
            ScreenState.RESULT,
        ):
            self.inferred_player = None
        self._latch_mode_between_games(analysis.state)
        with self.mode_lock:
            fast_mode = self.active_play_mode == "fast"
        self._collect_ponder_plan()
        signature = board_signature(analysis.board)
        verification = ""
        early_retry_now = False
        if self.pending_signature and signature and signature != self.pending_signature:
            verification = " — đã xác nhận bàn thay đổi sau click"
            if signature == self.expected_after_click_signature:
                self.confirmed_own_signature = signature
                verification += " đúng board dự kiến"
            else:
                self.confirmed_own_signature = None
            self.expected_after_click_signature = None
            self.pending_signature = None
            self.pending_early_click = False
            self.pending_warning_emitted = False
        elif (
            self.pending_signature == signature
            and self.pending_early_click
            and analysis.state == ScreenState.PLAYING_MY_TURN
            and analysis.board_validated
            and time.monotonic() - self.pending_since >= 0.35
            and signature not in self.early_retry_signatures
        ):
            # An exact-board early tap can arrive before WePlay accepts input.
            # Once highlights confirm the turn, unlock exactly one normal retry.
            self.early_retry_signatures.add(signature)
            self.pending_signature = None
            self.pending_early_click = False
            self.last_clicked_signature = None
            early_retry_now = True
        if signature and analysis.state in playing_states:
            self.recent_boards.append(signature)
            first_seen = getattr(self, "signature_first_seen", None)
            if first_seen is None:
                self.signature_first_seen = {}
                first_seen = self.signature_first_seen
            first_seen.setdefault(signature, getattr(self, "frame_started_at", time.monotonic()))
        elif analysis.state not in playing_states:
            self.recent_boards.clear()
            getattr(self, "search_cache", {}).clear()
            getattr(self, "signature_first_seen", {}).clear()
        self.recent_states.append(analysis.state)
        speculative_reply: SearchResult | None = None
        speculative_source: str | None = None
        pass_step: int | None = None
        fast_opponent_move: Move | None = None
        book = self.ponder_book if fast_mode else None
        pass_entry: PassReply | None = None
        if fast_mode and signature:
            with self.pass_lock:
                pass_entry = self.pass_replies.get(signature)
        if (
            pass_entry is not None
            and analysis.state in playing_states
            and analysis.board == pass_entry.board
            and (analysis.detected_player or active_player) is not None
            and pass_entry.reply.move in legal_moves(
                analysis.board, analysis.detected_player or active_player
            )
        ):
            speculative_reply = pass_entry.reply
            speculative_source = "pass"
            pass_step = pass_entry.step
        branch_response: tuple[Move, SearchResult] | None = None
        if book is not None and signature:
            with self.ponder_lock:
                branch_response = book.responses.get(signature)
        response_player = analysis.detected_player or active_player
        if (
            speculative_reply is None
            and book is not None
            and branch_response is not None
            and analysis.state in playing_states
            and analysis.board is not None
            and response_player is not None
        ):
            opponent_move, reply = branch_response
            transitioned = infer_transition(book.source_board, analysis.board, response_player.opponent)
            reply_is_legal = reply.move is None or reply.move in legal_moves(analysis.board, response_player)
            if transitioned == opponent_move and reply_is_legal:
                speculative_reply = reply
                speculative_source = "opponent"
                fast_opponent_move = opponent_move
        required_board_frames = (
            1 if speculative_reply is not None and self.locked_game_grid is not None
            else 2 if analysis.board_validated and self.locked_game_grid is not None
            else 3
        )
        recent_board_list = list(self.recent_boards)
        stable = not require_stability or (
            len(recent_board_list) >= required_board_frames
            and len(set(recent_board_list[-required_board_frames:])) == 1
        )
        if (
            fast_mode
            and speculative_reply is None
            and analysis.state == ScreenState.PLAYING_WAIT
            and (stable or signature == self.confirmed_own_signature)
            and analysis.board is not None
        ):
            ponder_player = analysis.detected_player or active_player
            # At the untouched initial board, no highlights means black is the
            # remote side if the local player is white. This enables thinking
            # during the very first opponent turn without permanently guessing
            # the user's colour; a transient frame for local black simply will
            # not match any predicted transition and falls back safely.
            if ponder_player is None and analysis.board == initial_board():
                ponder_player = Disc.WHITE
            if ponder_player is not None:
                self._schedule_ponder(analysis.board, ponder_player, signature)
        search: SearchResult | None = None
        chosen: Move | None = None
        normal_turn_ready = analysis.state == ScreenState.PLAYING_MY_TURN and analysis.board_validated
        speculative_turn_ready = speculative_reply is not None and analysis.state in playing_states
        if (normal_turn_ready or speculative_turn_ready) and stable and analysis.board:
            cache = getattr(self, "search_cache", None)
            if cache is None:
                self.search_cache = {}
                cache = self.search_cache
            decision_player = analysis.detected_player or active_player
            cache_key = (signature, decision_player)
            search = speculative_reply or cache.get(cache_key)
            if search is None:
                move_engine = getattr(self, "move_engine", None)
                search = (
                    move_engine.choose_move(analysis.board, decision_player)
                    if move_engine is not None
                    else choose_move(analysis.board, decision_player, max_depth=5, time_limit_ms=900)
                )
                cache[cache_key] = search
            chosen = search.move
            if fast_mode and chosen is not None and decision_player is not None:
                self._schedule_pass_chain(
                    analysis.board,
                    decision_player,
                    chosen,
                    signature,
                    from_pass_cache=speculative_source == "pass",
                )
        rendered = annotate(image, analysis, chosen)
        suffix = grid_lock_note
        if fast_mode and analysis.state == ScreenState.PLAYING_WAIT:
            with self.pass_lock:
                pass_count = len(self.pass_replies)
            if self.pass_future is not None and not self.pass_future.done():
                suffix += f" — Mode 2 đang tính chuỗi pass; đã sẵn sàng {pass_count} bước"
            elif pass_count:
                suffix += f" — Mode 2 đã cache chuỗi pass {pass_count} bước"
            elif self.ponder_book is not None:
                with self.ponder_lock:
                    ready_count = len(self.ponder_book.responses)
                    finished_count = self.ponder_book.finished_branches
                    total_count = self.ponder_book.total_branches
                    book_ms = self.ponder_book.elapsed_ms
                    predicted = self.ponder_book.predicted_best.algebraic
                suffix += (
                    f" — Mode 2 sẵn sàng {ready_count}/{total_count} case "
                    f"({finished_count} đã kết thúc, {book_ms} ms); ưu tiên {predicted}"
                )
            elif self.ponder_future is not None:
                suffix += " — Mode 2 đang khởi tạo case; mỗi worker có tối đa 3 giây"
        if analysis.state == ScreenState.PLAYING_MY_TURN and not stable:
            suffix = f" — đang chờ {required_board_frames} frame ổn định"
        elif chosen:
            if speculative_source == "pass":
                fast_note = f"; pass-chain bước {pass_step}, xác nhận 1 frame"
            elif fast_opponent_move is not None and book is not None:
                fast_note = f"; phản công sau {fast_opponent_move.algebraic}, bảng {book.elapsed_ms} ms"
            else:
                fast_note = ""
            suffix = f" — đề xuất {chosen.algebraic} ({search.engine if search else 'không rõ engine'}{fast_note})"
            can_click = (
                live
                and self.auto_click_enabled.is_set()
                and analysis.grid is not None
                and signature != self.last_clicked_signature
                and self.pending_signature is None
            )
            if can_click:
                x, y = analysis.grid.center(chosen)
                # Lock before invoking ADB: even a reported failure must not create
                # a rapid retry loop against the same board position.
                self.last_clicked_signature = signature
                try:
                    self.client.tap(x, y)
                    self.pending_signature = signature
                    self.expected_after_click_signature = board_signature(
                        apply_move(analysis.board, decision_player, chosen)
                    )
                    self.pending_since = time.monotonic()
                    self.pending_early_click = (
                        not early_retry_now
                        and (analysis.state != ScreenState.PLAYING_MY_TURN or not analysis.board_validated)
                    )
                    self.pending_warning_emitted = False
                    first_seen_at = getattr(self, "signature_first_seen", {}).get(signature, self.pending_since)
                    pipeline_ms = int((self.pending_since - first_seen_at) * 1000)
                    click_kind = "early" if self.pending_early_click else "confirmed"
                    retry_note = " retry" if early_retry_now else ""
                    suffix += (
                        f" — đã click {click_kind}{retry_note} ({x}, {y}) sau "
                        f"{pipeline_ms} ms, đang chờ xác nhận"
                    )
                except (OSError, RuntimeError, ValueError) as exc:
                    suffix += f" — CLICK LỖI: {exc}; không tự thử lại ở bàn này"
            else:
                suffix += (
                    " — DRY RUN: AUTO LOOP OFF nên không click nước cờ"
                    if not self.auto_click_enabled.is_set()
                    else " — đã khóa click cho bàn này"
                )
        if (
            self.pending_signature
            and signature == self.pending_signature
            and time.monotonic() - self.pending_since > 4.0
            and not self.pending_warning_emitted
        ):
            suffix += " — chưa thấy bàn đổi sau 4 giây; không tự click lại"
            self.pending_warning_emitted = True
        if analysis.state in (
            ScreenState.LOBBY,
            ScreenState.MATCHING,
            ScreenState.OPPONENT_LEFT,
            ScreenState.RESULT,
        ):
            self.last_clicked_signature = None
            self.pending_signature = None
            self.expected_after_click_signature = None
            self.confirmed_own_signature = None
            self.pending_early_click = False
            self.early_retry_signatures.clear()
            self.pending_warning_emitted = False
        state_stable = not require_stability or (
            len(self.recent_states) == self.recent_states.maxlen
            and len(set(self.recent_states)) == 1
        )
        if self.last_screen_action is not None and analysis.state != self.last_screen_action:
            self.last_screen_action = None
        if (
            live
            and self.auto_click_enabled.is_set()
            and state_stable
            and analysis.state in (ScreenState.LOBBY, ScreenState.OPPONENT_LEFT, ScreenState.RESULT)
            and self.last_screen_action != analysis.state
        ):
            if analysis.state == ScreenState.LOBBY:
                target = locate_lobby_primary_button(image)
                label = "Trận sơ cấp"
            elif analysis.state == ScreenState.OPPONENT_LEFT:
                target = locate_opponent_left_exit_button(image)
                label = "Thoát"
            else:
                target = locate_replay_button(image, analysis.grid) if analysis.grid else None
                label = "Chơi lại"
            if target is None:
                suffix += " — không xác định chắc nút; không click"
            else:
                self.last_screen_action = analysis.state
                try:
                    self.client.tap(*target)
                    suffix += f" — đã click {label} {target}; chờ chuyển màn hình"
                except (OSError, RuntimeError, ValueError) as exc:
                    suffix += f" — CLICK LỖI: {exc}; không tự thử lại ở màn này"
        elif live and not self.auto_click_enabled.is_set() and state_stable and analysis.state in (
            ScreenState.LOBBY,
            ScreenState.OPPONENT_LEFT,
            ScreenState.RESULT,
        ):
            label = {
                ScreenState.LOBBY: "Trận sơ cấp",
                ScreenState.OPPONENT_LEFT: "Thoát",
                ScreenState.RESULT: "Chơi lại",
            }[analysis.state]
            suffix += f" — AUTO LOOP OFF nên không bấm {label}"
        return MonitorUpdate(rendered, analysis, search, analysis.message + suffix + verification)

    def _poll_events(self) -> None:
        try:
            while True:
                update = self.events.get_nowait()
                self._render(update)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _render(self, update: MonitorUpdate) -> None:
        self._log(update.text)
        with self.mode_lock:
            active_mode = self.active_play_mode
            requested_mode = self.requested_play_mode
        active_label = "Mode 2 — phản công nhanh" if active_mode == "fast" else "Mode 1 — ổn định"
        if active_mode != requested_mode:
            pending_label = "Mode 2" if requested_mode == "fast" else "Mode 1"
            active_label += f" ({pending_label} từ ván sau)"
        self.play_mode_status_var.set(active_label)
        if update.analysis:
            analysis = update.analysis
            self.state_var.set(analysis.state.value)
            self.detail_var.set(update.text)
            lines = [
                f"State: {analysis.state.value}",
                "Màu bạn: " + ("ĐEN" if analysis.detected_player == Disc.BLACK else "TRẮNG" if analysis.detected_player == Disc.WHITE else "chưa xác định"),
                f"Grid strength: {analysis.grid.strength:.2f}" if analysis.grid else "Grid: không có",
                f"Ô vàng: {moves_text(analysis.highlighted_moves)}",
                f"Nước hợp lệ: {moves_text(analysis.legal_moves)}",
                f"Validated: {'YES' if analysis.board_validated else 'NO'}",
            ]
            if update.search and update.search.move:
                lines.extend(
                    [
                        f"Đề xuất: {update.search.move.algebraic}",
                        f"Engine: {update.search.engine}",
                        f"Time: {update.search.elapsed_ms} ms",
                    ]
                )
                if update.search.completed_depth:
                    lines.extend(
                        [
                            f"Depth: {update.search.completed_depth}",
                            f"Nodes: {update.search.nodes}",
                        ]
                    )
            if analysis.board:
                lines.extend(["", format_board(analysis.board, analysis.legal_moves)])
            self._set_text(self.board_text, "\n".join(lines))
        else:
            self.detail_var.set(update.text)
        if update.image:
            preview = update.image.copy()
            preview.thumbnail((500, 500), Image.Resampling.LANCZOS)
            self.photo = ImageTk.PhotoImage(preview)
            self.preview.configure(image=self.photo)

    def _log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{timestamp}  {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    @staticmethod
    def _set_text(widget: tk.Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def _close(self) -> None:
        self.stop_event.set()
        self.ponder_executor.shutdown(wait=False, cancel_futures=True)
        for ponder_engine in getattr(self, "ponder_engines", []):
            ponder_engine.close()
        move_engine = getattr(self, "move_engine", None)
        if move_engine is not None:
            move_engine.close()
        self.root.destroy()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WePlay Reversi live monitor with guarded ADB tap")
    parser.add_argument("--ld-dir", type=Path, default=None)
    parser.add_argument("--index", type=int, default=None)
    parser.add_argument("--fixtures", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_root = Path(__file__).resolve().parents[2]
    fixture_dir = args.fixtures or project_root / "tests" / "fixtures" / "weplay"
    root = tk.Tk()
    ReversiMonitor(root, fixture_dir, args.ld_dir, args.index)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
