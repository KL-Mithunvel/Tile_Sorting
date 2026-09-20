"""Accuracy of every deployable ONNX artifact on one common held-out split.

The point of doing this here rather than trusting cam_yolo's and cam_vit's own
reported numbers:

  1. **Same split for everyone.** `config.yaml`'s `val_dir` is cam_yolo's
     76-image split off the ORIGINAL dataset. cam_vit's headline 88.1% was
     scored against a 202-image split off the offline-AUGMENTED dataset, where
     augmented variants of a train image can land in val — not comparable, and
     optimistic. Its `results_vs_clean_baseline/` (92.1%) used this split.
  2. **Same artifact that gets deployed.** These numbers come from the .onnx
     file through runtime.py's preprocessing — the exact path the Pi will run,
     not the .pt through ultralytics/transformers. If an export or a
     preprocessing recipe is subtly wrong, it shows up here instead of on the
     line.
  3. **Quantization cost.** INT8 is only worth it if the accuracy it spends is
     less than the latency it buys. Nobody can tell you that without measuring
     both on the same images.

    python camera_models/cam_edge/evaluate.py
    python camera_models/cam_edge/evaluate.py --report results/accuracy.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from runtime import OnnxClassifier, load_image  # noqa: E402

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}


def load_config(path: Path | None = None) -> dict:
    with open(path or HERE / "config.yaml", "r") as f:
        return yaml.safe_load(f)


def collect_val_images(val_dir: Path, classes: list[str]) -> list[tuple[Path, str]]:
    items: list[tuple[Path, str]] = []
    for cls in classes:
        d = val_dir / cls
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.suffix.lower() in IMAGE_SUFFIXES:
                items.append((p, cls))
    return items


def evaluate_one(clf: OnnxClassifier, items: list[tuple[Path, str]], classes: list[str]) -> dict:
    correct = 0
    confusion = {t: defaultdict(int) for t in classes}
    confidences_right: list[float] = []
    confidences_wrong: list[float] = []

    for path, truth in items:
        pred = clf.predict(load_image(path))
        confusion[truth][pred.label] += 1
        if pred.label == truth:
            correct += 1
            confidences_right.append(pred.confidence)
        else:
            confidences_wrong.append(pred.confidence)

    per_class = {}
    for cls in classes:
        tp = confusion[cls][cls]
        support = sum(confusion[cls].values())
        predicted_as = sum(confusion[t][cls] for t in classes)
        recall = tp / support if support else 0.0
        precision = tp / predicted_as if predicted_as else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        per_class[cls] = {
            "support": support,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }

    n = len(items)
    acc = correct / n if n else 0.0
    # Wilson score interval — on a 76-image val set the point estimate alone
    # is misleading, and the whole question here is whether a few points of
    # accuracy difference between models is real.
    lo, hi = wilson_interval(correct, n)

    return {
        "accuracy": round(acc, 4),
        "correct": correct,
        "total": n,
        "ci95_low": round(lo, 4),
        "ci95_high": round(hi, 4),
        "mean_conf_correct": round(float(np.mean(confidences_right)), 4)
        if confidences_right else None,
        "mean_conf_wrong": round(float(np.mean(confidences_wrong)), 4)
        if confidences_wrong else None,
        "per_class": per_class,
        "confusion": {t: dict(row) for t, row in confusion.items()},
    }


def wilson_interval(correct: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = correct / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    margin = z * ((p * (1 - p) / n + z**2 / (4 * n**2)) ** 0.5) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Score every ONNX artifact on the common val split.")
    ap.add_argument("--report", type=str, default=None)
    ap.add_argument("--config", type=str, default=None)
    ap.add_argument("--threads", type=int, default=4)
    args = ap.parse_args(argv)

    cfg = load_config(Path(args.config) if args.config else None)
    classes = cfg["classes"]
    val_dir = (HERE / cfg["val_dir"]).resolve()
    items = collect_val_images(val_dir, classes)

    if not items:
        print(f"No val images under {val_dir}.\n"
              f"Build the split first:  python camera_models/cam_yolo/prepare_dataset.py",
              file=sys.stderr)
        sys.exit(1)

    counts = {c: sum(1 for _, t in items if t == c) for c in classes}
    print(f"Val split: {val_dir}")
    print(f"  {len(items)} images  {counts}\n")

    entries = [(m["name"], HERE / m["onnx"], m["preprocess"]) for m in cfg["models"]]
    if cfg.get("quantize", {}).get("enabled"):
        out_dir = HERE / cfg["quantize"]["output_dir"]
        for m in cfg["models"]:
            q = out_dir / f"{m['name']}_int8.onnx"
            if q.exists():
                entries.append((f"{m['name']} INT8", q, m["preprocess"]))

    results = {}
    print(f"  {'model':24s} {'MB':>7s} {'acc':>8s} {'95% CI':>16s}")
    for name, path, mode in entries:
        if not path.exists():
            print(f"  SKIP {name}: missing {path}")
            continue
        clf = OnnxClassifier(path, classes, mode, threads=args.threads)
        r = evaluate_one(clf, items, classes)
        r["onnx_mb"] = round(path.stat().st_size / 1e6, 1)
        results[name] = r
        print(f"  {name:24s} {r['onnx_mb']:7.1f} {r['accuracy']*100:7.1f}% "
              f"  [{r['ci95_low']*100:4.1f}, {r['ci95_high']*100:4.1f}]")
        del clf

    print("\nPer-class recall (which grades each model actually gets right):")
    header = "  " + " " * 24 + "".join(f"{c:>8s}" for c in classes)
    print(header)
    for name, r in results.items():
        row = "".join(f"{r['per_class'][c]['recall']*100:7.0f}%" for c in classes)
        print(f"  {name:24s}{row}")

    if args.report:
        out = Path(args.report)
        if not out.is_absolute():
            out = HERE / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(
            {"val_dir": str(val_dir), "class_counts": counts, "results": results}, indent=2))
        print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
