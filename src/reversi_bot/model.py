from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable


class Disc(IntEnum):
    EMPTY = 0
    BLACK = 1
    WHITE = -1

    @property
    def opponent(self) -> "Disc":
        if self == Disc.EMPTY:
            raise ValueError("EMPTY has no opponent")
        return Disc(-int(self))


@dataclass(frozen=True, order=True)
class Move:
    row: int
    col: int

    @property
    def algebraic(self) -> str:
        return f"{chr(ord('a') + self.col)}{self.row + 1}"


Board = tuple[tuple[Disc, ...], ...]


def make_board(rows: Iterable[Iterable[int | Disc]]) -> Board:
    board = tuple(tuple(Disc(cell) for cell in row) for row in rows)
    if len(board) != 8 or any(len(row) != 8 for row in board):
        raise ValueError("A Reversi board must be exactly 8x8")
    return board


def initial_board() -> Board:
    rows = [[Disc.EMPTY for _ in range(8)] for _ in range(8)]
    rows[3][3], rows[4][4] = Disc.WHITE, Disc.WHITE
    rows[3][4], rows[4][3] = Disc.BLACK, Disc.BLACK
    return make_board(rows)


def format_board(board: Board, legal: Iterable[Move] = ()) -> str:
    markers = {(move.row, move.col) for move in legal}
    symbols = {Disc.EMPTY: ".", Disc.BLACK: "B", Disc.WHITE: "W"}
    lines = ["    a b c d e f g h"]
    for row_index, row in enumerate(board):
        cells = ["*" if (row_index, col) in markers else symbols[cell] for col, cell in enumerate(row)]
        lines.append(f"{row_index + 1:>2}  " + " ".join(cells))
    return "\n".join(lines)

