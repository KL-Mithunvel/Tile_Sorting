"""Exports a trained tile-grade YOLO classifier to ONNX, for deployment
targets that don't run PyTorch/Ultralytics directly -- specifically, Edge
Impulse's Bring Your Own Model (BYOM) import for the Arduino UNO Q (see
the repo root CLAUDE.md's Deployment Notes and the 2026-08-26 session's
real-time feasibility research: the UNO Q's QRB2210 has no dedicated vision
NPU, so this small a classifier -- yolo26s-cls is 5.4M params, yolo26n-cls
1.5M -- is the safer real-time candidate compared to cam_vit's 86M-param
ViT-Base).

Usage:
    venv\\Scripts\\activate
    python camera_models\\cam_yolo\\export_onnx.py
    python camera_models\\cam_yolo\\export_onnx.py --weights path\\to\\best.pt
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
    parser.add_argument("--imgsz", type=int, default=config["train"].get("imgsz", 224))
    parser.add_argument("--opset", type=int, default=17)
    args = parser.parse_args()

    if not args.weights.is_file():
        raise SystemExit(f"{args.weights} not found — train a model first with cam_yolo/train.py")

    model = YOLO(str(args.weights))
    onnx_path = model.export(format="onnx", imgsz=args.imgsz, opset=args.opset)
    print(f"Exported ONNX model to {onnx_path}")


if __name__ == "__main__":
    main()
