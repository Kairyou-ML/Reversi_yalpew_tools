from __future__ import annotations

from dataclasses import dataclass
from math import inf
from time import monotonic

from .model import Board, Disc, Move, make_board

DIRECTIONS = tuple(
    (dr, dc)
    for dr in (-1, 0, 1)
    for dc in (-1, 0, 1)
    if (dr, dc) != (0, 0)
)

POSITION_WEIGHTS = (
    (120, -25, 20, 5, 5, 20, -25, 120),
    (-25, -45, -5, -5, -5, -5, -45, -25),
    (20, -5, 15, 3, 3, 15, -5, 20),
    (5, -5, 3, 3, 3, 3, -5, 5),
    (5, -5, 3, 3, 3, 3, -5, 5),
    (20, -5, 15, 3, 3, 15, -5, 20),
    (-25, -45, -5, -5, -5, -5, -45, -25),
    (120, -25, 20, 5, 5, 20, -25, 120),
)


class SearchTimeout(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchResult:
    move: Move | None
    score: float
    completed_depth: int
    nodes: int
    elapsed_ms: int
    engine: str = "Python MVP"


def _captures(board: Board, player: Disc, row: int, col: int) -> list[tuple[int, int]]:
    if board[row][col] != Disc.EMPTY:
        return []
    captured: list[tuple[int, int]] = []
    for dr, dc in DIRECTIONS:
        r, c = row + dr, col + dc
        line: list[tuple[int, int]] = []
        while 0 <= r < 8 and 0 <= c < 8 and board[r][c] == player.opponent:
            line.append((r, c))
            r, c = r + dr, c + dc
        if line and 0 <= r < 8 and 0 <= c < 8 and board[r][c] == player:
            captured.extend(line)
    return captured


def legal_moves(board: Board, player: Disc) -> list[Move]:
    return [
        Move(row, col)
        for row in range(8)
        for col in range(8)
        if _captures(board, player, row, col)
    ]


def apply_move(board: Board, player: Disc, move: Move) -> Board:
    captured = _captures(board, player, move.row, move.col)
    if not captured:
        raise ValueError(f"Illegal move: {move.algebraic}")
    rows = [list(row) for row in board]
    rows[move.row][move.col] = player
    for row, col in captured:
        rows[row][col] = player
    return make_board(rows)


def game_over(board: Board) -> bool:
    return not legal_moves(board, Disc.BLACK) and not legal_moves(board, Disc.WHITE)


def evaluate(board: Board, player: Disc) -> float:
    opponent = player.opponent
    positional = sum(
        POSITION_WEIGHTS[row][col] * int(board[row][col]) * int(player)
        for row in range(8)
        for col in range(8)
    )
    mobility = len(legal_moves(board, player)) - len(legal_moves(board, opponent))
    player_count = sum(cell == player for row in board for cell in row)
    opponent_count = sum(cell == opponent for row in board for cell in row)
    empties = 64 - player_count - opponent_count
    if game_over(board):
        margin = player_count - opponent_count
        return 100_000.0 + margin if margin > 0 else -100_000.0 + margin if margin < 0 else 0.0
    parity_weight = 1 if empties > 16 else 4
    return positional + 8 * mobility + parity_weight * (player_count - opponent_count)


def choose_move(board: Board, player: Disc, max_depth: int = 5, time_limit_ms: int = 1500) -> SearchResult:
    if max_depth < 1:
        raise ValueError("max_depth must be at least 1")
    if time_limit_ms < 1:
        raise ValueError("time_limit_ms must be at least 1")
    start = monotonic()
    deadline = start + time_limit_ms / 1000
    moves = legal_moves(board, player)
    if not moves:
        return SearchResult(None, evaluate(board, player), 0, 0, int((monotonic() - start) * 1000))

    nodes = 0
    best_move = moves[0]
    best_score = evaluate(apply_move(board, player, best_move), player)
    completed_depth = 0

    def negamax(position: Board, side: Disc, root: Disc, depth: int, alpha: float, beta: float, passed: bool) -> float:
        nonlocal nodes
        nodes += 1
        if monotonic() >= deadline:
            raise SearchTimeout
        available = legal_moves(position, side)
        if depth == 0 or (passed and not available):
            return evaluate(position, root) * (1 if side == root else -1)
        if not available:
            return -negamax(position, side.opponent, root, depth, -beta, -alpha, True)
        value = -inf
        ordered = sorted(available, key=lambda m: POSITION_WEIGHTS[m.row][m.col], reverse=True)
        for candidate in ordered:
            child = apply_move(position, side, candidate)
            value = max(value, -negamax(child, side.opponent, root, depth - 1, -beta, -alpha, False))
            alpha = max(alpha, value)
            if alpha >= beta:
                break
        return value

    for depth in range(1, max_depth + 1):
        iteration_best = best_move
        iteration_score = -inf
        alpha = -inf
        try:
            ordered = sorted(moves, key=lambda m: (m != best_move, -POSITION_WEIGHTS[m.row][m.col]))
            for candidate in ordered:
                child = apply_move(board, player, candidate)
                score = -negamax(child, player.opponent, player, depth - 1, -inf, -alpha, False)
                if score > iteration_score:
                    iteration_score, iteration_best = score, candidate
                alpha = max(alpha, score)
        except SearchTimeout:
            break
        best_move, best_score, completed_depth = iteration_best, iteration_score, depth

    return SearchResult(best_move, best_score, completed_depth, nodes, int((monotonic() - start) * 1000))
