"""Copy a trained model out of camera_models/ into camera_node/models/ for deployment.

`camera_models/` is dev-only tooling and its `runs/` folders are gitignored, so
the Pi station cannot reach a trained model by cloning the repo. This script
promotes one chosen artifact into `camera_node/models/`, which IS committed
(see the scoped exception in .gitignore), alongside a metadata JSON recording
what it is, how to preprocess for it, and what it scored.

Deliberately a manual, explicit copy rather than an import across folders —
same one-directional, copy-as-needed relationship the `Acoustic-Analysis`
submodule has with `acoustic_node` (`.claude/CLAUDE.md` Architecture). Nothing
in `camera_node/` may import from `camera_models/`.

    python camera_models/cam_edge/stage_model.py                  # default model
    python camera_models/cam_edge/stage_model.py --model vit-base-p16-224
    python camera_models/cam_edge/stage_model.py --list
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import date
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
DEST_DIR = REPO_ROOT / "camera_node" / "models"

# Default is the nano YOLO: on the common 76-image val split it scored 82.9%
# vs. the 7x-larger yolo26s-cls's 85.5% — a difference well inside both models'
# 95% confidence intervals — while being ~2.6x faster and 3.5x smaller. See
# camera_models/cam_edge/README.md for the full measurement.
DEFAULT_MODEL = "yolo26n-cls"


def load_config() -> dict:
    with open(HERE / "config.yaml", "r") as f:
        return yaml.safe_load(f)


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def read_accuracy(model_name: str) -> dict | None:
    """Pull this model's measured accuracy out of evaluate.py's report, so the
    deployed artifact carries its own provenance instead of a number someone
    remembered."""
    report = HERE / "results" / "accuracy.json"
    if not report.exists():
        return None
    data = json.loads(report.read_text())
    r = data.get("results", {}).get(model_name)
    if not r:
        return None
    return {
        "val_split": data.get("val_dir"),
        "val_images": r.get("total"),
        "accuracy": r.get("accuracy"),
        "ci95": [r.get("ci95_low"), r.get("ci95_high")],
        "per_class_recall": {c: v["recall"] for c, v in r.get("per_class", {}).items()},
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Stage a trained model into camera_node/models/.")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"Model name from config.yaml (default: {DEFAULT_MODEL})")
    ap.add_argument("--list", action="store_true", help="List available model names and exit.")
    ap.add_argument("--name", default=None, help="Output basename (default: derived from the model name).")
    args = ap.parse_args(argv)

    cfg = load_config()
    by_name = {m["name"]: m for m in cfg["models"]}

    if args.list:
        print("Available models (from camera_models/cam_edge/config.yaml):")
        for name, m in by_name.items():
            src = (HERE / m["onnx"]).resolve()
            mark = "ok " if src.exists() else "MISSING"
            print(f"  [{mark}] {name:22s} {m.get('note', '')}")
        return

    if args.model not in by_name:
        print(f"Unknown model '{args.model}'. Known: {', '.join(by_name)}", file=sys.stderr)
        sys.exit(1)

    entry = by_name[args.model]
    src = (HERE / entry["onnx"]).resolve()
    if not src.exists():
        print(f"No ONNX at {src}\nExport it first (see camera_models/cam_yolo/export_onnx.py).", file=sys.stderr)
        sys.exit(1)

    basename = args.name or f"tile_grade_{args.model.replace('-', '_')}"
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    dst_onnx = DEST_DIR / f"{basename}.onnx"
    dst_meta = DEST_DIR / f"{basename}.json"

    size_mb = src.stat().st_size / 1e6
    if size_mb > 50:
        print(f"WARNING: {size_mb:.0f} MB is large for a committed artifact. "
              f"Consider keeping this one out of git and copying it to the Pi by hand.")

    shutil.copy2(src, dst_onnx)

    meta = {
        "name": args.model,
        "staged_on": date.today().isoformat(),
        "source": str(src.relative_to(REPO_ROOT)).replace("\\", "/"),
        "onnx_mb": round(size_mb, 2),
        "sha256": sha256(dst_onnx),
        "classes": cfg["classes"],
        "preprocess": entry["preprocess"],
        "input_size": 224,
        "note": entry.get("note", ""),
        "measured": read_accuracy(args.model),
        "what_it_classifies": (
            "Cosmetic grade tier (3A/3B/4/5) of an intact tile. This model does NOT "
            "detect cracks or broken corners - that is camera_node's classical CV path "
            "(crack_detection.py / corner_detection.py). Trained on 376 photos of intact "
            "tiles; see camera_models/cam_edge/README.md before trusting any single "
            "prediction."
        ),
    }
    dst_meta.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"Staged {args.model}")
    print(f"  {dst_onnx.relative_to(REPO_ROOT)}  ({size_mb:.1f} MB)")
    print(f"  {dst_meta.relative_to(REPO_ROOT)}")
    if meta["measured"]:
        m = meta["measured"]
        print(f"  measured {m['accuracy']*100:.1f}% on {m['val_images']} val images "
              f"(95% CI {m['ci95'][0]*100:.1f}-{m['ci95'][1]*100:.1f})")
    print(f"\nEnable it in camera_node/python/camera/config.yaml:\n"
          f"  grading_model:\n    enabled: true\n    path: \"models/{basename}.onnx\"")


if __name__ == "__main__":
    main()
