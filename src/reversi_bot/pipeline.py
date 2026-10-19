from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .engine import SearchResult, choose_move, legal_moves
from .model import Board, Disc
from .vision import RecognitionResult, VisionConfig, cell_center, load_image, recognize_board


@dataclass(frozen=True)
class PipelineResult:
    board: Board
    recognition: RecognitionResult
    search: SearchResult
    target_pixel: tuple[int, int] | None


def analyze_screenshot(
    image_path: str | Path,
    config: VisionConfig,
    player: Disc,
    depth: int = 5,
    time_limit_ms: int = 1500,
) -> PipelineResult:
    image = load_image(image_path)
    recognition = recognize_board(image, config)
    search = choose_move(recognition.board, player, depth, time_limit_ms)
    target = cell_center(config, search.move.row, search.move.col) if search.move else None
    return PipelineResult(recognition.board, recognition, search, target)


def legal_move_count(board: Board, player: Disc) -> int:
    return len(legal_moves(board, player))

