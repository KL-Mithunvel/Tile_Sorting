"""Runs a trained tile-grade ViT classifier against one image or a folder of
images and prints the predicted grade + confidence for each.

Usage:
    venv\\Scripts\\activate
    python camera_models\\cam_vit\\predict.py path\\to\\tile.jpg
    python camera_models\\cam_vit\\predict.py path\\to\\folder --weights path\\to\\checkpoint
"""

from __future__ import annotations

import argparse
from pathlib import Path

import torch
import yaml
from PIL import Image
from transformers import ViTForImageClassification, ViTImageProcessor

from dataset import build_transforms

CAM_VIT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = CAM_VIT_DIR / "config.yaml"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def default_weights_path(config: dict) -> Path:
    return CAM_VIT_DIR / config["train"]["output_dir"] / "best"


def main() -> None:
    config = load_config()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Image file or folder of images")
    parser.add_argument(
        "--weights", type=Path, default=default_weights_path(config), help="Path to a trained checkpoint directory"
    )
    args = parser.parse_args()

    if not args.weights.is_dir():
        raise SystemExit(f"{args.weights} not found — train a model first with cam_vit/train.py")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    processor = ViTImageProcessor.from_pretrained(str(args.weights))
    model = ViTForImageClassification.from_pretrained(str(args.weights)).to(device).eval()
    image_size = processor.size.get("height", 224)
    transform = build_transforms(image_size, processor.image_mean, processor.image_std, aug_cfg=None, train=False)

    if args.source.is_dir():
        paths = sorted(p for p in args.source.iterdir() if p.suffix.lower() in SUPPORTED_EXTENSIONS)
    else:
        paths = [args.source]

    with torch.no_grad():
        for path in paths:
            img = Image.open(path).convert("RGB")
            pixel_values = transform(img).unsqueeze(0).to(device)
            logits = model(pixel_values=pixel_values).logits
            probs = logits.softmax(dim=1)[0]
            top1 = int(probs.argmax())
            grade = model.config.id2label[top1]
            print(f"{path}: grade={grade} confidence={float(probs[top1]):.3f}")


if __name__ == "__main__":
    main()
