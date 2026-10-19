from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from time import sleep
from typing import Callable

from .model import Disc
from .pipeline import PipelineResult, analyze_screenshot
from .vision import VisionConfig


class BotState(Enum):
    IDLE = auto()
    ACQUIRING = auto()
    RECOGNIZING = auto()
    THINKING = auto()
    READY = auto()
    ERROR = auto()
    STOPPED = auto()


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    delay_ms: int = 250


class OfflineController:
    """State-machine seam used now for files and later for live capture/action adapters."""

    def __init__(self, config: VisionConfig, logger: logging.Logger | None = None):
        self.config = config
        self.state = BotState.IDLE
        self.log = logger or logging.getLogger(__name__)

    def analyze_once(
        self,
        source: Callable[[], Path],
        player: Disc,
        retry: RetryPolicy = RetryPolicy(),
        depth: int = 5,
        time_limit_ms: int = 1500,
    ) -> PipelineResult:
        last_error: Exception | None = None
        for attempt in range(1, retry.attempts + 1):
            try:
                self._transition(BotState.ACQUIRING, attempt=attempt)
                image_path = source()
                self._transition(BotState.RECOGNIZING, image=str(image_path))
                self._transition(BotState.THINKING, player=player.name)
                result = analyze_screenshot(image_path, self.config, player, depth, time_limit_ms)
                self._transition(
                    BotState.READY,
                    move=result.search.move.algebraic if result.search.move else "pass",
                    confidence=round(result.recognition.min_confidence, 3),
                )
                return result
            except (OSError, RuntimeError, ValueError) as exc:
                last_error = exc
                self._transition(BotState.ERROR, attempt=attempt, error=str(exc))
                if attempt < retry.attempts:
                    sleep(retry.delay_ms / 1000)
        self.state = BotState.STOPPED
        raise RuntimeError(f"Offline analysis failed after {retry.attempts} attempts") from last_error

    def _transition(self, state: BotState, **context: object) -> None:
        previous = self.state
        self.state = state
        self.log.info("state_transition", extra={"from_state": previous.name, "to_state": state.name, **context})

