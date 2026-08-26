"""Runs the trained tile-grade classifier against one image or a folder of
images and prints the predicted grade + confidence for each.

Usage:
    venv\\Scripts\\activate
    python camera_models\\cam_yolo\\predict.py path\\to\\tile.jpg
    python camera_models\\cam_yolo\\predict.py path\\to\\folder --weights path\\to\\best.pt
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml
from ultralytics import YOLO

CAM_YOLO_DIR = Path(__file__).resolve().parent
CONFIG_PATH = CAM_YOLO_DIR / "config.yaml"


def load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def default_weights_path(config: dict) -> Path:
    train_cfg = config["train"]
    return CAM_YOLO_DIR / train_cfg["project"] / train_cfg["name"] / "weights" / "best.pt"


def main() -> None:
    config = load_config()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Image file or folder of images")
    parser.add_argument(
        "--weights", type=Path, default=default_weights_path(config), help="Path to a trained .pt checkpoint"
    )
    args = parser.parse_args()

    if not args.weights.is_file():
        raise SystemExit(f"{args.weights} not found — train a model first with cam_yolo/train.py")

    model = YOLO(str(args.weights))
    results = model(str(args.source))

    for result in results:
        top1 = result.probs.top1
        top1_conf = float(result.probs.top1conf)
        grade = result.names[top1]
        print(f"{result.path}: grade={grade} confidence={top1_conf:.3f}")


if __name__ == "__main__":
    main()
