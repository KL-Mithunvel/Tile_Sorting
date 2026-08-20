"""Validates a trained tile-grade classifier against the val/ split.

Usage:
    venv\\Scripts\\activate
    python cam_yolo\\val.py                              # uses runs/<name>/weights/best.pt
    python cam_yolo\\val.py --weights path\\to\\best.pt
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
    parser.add_argument(
        "--weights", type=Path, default=default_weights_path(config), help="Path to a trained .pt checkpoint"
    )
    args = parser.parse_args()

    if not args.weights.is_file():
        raise SystemExit(f"{args.weights} not found — train a model first with cam_yolo/train.py")

    dataset_dir = (CAM_YOLO_DIR / config["dataset_dir"]).resolve()
    model = YOLO(str(args.weights))
    metrics = model.val(data=str(dataset_dir))
    print(f"\ntop1 accuracy: {metrics.top1:.4f}")
    print(f"top5 accuracy: {metrics.top5:.4f}")


if __name__ == "__main__":
    main()
