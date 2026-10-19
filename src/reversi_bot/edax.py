from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
from pathlib import Path
from time import monotonic

from .engine import SearchResult, legal_moves
from .model import Board, Disc, Move

PLAY_RE = re.compile(r"(?i)Edax plays\s+([a-h][1-8]|pass)")
SCORE_RE = re.compile(r"^\s*\d+\s+([+-]\d+)\s+.*?\s([a-h][1-8]|pass)(?:\s|$)", re.IGNORECASE)


def board_to_edax(board: Board, player: Disc) -> str:
    """Return Edax's 64-cell row-major board plus side to move."""
    symbols = {Disc.EMPTY: "-", Disc.BLACK: "X", Disc.WHITE: "O"}
    cells = "".join(symbols[cell] for row in board for cell in row)
    side = "X" if player == Disc.BLACK else "O"
    return f"{cells} {side}"


def _token_to_move(token: str) -> Move | None:
    token = token.lower()
    if token == "pass":
        return None
    return Move(int(token[1]) - 1, ord(token[0]) - ord("a"))


class EdaxConsole:
    """Persistent adapter for the official Edax 4.6 console engine."""

    def __init__(
        self,
        executable: Path,
        eval_file: Path,
        level: int = 21,
        threads: int = 2,
        timeout_seconds: float = 5.0,
    ):
        self.executable = Path(executable).resolve()
        self.eval_file = Path(eval_file).resolve()
        self.level = level
        self.threads = threads
        self.timeout_seconds = timeout_seconds
        self._process: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.Lock()

    def _start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        if not self.executable.is_file():
            raise FileNotFoundError(f"Khong tim thay Edax: {self.executable}")
        if not self.eval_file.is_file():
            raise FileNotFoundError(f"Khong tim thay Edax eval.dat: {self.eval_file}")
        self._lines = queue.Queue()
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self._process = subprocess.Popen(
            [
                str(self.executable),
                "-edax",
                "-n",
                str(self.threads),
                "-l",
                str(self.level),
                "-eval-file",
                str(self.eval_file),
                "-book-usage",
                "off",
                "-auto-start",
                "off",
                "-auto-store",
                "off",
            ],
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

        threading.Thread(target=read_output, name="edax-output", daemon=True).start()

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
            return SearchResult(None, 0.0, 0, 0, 0, f"Edax 4.6 L{actual_level}")
        requested_timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        started = monotonic()
        with self._lock:
            self._start()
            assert self._process is not None and self._process.stdin is not None
            try:
                # Edax accepts whole seconds for move-time. The Python deadline is
                # the real hard stop and retains time for CV plus the ADB tap.
                move_seconds = max(1, int(requested_timeout))
                self._process.stdin.write(
                    f"level {actual_level}\nmove-time {move_seconds}\n"
                    f"setboard {board_to_edax(board, player)}\ngo\n"
                )
                self._process.stdin.flush()
                deadline = monotonic() + requested_timeout
                score = 0.0
                move: Move | None = None
                collected: list[str] = []
                while monotonic() < deadline:
                    try:
                        line = self._lines.get(timeout=max(0.01, deadline - monotonic()))
                    except queue.Empty:
                        break
                    if line is None:
                        raise RuntimeError("Edax exited unexpectedly")
                    collected.append(line)
                    score_match = SCORE_RE.match(line)
                    if score_match:
                        score = float(score_match.group(1))
                    play_match = PLAY_RE.search(line)
                    if play_match:
                        move = _token_to_move(play_match.group(1))
                        break
                if move is None:
                    raise TimeoutError("Edax did not return a move before the deadline")
                if move not in legal:
                    raise RuntimeError(f"Edax returned illegal move {move}; output={''.join(collected)!r}")
                return SearchResult(
                    move,
                    score,
                    actual_level,
                    0,
                    int((monotonic() - started) * 1000),
                    f"Edax 4.6 L{actual_level}",
                )
            except Exception:
                self._stop_process()
                raise

    def prewarm(self) -> None:
        with self._lock:
            self._start()

    def _stop_process(self) -> None:
        process, self._process = self._process, None
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
