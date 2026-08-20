"""Splits the per-grade cropped tile photos in data/roboflow_dataset/<grade>/
(built by development/prepare_roboflow_dataset.py) into the train/<class>/,
val/<class>/ directory layout Ultralytics YOLO classification training
expects (see Ultralytics classify dataset docs).

Copies rather than moves/symlinks — source images stay untouched under
data/, and cam_yolo/dataset/ can be regenerated (a different split seed, a
different val_fraction) by just deleting cam_yolo/dataset/ and rerunning
this script.

Usage:
    venv\\Scripts\\activate
    python cam_yolo\\prepare_dataset.py
"""

from __future__ import annotations

import random
import shutil
from pathlib import Path

import yaml

CAM_YOLO_DIR = Path(__file__).resolve().parent
CONFIG_PATH = CAM_YOLO_DIR / "config.yaml"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def main() -> None:
    config = load_config()
    source_dir = (CAM_YOLO_DIR / config["source_dataset_dir"]).resolve()
    dataset_dir = (CAM_YOLO_DIR / config["dataset_dir"]).resolve()
    classes = config["classes"]
    val_fraction = config["split"]["val_fraction"]
    seed = config["split"]["seed"]

    if dataset_dir.exists():
        print(f"Removing existing split at {dataset_dir}")
        shutil.rmtree(dataset_dir)

    rng = random.Random(seed)
    print(f"Splitting {source_dir} -> {dataset_dir} (val_fraction={val_fraction}, seed={seed})\n")

    total_train = 0
    total_val = 0
    for grade in classes:
        grade_dir = source_dir / grade
        if not grade_dir.is_dir():
            print(f"  [WARN] no source folder for class {grade!r} at {grade_dir}, skipping")
            continue

        images = sorted(p for p in grade_dir.iterdir() if p.suffix.lower() in SUPPORTED_EXTENSIONS)
        rng.shuffle(images)

        n_val = max(1, round(len(images) * val_fraction)) if images else 0
        val_images = images[:n_val]
        train_images = images[n_val:]

        for split_name, split_images in (("train", train_images), ("val", val_images)):
            out_dir = dataset_dir / split_name / grade
            out_dir.mkdir(parents=True, exist_ok=True)
            for src in split_images:
                shutil.copy2(src, out_dir / src.name)

        total_train += len(train_images)
        total_val += len(val_images)
        print(f"  {grade:4s} {len(images):4d} images -> train={len(train_images):4d} val={len(val_images):4d}")

    print(f"\nTotal: train={total_train} val={total_val}")
    print(f"Dataset ready at {dataset_dir}")


if __name__ == "__main__":
    main()
