from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image, ImageDraw

from .engine import legal_moves
from .model import Board, Disc, Move, make_board


class ScreenState(str, Enum):
    LOBBY = "LOBBY"
    MATCHING = "MATCHING"
    LOADING = "LOADING"
    OPPONENT_LEFT = "OPPONENT_LEFT"
    PLAYING_MY_TURN = "PLAYING_MY_TURN"
    PLAYING_WAIT = "PLAYING_WAIT"
    RESULT = "RESULT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class GridDetection:
    left: int
    top: int
    spacing: int
    strength: float

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.left + 8 * self.spacing, self.top + 8 * self.spacing

    def center(self, move: Move) -> tuple[int, int]:
        return (
            round(self.left + (move.col + 0.5) * self.spacing),
            round(self.top + (move.row + 0.5) * self.spacing),
        )


@dataclass(frozen=True)
class FrameAnalysis:
    state: ScreenState
    grid: GridDetection | None
    board: Board | None
    detected_player: Disc | None
    highlighted_moves: tuple[Move, ...]
    legal_moves: tuple[Move, ...]
    board_validated: bool
    message: str


def _local_max(profile: np.ndarray, position: int, radius: int = 2) -> float:
    low = max(0, position - radius)
    high = min(len(profile), position + radius + 1)
    if low >= high:
        return 0.0
    return float(profile[low:high].max())


def locate_grid(image: Image.Image) -> GridDetection | None:
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    gray = rgb.mean(axis=2)
    height, width = gray.shape
    vertical = np.abs(np.diff(gray, axis=1))[int(height * 0.25):int(height * 0.83), :int(width * 0.92)].mean(axis=0)
    horizontal = np.abs(np.diff(gray, axis=0))[:, int(width * 0.04):int(width * 0.90)].mean(axis=1)
    vertical_baseline = max(0.1, float(np.median(vertical)))
    horizontal_baseline = max(0.1, float(np.median(horizontal)))
    best: tuple[float, int, int, int] | None = None

    spacing_min = max(28, int(width * 0.07))
    spacing_max = min(70, int(width * 0.115))
    for spacing in range(spacing_min, spacing_max + 1):
        x_candidates: list[tuple[float, int]] = []
        for left in range(int(width * 0.04), int(width * 0.22)):
            if left + 8 * spacing >= len(vertical):
                continue
            score = sum(_local_max(vertical, left + index * spacing) for index in range(9))
            x_candidates.append((score, left))
        y_candidates: list[tuple[float, int]] = []
        for top in range(int(height * 0.25), int(height * 0.46)):
            if top + 8 * spacing >= min(len(horizontal), height * 0.86):
                continue
            score = sum(_local_max(horizontal, top + index * spacing) for index in range(9))
            y_candidates.append((score, top))
        if not x_candidates or not y_candidates:
            continue
        x_score, left = max(x_candidates)
        y_score, top = max(y_candidates)
        normalized = (x_score + y_score) / (9 * (vertical_baseline + horizontal_baseline))
        if best is None or normalized > best[0]:
            best = normalized, left, top, spacing

    if best is None or best[0] < 8.0:
        return None
    strength, left, top, spacing = best
    return GridDetection(left, top, spacing, strength)


def _cell_patch(rgb: np.ndarray, grid: GridDetection, row: int, col: int, radius_ratio: float) -> np.ndarray:
    center_x = round(grid.left + (col + 0.5) * grid.spacing)
    center_y = round(grid.top + (row + 0.5) * grid.spacing)
    radius = max(3, int(grid.spacing * radius_ratio))
    return rgb[center_y - radius:center_y + radius + 1, center_x - radius:center_x + radius + 1]


def _highlight_score(rgb: np.ndarray, grid: GridDetection, row: int, col: int) -> float:
    left = round(grid.left + col * grid.spacing) + 3
    right = round(grid.left + (col + 1) * grid.spacing) - 3
    top = round(grid.top + row * grid.spacing) + 3
    bottom = round(grid.top + (row + 1) * grid.spacing) - 3
    patch = rgb[top:bottom, left:right].astype(np.int16)
    if patch.size == 0:
        return 0.0
    yellow = (
        (patch[:, :, 0] > 180)
        & (patch[:, :, 1] > 170)
        & (patch[:, :, 2] < 145)
        & (np.abs(patch[:, :, 0] - patch[:, :, 1]) < 80)
    )
    return float(yellow.mean())


class CellAppearance(IntEnum):
    EMPTY = 0
    CLASSIC_BLACK = 1
    CLASSIC_WHITE = 2
    SKIN = 3


def _cell_appearance(rgb: np.ndarray, grid: GridDetection, row: int, col: int) -> CellAppearance:
    """Separate occupancy/appearance from Reversi colour.

    WePlay only skins the remote player's discs.  A smooth classic black/white
    disc can therefore identify our side, while a textured/coloured circular
    object is mapped to the opponent.  Empty checkerboard cells have neither
    the centre texture nor the centre-to-ring contrast of a disc.
    """
    center = _cell_patch(rgb, grid, row, col, 0.17).astype(np.float32)
    wide = _cell_patch(rgb, grid, row, col, 0.39).astype(np.float32)
    if center.size == 0 or wide.size == 0:
        return CellAppearance.EMPTY
    color = np.median(center.reshape(-1, 3), axis=0)
    luminance = float(color.mean())
    texture = float(center.std())

    height, width = wide.shape[:2]
    yy, xx = np.ogrid[:height, :width]
    cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
    distance = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    radius = min(height, width) / 2.0
    inner = wide[distance <= radius * 0.55]
    ring = wide[(distance >= radius * 0.78) & (distance <= radius * 0.98)]
    radial_contrast = 0.0
    if inner.size and ring.size:
        radial_contrast = float(np.abs(np.mean(inner, axis=0) - np.mean(ring, axis=0)).mean())
    channel_spread = float(color.max() - color.min())

    # Classic pieces are glossy but their small centre patch is comparatively
    # smooth.  Textured blue/red gems and stamped coins deliberately fall
    # through to SKIN even when their average colour is dark or pale.
    if luminance < 100 and texture < 48:
        return CellAppearance.CLASSIC_BLACK
    if luminance > 180 and float(color[2]) - float(color[0]) > 8 and texture < 38:
        return CellAppearance.CLASSIC_WHITE
    if texture > 22 or radial_contrast > 12 or channel_spread > 55:
        return CellAppearance.SKIN
    return CellAppearance.EMPTY


def _board_from_appearances(
    appearances: tuple[tuple[CellAppearance, ...], ...],
    skin_side: Disc,
) -> Board:
    rows: list[list[Disc]] = []
    for row in appearances:
        rows.append([
            Disc.BLACK if cell == CellAppearance.CLASSIC_BLACK else
            Disc.WHITE if cell == CellAppearance.CLASSIC_WHITE else
            skin_side if cell == CellAppearance.SKIN else Disc.EMPTY
            for cell in row
        ])
    return make_board(rows)


def read_board_candidates(
    image: Image.Image,
    grid: GridDetection,
    player: Disc | None = None,
) -> tuple[tuple[Board, ...], tuple[Move, ...]]:
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    appearance_rows: list[list[CellAppearance]] = []
    highlights: list[Move] = []
    for row in range(8):
        appearance_row: list[CellAppearance] = []
        for col in range(8):
            highlighted = _highlight_score(rgb, grid, row, col) > 0.15
            appearance = CellAppearance.EMPTY if highlighted else _cell_appearance(rgb, grid, row, col)
            appearance_row.append(appearance)
            if highlighted:
                highlights.append(Move(row, col))
        appearance_rows.append(appearance_row)
    appearances = tuple(tuple(row) for row in appearance_rows)
    if player is not None:
        # The local player is always classic; every other occupied appearance
        # belongs to the opponent.  Classic-vs-classic remains unchanged.
        skin_side = player.opponent
        return (_board_from_appearances(appearances, skin_side),), tuple(highlights)
    black_skin = _board_from_appearances(appearances, Disc.BLACK)
    white_skin = _board_from_appearances(appearances, Disc.WHITE)
    candidates = (black_skin,) if black_skin == white_skin else (black_skin, white_skin)
    return candidates, tuple(highlights)


def read_board(
    image: Image.Image,
    grid: GridDetection,
    player: Disc | None = None,
) -> tuple[Board, tuple[Move, ...]]:
    candidates, highlights = read_board_candidates(image, grid, player)
    return candidates[0], highlights


def infer_player(board: Board, highlighted_moves: Iterable[Move]) -> Disc | None:
    highlighted = set(highlighted_moves)
    if not highlighted:
        return None
    candidates = [
        side
        for side in (Disc.BLACK, Disc.WHITE)
        if set(legal_moves(board, side)) == highlighted
    ]
    return candidates[0] if len(candidates) == 1 else None


def _thumbnail(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGB").resize((36, 64)), dtype=np.float32) / 255.0


def _is_loading_screen(image: Image.Image) -> bool:
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    gray = rgb.mean(axis=2)
    return float(gray.mean()) > 210.0 and float((gray > 220).mean()) > 0.75


def _button_center_from_color(
    image: Image.Image,
    mask: np.ndarray,
    top_ratio: float,
    bottom_ratio: float,
    minimum_row_ratio: float = 0.20,
) -> tuple[int, int] | None:
    height, width = mask.shape
    first = max(0, round(height * top_ratio))
    last = min(height, round(height * bottom_ratio))
    qualifying = np.flatnonzero(mask[first:last].sum(axis=1) >= width * minimum_row_ratio) + first
    if qualifying.size == 0:
        return None
    runs = np.split(qualifying, np.where(np.diff(qualifying) > 1)[0] + 1)
    run = max(runs, key=len)
    if len(run) < max(6, round(height * 0.01)):
        return None
    y1, y2 = int(run[0]), int(run[-1])
    ys, xs = np.nonzero(mask[y1:y2 + 1])
    if xs.size == 0:
        return None
    return round((int(xs.min()) + int(xs.max())) / 2), round((y1 + y2) / 2)


def locate_lobby_primary_button(image: Image.Image) -> tuple[int, int] | None:
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    green_button = (green > 65) & (green > red * 1.10) & (green > blue * 1.12)
    return _button_center_from_color(image, green_button, 0.34, 0.61, 0.25)


def locate_opponent_left_exit_button(image: Image.Image) -> tuple[int, int] | None:
    """Locate the wide orange Exit button in the post-matchmaking abort modal."""
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    height, width = rgb.shape[:2]
    red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    orange_button = (
        (red > 125)
        & (green > 40)
        & (red > green * 1.30)
        & (green > blue * 1.15)
    )
    target = _button_center_from_color(image, orange_button, 0.50, 0.64, 0.30)
    if target is None:
        return None

    # The modal covers the middle of the board with a dark red-brown panel.
    # Requiring both its geometry and color avoids confusing normal in-game
    # orange controls or the lower replay button with this special exit action.
    modal = rgb[
        round(height * 0.38):round(height * 0.54),
        round(width * 0.18):round(width * 0.82),
    ]
    if modal.size == 0:
        return None
    modal_mean = modal.mean(axis=(0, 1))
    centered = width * 0.25 <= target[0] <= width * 0.75
    expected_height = height * 0.52 <= target[1] <= height * 0.62
    dark_brown_modal = float(modal_mean.mean()) < 105 and modal_mean[0] > modal_mean[1] * 1.25
    return target if centered and expected_height and dark_brown_modal else None


def locate_replay_button(image: Image.Image, grid: GridDetection) -> tuple[int, int] | None:
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    orange_button = (red > 120) & (green > 45) & (red > green * 1.18) & (green > blue * 1.15)
    minimum_top = min(image.height - 1, grid.rect[3] + round(grid.spacing * 0.30))
    orange_target = _button_center_from_color(
        image,
        orange_button,
        minimum_top / image.height,
        0.95,
        0.20,
    )
    if orange_target is not None:
        return orange_target

    # WePlay renders the main replay button gray after a loss, while keeping
    # the same wide rounded layout. Brown background pixels are not neutral,
    # so low RGB spread isolates the gray control without OCR.
    brightness = (red + green + blue) / 3
    neutral_spread = np.maximum.reduce([red, green, blue]) - np.minimum.reduce([red, green, blue])
    gray_button = (neutral_spread < 25) & (brightness > 55) & (brightness < 190)
    return _button_center_from_color(
        image,
        gray_button,
        minimum_top / image.height,
        0.95,
        0.20,
    )


def _result_banner_score(image: Image.Image) -> float:
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    height, width = rgb.shape[:2]
    patch = rgb[round(height * 0.07):round(height * 0.30), round(width * 0.18):round(width * 0.82)]
    red, green, blue = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
    banner = (red > 150) & (green > 45) & (red > green * 1.25) & (blue < 100)
    return float(banner.mean()) if banner.size else 0.0


class WePlayRecognizer:
    def __init__(self, references: dict[ScreenState, Iterable[Image.Image]] | None = None):
        self.references = {
            state: [_thumbnail(image) for image in images]
            for state, images in (references or {}).items()
        }

    @classmethod
    def from_fixture_directory(cls, directory: str | Path) -> "WePlayRecognizer":
        root = Path(directory)
        patterns = {
            ScreenState.LOBBY: "*lobby*.png",
            ScreenState.MATCHING: "*matching*.png",
        }
        references = {
            state: [Image.open(path).convert("RGB") for path in sorted(root.glob(pattern))]
            for state, pattern in patterns.items()
        }
        return cls(references)

    def analyze(
        self,
        image: Image.Image,
        player: Disc | None = None,
        grid_override: GridDetection | None = None,
    ) -> FrameAnalysis:
        if _is_loading_screen(image):
            return FrameAnalysis(
                ScreenState.LOADING,
                None,
                None,
                None,
                (),
                (),
                False,
                "Màn hình đang tải trận; chỉ chờ, không tìm bàn và không click",
            )
        if locate_opponent_left_exit_button(image) is not None:
            return FrameAnalysis(
                ScreenState.OPPONENT_LEFT,
                None,
                None,
                None,
                (),
                (),
                False,
                "Đối thủ rời sau khi ghép; chờ ổn định để bấm Thoát",
            )
        grid = grid_override or locate_grid(image)
        if grid is None:
            state, distance = self._classify_reference(image)
            message = f"Không tìm thấy bàn; nearest-reference distance={distance:.3f}"
            return FrameAnalysis(state, None, None, None, (), (), False, message)

        candidates, highlights = read_board_candidates(image, grid, player)
        highlight_set = set(highlights)
        detected_player = player
        board = candidates[0]
        if highlights and detected_player is None:
            matches: list[tuple[Board, Disc]] = []
            for candidate in candidates:
                side = infer_player(candidate, highlights)
                if side is not None:
                    matches.append((candidate, side))
            if len(matches) == 1:
                board, detected_player = matches[0]
        elif detected_player is not None:
            board = candidates[0]
        legal = tuple(legal_moves(board, detected_player)) if detected_player else ()
        board_validated = bool(highlights) and detected_player is not None and highlight_set == set(legal)
        occupied = sum(cell != Disc.EMPTY for row in board for cell in row)
        replay_target = locate_replay_button(image, grid)

        if grid.top / image.height > 0.35 and (
            _result_banner_score(image) > 0.12
            or (occupied >= 50 and replay_target is not None)
        ):
            state = ScreenState.RESULT
            message = "Phát hiện bàn ở layout kết quả"
        elif highlights:
            state = ScreenState.PLAYING_MY_TURN
            if board_validated:
                color = "ĐEN" if detected_player == Disc.BLACK else "TRẮNG"
                message = f"Tự xác định bạn cầm quân {color}; ô vàng khớp luật"
            else:
                message = "Chưa xác định chắc màu hoặc ô vàng không khớp luật; không hành động"
        else:
            state = ScreenState.PLAYING_WAIT
            message = "Có bàn nhưng không có ô vàng; đang chờ hoặc có animation"
        return FrameAnalysis(state, grid, board, detected_player, highlights, legal, board_validated, message)

    def _classify_reference(self, image: Image.Image) -> tuple[ScreenState, float]:
        if not self.references:
            return ScreenState.UNKNOWN, 1.0
        sample = _thumbnail(image)
        distances = {
            state: min(float(np.mean(np.abs(sample - reference))) for reference in references)
            for state, references in self.references.items()
            if references
        }
        if not distances:
            return ScreenState.UNKNOWN, 1.0
        state = min(distances, key=distances.get)
        distance = distances[state]
        return (state if distance < 0.18 else ScreenState.UNKNOWN), distance


def annotate(image: Image.Image, analysis: FrameAnalysis, chosen: Move | None = None) -> Image.Image:
    output = image.convert("RGB").copy()
    draw = ImageDraw.Draw(output)
    if analysis.grid:
        left, top, right, bottom = analysis.grid.rect
        draw.rectangle((left, top, right, bottom), outline=(0, 255, 100), width=3)
        for index in range(1, 8):
            x = left + index * analysis.grid.spacing
            y = top + index * analysis.grid.spacing
            draw.line((x, top, x, bottom), fill=(0, 220, 255), width=1)
            draw.line((left, y, right, y), fill=(0, 220, 255), width=1)
        if chosen:
            center_x, center_y = analysis.grid.center(chosen)
            radius = max(8, analysis.grid.spacing // 4)
            draw.ellipse(
                (center_x - radius, center_y - radius, center_x + radius, center_y + radius),
                outline=(255, 40, 40),
                width=4,
            )
    return output


def board_signature(board: Board | None) -> str:
    if board is None:
        return ""
    symbols = {Disc.EMPTY: ".", Disc.BLACK: "B", Disc.WHITE: "W"}
    return "/".join("".join(symbols[cell] for cell in row) for row in board)


def moves_text(moves: Iterable[Move]) -> str:
    return ", ".join(move.algebraic for move in moves) or "—"
