"""Validates a trained tile-grade ViT classifier against the val/ split,
printing top1 accuracy plus per-class precision/recall/f1, and writing the
same kind of evaluation artifacts development/evaluate_grade_model.py
produces for the Roboflow models (confusion_matrix.png, metrics.json,
predictions.json) so cam_vit's results are recorded the same way.

Usage:
    venv\\Scripts\\activate
    python camera_models\\cam_vit\\val.py                              # uses runs/<output_dir>/best
    python camera_models\\cam_vit\\val.py --weights path\\to\\checkpoint
    python camera_models\\cam_vit\\val.py --output-dir path\\to\\dir     # default: camera_models/cam_vit/results
    python camera_models\\cam_vit\\val.py --dataset-dir path\\to\\split  # evaluate against a different train/val split
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import torch
import yaml
from torch.utils.data import DataLoader
from transformers import ViTForImageClassification, ViTImageProcessor

from dataset import TileGradeDataset, build_transforms

CAM_VIT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = CAM_VIT_DIR / "config.yaml"


def load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def default_weights_path(config: dict) -> Path:
    return CAM_VIT_DIR / config["train"]["output_dir"] / "best"


def summarize(rows: list[dict], classes: list[str]) -> dict:
    correct = sum(r["true"] == r["pred"] for r in rows)
    matrix = np.zeros((len(classes), len(classes)), dtype=int)
    for r in rows:
        matrix[classes.index(r["true"]), classes.index(r["pred"])] += 1

    per_class = {}
    for i, cls in enumerate(classes):
        tp = matrix[i, i]
        support = int(matrix[i, :].sum())
        predicted = int(matrix[:, i].sum())
        precision = tp / predicted if predicted else 0.0
        recall = tp / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        per_class[cls] = {"precision": precision, "recall": recall, "f1": f1, "support": support}

    return {
        "accuracy": correct / len(rows),
        "num_correct": correct,
        "num_total": len(rows),
        "confusion_matrix": matrix.tolist(),
        "per_class": per_class,
    }


def plot_confusion_matrix(matrix: list, classes: list[str], accuracy: float, out_path: Path) -> None:
    matrix = np.array(matrix)
    fig, ax = plt.subplots(figsize=(5, 4.5))
    im = ax.imshow(matrix, cmap="Blues")
    ax.set_xticks(range(len(classes)))
    ax.set_yticks(range(len(classes)))
    ax.set_xticklabels(classes)
    ax.set_yticklabels(classes)
    ax.set_xlabel("Predicted grade")
    ax.set_ylabel("True grade")
    ax.set_title(f"cam_vit (vit-base-patch16-224-in21k)\naccuracy={accuracy:.1%} (val split, n={matrix.sum()})")
    for i in range(len(classes)):
        for j in range(len(classes)):
            color = "white" if matrix[i, j] > matrix.max() / 2 else "black"
            ax.text(j, i, str(matrix[i, j]), ha="center", va="center", color=color)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main() -> None:
    config = load_config()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--weights", type=Path, default=default_weights_path(config), help="Path to a trained checkpoint directory"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=CAM_VIT_DIR / "results", help="Where to write evaluation artifacts"
    )
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=None,
        help="Evaluate against this train/val split dir instead of config.yaml's dataset_dir "
        "(must contain a val/<class>/ subfolder in the same layout)",
    )
    args = parser.parse_args()

    if not args.weights.is_dir():
        raise SystemExit(f"{args.weights} not found — train a model first with cam_vit/train.py")

    classes = config["classes"]
    dataset_dir = (args.dataset_dir if args.dataset_dir else CAM_VIT_DIR / config["dataset_dir"]).resolve()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    processor = ViTImageProcessor.from_pretrained(str(args.weights))
    model = ViTForImageClassification.from_pretrained(str(args.weights)).to(device).eval()

    image_size = processor.size.get("height", 224)
    transform = build_transforms(image_size, processor.image_mean, processor.image_std, aug_cfg=None, train=False)
    val_dataset = TileGradeDataset(dataset_dir / "val", classes, transform)
    loader = DataLoader(val_dataset, batch_size=16, shuffle=False)

    rows = []
    idx = 0
    with torch.no_grad():
        for batch in loader:
            pixel_values = batch["pixel_values"].to(device)
            labels = batch["labels"].numpy()
            logits = model(pixel_values=pixel_values).logits
            probs = logits.softmax(dim=1).cpu().numpy()
            preds = probs.argmax(axis=1)
            for label, pred, prob in zip(labels, preds, probs):
                path, _ = val_dataset.samples[idx]
                rows.append(
                    {
                        "image": str(path.relative_to(dataset_dir)),
                        "true": classes[label],
                        "pred": classes[pred],
                        "confidence": float(prob[pred]),
                    }
                )
                idx += 1

    result = summarize(rows, classes)

    print(f"top1 accuracy: {result['num_correct']}/{result['num_total']} = {result['accuracy']:.4f}\n")
    print(f"{'class':6s} {'precision':>10s} {'recall':>10s} {'f1':>10s} {'support':>8s}")
    for cls, m in result["per_class"].items():
        print(f"{cls:6s} {m['precision']:10.3f} {m['recall']:10.3f} {m['f1']:10.3f} {m['support']:8d}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    with open(args.output_dir / "predictions.json", "w") as f:
        json.dump(rows, f, indent=2)
    with open(args.output_dir / "metrics.json", "w") as f:
        json.dump(
            {
                "model": "cam_vit (google/vit-base-patch16-224-in21k, local fine-tune)",
                "weights": str(args.weights),
                "eval_set": "val split (same seed/split as camera_models/cam_yolo, see camera_models/cam_vit/config.yaml)",
                "accuracy": result["accuracy"],
                "num_correct": result["num_correct"],
                "num_total": result["num_total"],
                "per_class": result["per_class"],
            },
            f,
            indent=2,
        )
    plot_confusion_matrix(result["confusion_matrix"], classes, result["accuracy"], args.output_dir / "confusion_matrix.png")
    print(f"\nWrote predictions.json, metrics.json, confusion_matrix.png to {args.output_dir}")


if __name__ == "__main__":
    main()
