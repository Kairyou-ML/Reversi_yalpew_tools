from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import monotonic

from .edax import EdaxConsole
from .engine import SearchResult, apply_move, choose_move, legal_moves
from .model import Board, Disc, Move

MOVE_RE = re.compile(r"(?i)(?<![a-z0-9])([a-h][1-8]|pass)(?![a-z0-9])")


def board_to_egaroucid(board: Board, player: Disc) -> str:
    """Return Egaroucid's 64-cell row-major board plus side to move."""
    symbols = {Disc.EMPTY: "-", Disc.BLACK: "B", Disc.WHITE: "W"}
    cells = "".join(symbols[cell] for row in board for cell in row)
    side = "B" if player == Disc.BLACK else "W"
    return f"{cells} {side}"


def parse_move(text: str) -> Move | None:
    matches = MOVE_RE.findall(text)
    if not matches:
        raise RuntimeError(f"Egaroucid did not return a move: {text!r}")
    token = matches[-1].lower()
    if token == "pass":
        return None
    return Move(int(token[1]) - 1, ord(token[0]) - ord("a"))


class EgaroucidConsole:
    """Small, persistent adapter around the official Egaroucid console binary."""

    def __init__(self, executable: Path, level: int = 5, threads: int = 2, timeout_seconds: float = 3.0):
        self.executable = Path(executable).resolve()
        self.level = level
        self.threads = threads
        self.timeout_seconds = timeout_seconds
        self._process: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.Lock()
        self._has_completed_request = False

    @property
    def label(self) -> str:
        return f"Egaroucid 7.8.1 L{self.level}"

    def _start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        if not self.executable.is_file():
            raise FileNotFoundError(f"Không tìm thấy Egaroucid: {self.executable}")
        self._lines = queue.Queue()
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self._process = subprocess.Popen(
            [str(self.executable), "-l", str(self.level), "-t", str(self.threads), "-q", "-noboard"],
            cwd=self.executable.parent,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
        process = self._process
        output_lines = self._lines

        def read_output() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                output_lines.put(line)
            output_lines.put(None)

        threading.Thread(target=read_output, name="egaroucid-output", daemon=True).start()

    def choose_move(
        self,
        board: Board,
        player: Disc,
        *,
        level: int | None = None,
        timeout_seconds: float | None = None,
    ) -> SearchResult:
        legal = legal_moves(board, player)
        actual_level = self.level if level is None else level
        if not legal:
            return SearchResult(None, 0.0, 0, 0, 0, f"Egaroucid 7.8.1 L{actual_level}")
        started = monotonic()
        with self._lock:
            self._start()
            assert self._process is not None and self._process.stdin is not None
            try:
                self._process.stdin.write(
                    f"level {actual_level}\nsetboard {board_to_egaroucid(board, player)}\ngo\n"
                )
                self._process.stdin.flush()
                requested_timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
                # The first request may still be loading the 70+ MB book/eval data.
                # Later requests use the phase-specific budget.
                effective_timeout = max(requested_timeout, self.timeout_seconds) if not self._has_completed_request else requested_timeout
                deadline = monotonic() + effective_timeout
                collected: list[str] = []
                move: Move | None = None
                while monotonic() < deadline:
                    remaining = max(0.01, deadline - monotonic())
                    try:
                        line = self._lines.get(timeout=remaining)
                    except queue.Empty:
                        break
                    if line is None:
                        raise RuntimeError("Egaroucid exited unexpectedly")
                    collected.append(line)
                    try:
                        move = parse_move(line)
                        break
                    except RuntimeError:
                        continue
                else:
                    move = None
                if move is None:
                    raise TimeoutError("Egaroucid không trả nước đi đúng hạn")
                if move not in legal:
                    raise RuntimeError(
                        f"Egaroucid trả nước không hợp lệ {move.algebraic}; output={''.join(collected)!r}"
                    )
                self._has_completed_request = True
                return SearchResult(
                    move=move,
                    score=0.0,
                    completed_depth=0,
                    nodes=0,
                    elapsed_ms=int((monotonic() - started) * 1000),
                    engine=f"Egaroucid 7.8.1 L{actual_level}",
                )
            except Exception:
                self._stop_process()
                raise

    def prewarm(self) -> None:
        """Start resource loading early so the first timed turn is not penalized."""
        with self._lock:
            self._start()

    def _stop_process(self) -> None:
        process, self._process = self._process, None
        self._has_completed_request = False
        if process is None:
            return
        try:
            if process.poll() is None and process.stdin is not None:
                process.stdin.write("quit\n")
                process.stdin.flush()
                process.wait(timeout=0.5)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
        finally:
            try:
                if process.stdin is not None:
                    process.stdin.close()
            except OSError:
                pass
            try:
                if process.stdout is not None:
                    process.stdout.close()
            except OSError:
                pass

    def close(self) -> None:
        with self._lock:
            self._stop_process()


class HybridMoveEngine:
    """Adaptive Egaroucid/Edax search with a guarded mobility-squeeze style."""

    def __init__(
        self,
        egaroucid: EgaroucidConsole,
        edax: EdaxConsole | None = None,
        fallback_time_ms: int = 900,
    ):
        self.egaroucid = egaroucid
        self.edax = edax
        self.fallback_time_ms = fallback_time_ms

    @staticmethod
    def _opponent_mobility(board: Board, player: Disc, move: Move) -> tuple[int, bool]:
        child = apply_move(board, player, move)
        replies = legal_moves(child, player.opponent)
        corners = {Move(0, 0), Move(0, 7), Move(7, 0), Move(7, 7)}
        return len(replies), any(reply in corners for reply in replies)

    def _pick_verified_squeeze(
        self,
        board: Board,
        player: Disc,
        egaroucid_result: SearchResult,
        edax_result: SearchResult,
        elapsed_ms: int,
    ) -> SearchResult:
        if egaroucid_result.move == edax_result.move:
            return SearchResult(
                edax_result.move,
                edax_result.score,
                edax_result.completed_depth,
                0,
                elapsed_ms,
                "Hybrid dong thuan Egaroucid + Edax",
            )
        assert egaroucid_result.move is not None and edax_result.move is not None
        corners = {Move(0, 0), Move(0, 7), Move(7, 0), Move(7, 7)}
        e_move, d_move = egaroucid_result.move, edax_result.move
        if e_move in corners and d_move not in corners:
            chosen, mobility = egaroucid_result, self._opponent_mobility(board, player, e_move)[0]
            reason = "lay goc"
        elif d_move in corners and e_move not in corners:
            chosen, mobility = edax_result, self._opponent_mobility(board, player, d_move)[0]
            reason = "lay goc"
        else:
            e_mobility, e_concedes_corner = self._opponent_mobility(board, player, e_move)
            d_mobility, d_concedes_corner = self._opponent_mobility(board, player, d_move)
            # Mobility only overrides Edax when another strong engine proposed the
            # move, it removes at least two replies, and it does not gift a corner.
            if e_mobility + 2 <= d_mobility and not e_concedes_corner:
                chosen, mobility, reason = egaroucid_result, e_mobility, "ep mobility da kiem chung"
            else:
                chosen, mobility, reason = edax_result, d_mobility, "Edax verifier"
        return SearchResult(
            chosen.move,
            chosen.score,
            chosen.completed_depth,
            chosen.nodes,
            elapsed_ms,
            f"Hybrid {reason}; doi thu con {mobility} nuoc",
        )

    @staticmethod
    def _run_pair(egaroucid_call, edax_call) -> tuple[SearchResult | None, SearchResult | None]:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="reversi-engine") as pool:
            e_future = pool.submit(egaroucid_call)
            d_future = pool.submit(edax_call)
            try:
                e_result = e_future.result()
            except (OSError, RuntimeError, TimeoutError, ValueError):
                e_result = None
            try:
                d_result = d_future.result()
            except (OSError, RuntimeError, TimeoutError, ValueError):
                d_result = None
        return e_result, d_result

    def choose_move(self, board: Board, player: Disc) -> SearchResult:
        started = monotonic()
        legal = legal_moves(board, player)
        if not legal:
            return SearchResult(None, 0.0, 0, 0, 0, "Pass (khong co nuoc hop le)")
        if len(legal) == 1:
            return SearchResult(legal[0], 0.0, 0, 0, 0, "Nước bắt buộc (1 nước hợp lệ)")
        occupied = sum(cell != Disc.EMPTY for row in board for cell in row)
        empties = 64 - occupied
        try:
            # Opening book: spend a little more than the old L2 policy, without
            # manufacturing an artificial delay when the book knows the move.
            if occupied <= 16:
                try:
                    result = self.egaroucid.choose_move(board, player, level=6, timeout_seconds=3.0)
                    return SearchResult(
                        result.move, result.score, result.completed_depth, result.nodes,
                        int((monotonic() - started) * 1000), "Opening book Egaroucid L6",
                    )
                except (OSError, RuntimeError, TimeoutError, ValueError) as opening_exc:
                    if self.edax is None:
                        raise
                    try:
                        result = self.edax.choose_move(board, player, level=16, timeout_seconds=2.5)
                    except (OSError, RuntimeError, TimeoutError, ValueError) as edax_exc:
                        raise RuntimeError(
                            f"Opening engines failed: Egaroucid={opening_exc}; Edax={edax_exc}"
                        ) from edax_exc
                    return SearchResult(
                        result.move, result.score, result.completed_depth, result.nodes,
                        int((monotonic() - started) * 1000),
                        f"Opening fallback Edax L16; Egaroucid loi: {type(opening_exc).__name__}",
                    )

            # Under 18 empties: rush the exact-capable search. Smaller endings
            # receive less wall time because they are substantially cheaper.
            if empties < 18:
                rush_seconds = 4.5 if empties >= 15 else 3.0 if empties >= 11 else 1.5
                if self.edax is None:
                    result = self.egaroucid.choose_move(board, player, level=9, timeout_seconds=rush_seconds)
                else:
                    e_result, d_result = self._run_pair(
                        lambda: self.egaroucid.choose_move(
                            board, player, level=9, timeout_seconds=rush_seconds
                        ),
                        lambda: self.edax.choose_move(
                            board, player, level=60, timeout_seconds=rush_seconds
                        ),
                    )
                    result = d_result or e_result
                    if result is None:
                        raise TimeoutError("Both endgame engines exceeded the rush deadline")
                    if e_result and d_result and e_result.move == d_result.move:
                        result = SearchResult(
                            result.move, result.score, result.completed_depth, result.nodes,
                            result.elapsed_ms, "Endgame rush: 2 engine dong thuan",
                        )
                return SearchResult(
                    result.move, result.score, result.completed_depth, result.nodes,
                    int((monotonic() - started) * 1000),
                    result.engine if result.engine.startswith("Endgame") else f"Endgame rush <18 ({result.engine})",
                )

            # Fast probe first. Easy positions are clicked as soon as two
            # independent engines agree; difficult positions may use 13.2 s.
            if self.edax is not None:
                fast_e, fast_d = self._run_pair(
                    lambda: self.egaroucid.choose_move(board, player, level=4, timeout_seconds=1.2),
                    lambda: self.edax.choose_move(board, player, level=16, timeout_seconds=1.2),
                )
                if fast_e and fast_d and fast_e.move == fast_d.move:
                    return SearchResult(
                        fast_d.move, fast_d.score, fast_d.completed_depth, 0,
                        int((monotonic() - started) * 1000),
                        "The de: Egaroucid L4 = Edax L16",
                    )
                deep_e, deep_d = self._run_pair(
                    lambda: self.egaroucid.choose_move(board, player, level=12, timeout_seconds=12.0),
                    lambda: self.edax.choose_move(board, player, level=28, timeout_seconds=12.0),
                )
                if deep_e and deep_d:
                    return self._pick_verified_squeeze(
                        board, player, deep_e, deep_d, int((monotonic() - started) * 1000)
                    )
                surviving = deep_d or deep_e or fast_d or fast_e
                if surviving:
                    return SearchResult(
                        surviving.move, surviving.score, surviving.completed_depth, surviving.nodes,
                        int((monotonic() - started) * 1000),
                        f"Hybrid 1 engine con song ({surviving.engine})",
                    )
                raise TimeoutError("Both engines failed")

            # Compatibility when Edax is not bundled.
            return self.egaroucid.choose_move(board, player, level=9, timeout_seconds=12.0)
        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
            fallback = choose_move(board, player, max_depth=5, time_limit_ms=self.fallback_time_ms)
            detail = " ".join(str(exc).split())[:120]
            error_label = f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
            return SearchResult(
                fallback.move,
                fallback.score,
                fallback.completed_depth,
                fallback.nodes,
                int((monotonic() - started) * 1000),
                f"Python fallback ({error_label})",
            )

    def choose_move_fast(self, board: Board, player: Disc, budget_seconds: float = 0.7) -> SearchResult:
        """Bounded opening reply search used only while it is the opponent's turn.

        It uses the same bundled engines and opening book as the normal policy,
        but never consumes the long midgame budget.  A legal Python result is
        always available if an external engine misses its slice.
        """
        started = monotonic()
        legal = legal_moves(board, player)
        if not legal:
            return SearchResult(None, 0.0, 0, 0, 0, "Fast table: pass")
        if len(legal) == 1:
            return SearchResult(legal[0], 0.0, 0, 0, 0, "Fast table: nước bắt buộc")
        budget_seconds = max(0.12, min(1.2, budget_seconds))
        # Edax's warmed evaluator is consistently faster than asking
        # Egaroucid to leave its opening book for every speculative branch.
        if self.edax is not None:
            try:
                result = self.edax.choose_move(
                    board, player, level=16,
                    timeout_seconds=max(0.10, budget_seconds * 0.82),
                )
                return SearchResult(
                    result.move, result.score, result.completed_depth, result.nodes,
                    int((monotonic() - started) * 1000), "Fast table Edax L16",
                )
            except (OSError, RuntimeError, TimeoutError, ValueError):
                pass
        remaining = budget_seconds - (monotonic() - started)
        if remaining >= 0.10:
            try:
                result = self.egaroucid.choose_move(
                    board, player, level=6, timeout_seconds=remaining
                )
                return SearchResult(
                    result.move, result.score, result.completed_depth, result.nodes,
                    int((monotonic() - started) * 1000), "Fast table Egaroucid L6",
                )
            except (OSError, RuntimeError, TimeoutError, ValueError):
                pass
        fallback_ms = max(40, min(180, int(budget_seconds * 250)))
        result = choose_move(board, player, max_depth=4, time_limit_ms=fallback_ms)
        return SearchResult(
            result.move, result.score, result.completed_depth, result.nodes,
            int((monotonic() - started) * 1000), "Fast table Python fallback",
        )

    def close(self) -> None:
        self.egaroucid.close()
        if self.edax is not None:
            self.edax.close()

    def prewarm(self) -> None:
        try:
            self.egaroucid.prewarm()
            if self.edax is not None:
                self.edax.prewarm()
        except (OSError, RuntimeError):
            # Missing/broken optional engine is handled by choose_move fallback.
            pass
