"""Exports a trained tile-grade ViT classifier to ONNX, for deployment
targets that don't run PyTorch/HuggingFace transformers directly --
specifically, Edge Impulse's Bring Your Own Model (BYOM) import for the
Arduino UNO Q (see the repo root CLAUDE.md's Deployment Notes and the
2026-08-26 session's real-time feasibility research).

ViT-Base (86M params) is a real risk for true real-time CPU inference on
the UNO Q's QRB2210 -- Qualcomm's own product brief describes its Hexagon
DSP as targeting low-power sensor fusion/audio, not a dedicated vision NPU.
This export exists so Edge Impulse's own on-device latency profiler can
give a real number instead of a guess, alongside cam_yolo's much smaller
(5.4M/1.5M param) candidates.

Usage:
    venv\\Scripts\\activate
    python camera_models\\cam_vit\\export_onnx.py
    python camera_models\\cam_vit\\export_onnx.py --weights path\\to\\checkpoint --output path\\to\\model.onnx
"""

from __future__ import annotations

import argparse
from pathlib import Path

import torch
import yaml
from transformers import ViTForImageClassification, ViTImageProcessor

CAM_VIT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = CAM_VIT_DIR / "config.yaml"


def load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def default_weights_path(config: dict) -> Path:
    return CAM_VIT_DIR / config["train"]["output_dir"] / "best"


class OnnxExportWrapper(torch.nn.Module):
    """Returns a plain logits tensor -- ViTForImageClassification's forward
    returns a ModelOutput object, which torch.onnx.export can't trace."""

    def __init__(self, model: ViTForImageClassification):
        super().__init__()
        self.model = model

    def forward(self, pixel_values: torch.Tensor) -> torch.Tensor:
        return self.model(pixel_values=pixel_values).logits


def main() -> None:
    config = load_config()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--weights", type=Path, default=default_weights_path(config), help="Path to a trained checkpoint directory"
    )
    parser.add_argument(
        "--output", type=Path, default=None, help="Output .onnx path (default: <weights>/model.onnx)"
    )
    parser.add_argument("--opset", type=int, default=17)
    args = parser.parse_args()

    if not args.weights.is_dir():
        raise SystemExit(f"{args.weights} not found — train a model first with cam_vit/train.py")

    output_path = args.output or (args.weights / "model.onnx")

    processor = ViTImageProcessor.from_pretrained(str(args.weights))
    model = ViTForImageClassification.from_pretrained(str(args.weights)).eval()
    image_size = processor.size.get("height", 224)

    wrapped = OnnxExportWrapper(model)
    dummy_input = torch.randn(1, 3, image_size, image_size)
    torch.onnx.export(
        wrapped,
        dummy_input,
        str(output_path),
        input_names=["pixel_values"],
        output_names=["logits"],
        dynamic_axes={"pixel_values": {0: "batch"}, "logits": {0: "batch"}},
        opset_version=args.opset,
    )
    print(f"Exported ONNX model to {output_path}")


if __name__ == "__main__":
    main()
