"""Fine-tunes a YOLO26 classification model on the tile-grade dataset built
by prepare_dataset.py.

Usage:
    venv\\Scripts\\activate
    python camera_models\\cam_yolo\\prepare_dataset.py   # once, or whenever the split should change
    python camera_models\\cam_yolo\\train.py
"""

from __future__ import annotations

from pathlib import Path

import yaml
from ultralytics import YOLO

CAM_YOLO_DIR = Path(__file__).resolve().parent
CONFIG_PATH = CAM_YOLO_DIR / "config.yaml"


def load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def main() -> None:
    config = load_config()
    dataset_dir = (CAM_YOLO_DIR / config["dataset_dir"]).resolve()
    if not dataset_dir.is_dir():
        raise SystemExit(f"{dataset_dir} does not exist yet — run cam_yolo/prepare_dataset.py first")

    train_cfg = config["train"]
    model = YOLO(train_cfg["model"])
    model.train(
        data=str(dataset_dir),
        epochs=train_cfg["epochs"],
        imgsz=train_cfg["imgsz"],
        batch=train_cfg["batch"],
        patience=train_cfg["patience"],
        project=str(CAM_YOLO_DIR / train_cfg["project"]),
        name=train_cfg["name"],
        degrees=train_cfg.get("degrees", 0.0),
        flipud=train_cfg.get("flipud", 0.0),
        dropout=train_cfg.get("dropout", 0.0),
        cos_lr=train_cfg.get("cos_lr", False),
    )


if __name__ == "__main__":
    main()
