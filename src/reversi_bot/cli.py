from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .engine import legal_moves
from .model import Disc, format_board
from .pipeline import analyze_screenshot
from .vision import VisionConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Recognize a Reversi screenshot and recommend a move.")
    parser.add_argument("image", type=Path, help="Static screenshot (.ppm, or PNG/JPEG with Pillow)")
    parser.add_argument("--config", type=Path, required=True, help="Vision calibration JSON")
    parser.add_argument("--player", choices=("black", "white"), required=True)
    parser.add_argument("--depth", type=int, default=5)
    parser.add_argument("--time-ms", type=int, default=1500)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    player = Disc.BLACK if args.player == "black" else Disc.WHITE
    try:
        config = VisionConfig.load(args.config)
        result = analyze_screenshot(args.image, config, player, args.depth, args.time_ms)
    except (OSError, ValueError, RuntimeError) as exc:
        logging.error("Analysis failed: %s", exc)
        return 2

    legal = legal_moves(result.board, player)
    payload = {
        "player": args.player,
        "recognition_min_confidence": round(result.recognition.min_confidence, 4),
        "legal_moves": [move.algebraic for move in legal],
        "chosen_move": result.search.move.algebraic if result.search.move else None,
        "target_pixel": list(result.target_pixel) if result.target_pixel else None,
        "score": round(result.search.score, 2),
        "completed_depth": result.search.completed_depth,
        "nodes": result.search.nodes,
        "elapsed_ms": result.search.elapsed_ms,
    }
    if args.as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(format_board(result.board, legal))
        print("\n" + json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

