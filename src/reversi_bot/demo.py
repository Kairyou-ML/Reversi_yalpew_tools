from __future__ import annotations

import argparse
from pathlib import Path

from .model import Board, Disc, initial_board

COLORS = {
    "background": (34, 39, 43),
    "board": (45, 145, 82),
    "grid": (27, 91, 52),
    "black": (25, 28, 31),
    "white": (235, 235, 228),
}


def render_ppm(path: Path, board: Board, size: int = 800, margin: int = 80) -> None:
    pixels = [COLORS["background"]] * (size * size)

    def set_pixel(x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < size and 0 <= y < size:
            pixels[y * size + x] = color

    board_size = size - 2 * margin
    cell = board_size // 8
    for y in range(margin, margin + board_size):
        for x in range(margin, margin + board_size):
            set_pixel(x, y, COLORS["board"])
    for index in range(9):
        line = margin + index * cell
        for offset in range(board_size + 1):
            set_pixel(line, margin + offset, COLORS["grid"])
            set_pixel(margin + offset, line, COLORS["grid"])
    radius = int(cell * 0.36)
    for row in range(8):
        for col in range(8):
            disc = board[row][col]
            if disc == Disc.EMPTY:
                continue
            color = COLORS["black"] if disc == Disc.BLACK else COLORS["white"]
            center_x = margin + col * cell + cell // 2
            center_y = margin + row * cell + cell // 2
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if dx * dx + dy * dy <= radius * radius:
                        set_pixel(center_x + dx, center_y + dy, color)
    raw = bytearray()
    for pixel in pixels:
        raw.extend(pixel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"P6\n{size} {size}\n255\n".encode("ascii") + raw)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a deterministic MVP screenshot fixture.")
    parser.add_argument("output", nargs="?", type=Path, default=Path("examples/initial_board.ppm"))
    args = parser.parse_args()
    render_ppm(args.output, initial_board())
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

