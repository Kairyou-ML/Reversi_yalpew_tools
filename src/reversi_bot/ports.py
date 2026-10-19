from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .model import Move


class StateSource(Protocol):
    def capture(self) -> Path: ...


class ActionExecutor(Protocol):
    def play(self, move: Move, pixel: tuple[int, int]) -> None: ...


class DisabledExecutor:
    """Safe MVP executor: records intent but never controls another application."""

    def play(self, move: Move, pixel: tuple[int, int]) -> None:
        raise RuntimeError(
            f"Real input is disabled. Intended move={move.algebraic}, pixel={pixel}. "
            "Implement and explicitly enable an ActionExecutor only after offline validation."
        )

