"""Splits the per-grade cropped tile photos in data/roboflow_dataset/<grade>/
(or data/roboflow_dataset_augmented/<grade>/, see config.yaml's
source_dataset_dir) into the train/<class>/, val/<class>/ directory layout
Ultralytics YOLO classification training expects (see Ultralytics classify
dataset docs).

Splits by SOURCE IMAGE GROUP, not by file (added 2026-08-26, matching
cam_vit/prepare_dataset.py -- see its docstring for the full rationale): if
source_dataset_dir is ever pointed at development/augment_dataset.py's
offline-augmented copy, `dslr__DSC_0022.jpg` and `..._aug0.jpg` are
near-duplicates of the same real tile and must not be split across
train/val, or val accuracy would be inflated by evaluating on an
essentially-already-seen image. Harmless no-op against the original,
non-augmented data/roboflow_dataset/ (every group there has exactly one
member) -- this pipeline currently still points at that original set.

Copies rather than moves/symlinks — source images stay untouched under
data/, and camera_models/cam_yolo/dataset/ can be regenerated (a different split seed, a
different val_fraction) by just deleting camera_models/cam_yolo/dataset/ and rerunning
this script.

Usage:
    venv\\Scripts\\activate
    python cam_yolo\\prepare_dataset.py
"""

from __future__ import annotations

import random
import re
import shutil
from pathlib import Path

import yaml

CAM_YOLO_DIR = Path(__file__).resolve().parent
CONFIG_PATH = CAM_YOLO_DIR / "config.yaml"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
AUG_SUFFIX_RE = re.compile(r"_aug\d+$")


def load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def group_key(path: Path) -> str:
    """The shared identity of every augmented variant of one source photo."""
    return AUG_SUFFIX_RE.sub("", path.stem)


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

    oversample_train = config["split"].get("oversample_train", False)

    rng = random.Random(seed)
    print(f"Splitting {source_dir} -> {dataset_dir} (val_fraction={val_fraction}, seed={seed})\n")

    per_class_train_images: dict[str, list[Path]] = {}
    total_val = 0
    for grade in classes:
        grade_dir = source_dir / grade
        if not grade_dir.is_dir():
            print(f"  [WARN] no source folder for class {grade!r} at {grade_dir}, skipping")
            continue

        images = sorted(p for p in grade_dir.iterdir() if p.suffix.lower() in SUPPORTED_EXTENSIONS)

        groups: dict[str, list[Path]] = {}
        for img in images:
            groups.setdefault(group_key(img), []).append(img)
        group_keys = list(groups.keys())
        rng.shuffle(group_keys)

        target_val = max(1, round(len(images) * val_fraction)) if images else 0
        val_images: list[Path] = []
        train_images: list[Path] = []
        for key in group_keys:
            members = groups[key]
            if len(val_images) < target_val:
                val_images.extend(members)
            else:
                train_images.extend(members)
        per_class_train_images[grade] = train_images

        out_dir = dataset_dir / "val" / grade
        out_dir.mkdir(parents=True, exist_ok=True)
        for src in val_images:
            shutil.copy2(src, out_dir / src.name)

        total_val += len(val_images)
        print(
            f"  {grade:4s} {len(images):4d} images ({len(group_keys)} source groups) "
            f"-> train={len(train_images):4d} val={len(val_images):4d}"
        )

    max_train = max((len(imgs) for imgs in per_class_train_images.values()), default=0)
    total_train = 0
    for grade, train_images in per_class_train_images.items():
        out_dir = dataset_dir / "train" / grade
        out_dir.mkdir(parents=True, exist_ok=True)
        for src in train_images:
            shutil.copy2(src, out_dir / src.name)

        n_written = len(train_images)
        if oversample_train and train_images and len(train_images) < max_train:
            n_needed = max_train - len(train_images)
            for i in range(n_needed):
                src = train_images[i % len(train_images)]
                dst = out_dir / f"{src.stem}_dup{i // len(train_images)}{src.suffix}"
                shutil.copy2(src, dst)
            n_written += n_needed
            print(f"  {grade:4s} oversampled train {len(train_images):4d} -> {n_written:4d} (target {max_train})")

        total_train += n_written

    print(f"\nTotal: train={total_train} val={total_val}")
    print(f"Dataset ready at {dataset_dir}")


if __name__ == "__main__":
    main()
