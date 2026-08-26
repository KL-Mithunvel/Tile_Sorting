"""Creates an offline-augmented COPY of data/roboflow_dataset/ -- the
per-tile grade-labeled crops both camera_models/cam_yolo/ and
camera_models/cam_vit/ train on -- to
expand the training pool from 376 images to ~1000 via real geometric
augmentation (mild affine rotation/shear, mild perspective), written as
actual files alongside the untouched originals.

Why an offline step rather than relying on cam_yolo's/cam_vit's own online
augmentation: Ultralytics' built-in `Albumentations` class
(ultralytics/data/augment.py, confirmed via source, 2026-08-26) applies only
color/quality transforms by default (Blur, MedianBlur, ToGray, CLAHE,
RandomBrightnessContrast, RandomGamma, ImageCompression) -- no rotation,
shear, or perspective at all. cam_yolo's `train.degrees`/`train.flipud`
config keys and cam_vit's RandomNinetyRotation (see their READMEs) already
cover rotation/flip, but neither pipeline does shear or perspective. This
script adds those directly via the `albumentations` library (API confirmed
against the installed version 2.0.8 and the upstream source,
albumentations-team/albumentations, 2026-08-26: `A.Affine`'s `rotate`/
`shear` are in degrees, sampled per-call from the given range; `A.Perspective`'s
`scale` is the corner-jitter standard deviation as a fraction of image size,
default (0.05, 0.1) -- this script uses a narrower range for a milder
effect).

Source -> output is additive: every original image is copied through
byte-for-byte unchanged (shutil.copy2, no recompression), plus N augmented
variants generated per image, so nothing is lost and every augmented image
is an inspectable file on disk (unlike cam_yolo's/cam_vit's online
per-epoch augmentation, which is regenerated fresh each epoch and never
saved).

This produces a dataset copy only -- it does not change
camera_models/cam_yolo/config.yaml's or camera_models/cam_vit/config.yaml's
`source_dataset_dir`. Point either at
data/roboflow_dataset_augmented (instead of data/roboflow_dataset) and rerun
that pipeline's prepare_dataset.py to actually train on the expanded set.

Usage:
    venv\\Scripts\\activate
    pip install albumentations
    python development\\augment_dataset.py
"""

from __future__ import annotations

import csv
import random
import shutil
from pathlib import Path

import albumentations as A
import cv2

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "data" / "roboflow_dataset"
OUTPUT_DATASET_DIR = REPO_ROOT / "data" / "roboflow_dataset_augmented"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
MANIFEST_CSV = OUTPUT_DIR / "roboflow_dataset_augmented_manifest.csv"

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
TARGET_TOTAL_IMAGES = 1000  # ~1k, per repo owner request, 2026-08-26
SEED = 42

# Mild affine + perspective, per repo owner's explicit spec (2026-08-26):
# rotation capped under 15 degrees, shear/perspective kept mild. Values
# chosen well inside Albumentations' own "reasonable" ranges (its docs cite
# [-45, 45] as reasonable for shear, [-360, 360] as the full range for
# rotate; Perspective's own default scale is (0.05, 0.1)) so augmented tiles
# stay recognizably real tile photos, not distorted past what a real
# camera-angle/mounting tolerance on the actual conveyor would produce.
TRANSFORM = A.Compose(
    [
        A.Affine(
            rotate=(-15, 15),  # user-specified cap
            shear=(-8, 8),  # mild
            scale=(0.95, 1.05),  # mild -- these crops are already tile-boundary-tight
            border_mode=cv2.BORDER_REFLECT_101,  # avoid an artificial solid-color border patch
            p=0.9,
        ),
        A.Perspective(
            scale=(0.02, 0.06),  # mild -- below Albumentations' own default (0.05, 0.1)
            keep_size=True,
            p=0.5,
        ),
    ]
)


def find_source_images() -> list[tuple[Path, str]]:
    images = []
    for grade_dir in sorted(SOURCE_DIR.iterdir()):
        if not grade_dir.is_dir():
            continue
        for path in sorted(grade_dir.iterdir()):
            if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                images.append((path, grade_dir.name))
    return images


def main() -> None:
    if OUTPUT_DATASET_DIR.exists():
        print(f"Removing existing augmented copy at {OUTPUT_DATASET_DIR}")
        shutil.rmtree(OUTPUT_DATASET_DIR)

    rng = random.Random(SEED)

    images = find_source_images()
    n_source = len(images)
    if n_source == 0:
        raise SystemExit(f"No images found under {SOURCE_DIR} -- run development/prepare_roboflow_dataset.py first")

    extra_needed = max(0, TARGET_TOTAL_IMAGES - n_source)
    base_extra, remainder = divmod(extra_needed, n_source)
    # `remainder` images get one extra augmented copy beyond `base_extra`,
    # chosen via a shuffled index rather than just the first N in sorted
    # (grade-clustered) order, so the extra is spread roughly proportionally
    # across every class instead of concentrated in one.
    order = list(range(n_source))
    rng.shuffle(order)
    bonus_indices = set(order[:remainder])

    print(
        f"Source: {n_source} images -> target {TARGET_TOTAL_IMAGES} "
        f"({base_extra} augmented copies/image, +1 more for {remainder} of them)\n"
    )

    OUTPUT_DATASET_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    manifest_rows = []
    total_written = 0
    for i, (src_path, grade) in enumerate(images):
        out_dir = OUTPUT_DATASET_DIR / grade
        out_dir.mkdir(parents=True, exist_ok=True)

        orig_out = out_dir / src_path.name
        shutil.copy2(src_path, orig_out)
        manifest_rows.append({"source": str(src_path), "output": str(orig_out), "grade": grade, "augmented": False})
        total_written += 1

        n_augmented = base_extra + (1 if i in bonus_indices else 0)
        if n_augmented == 0:
            continue

        bgr = cv2.imread(str(src_path))
        if bgr is None:
            print(f"  [WARN] unreadable, skipping augmentation for: {src_path}")
            continue

        for k in range(n_augmented):
            augmented = TRANSFORM(image=bgr)["image"]
            aug_out = out_dir / f"{src_path.stem}_aug{k}{src_path.suffix}"
            cv2.imwrite(str(aug_out), augmented)
            manifest_rows.append({"source": str(src_path), "output": str(aug_out), "grade": grade, "augmented": True})
            total_written += 1

    with open(MANIFEST_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["source", "output", "grade", "augmented"])
        writer.writeheader()
        writer.writerows(manifest_rows)

    print(f"Wrote {total_written} images to {OUTPUT_DATASET_DIR}")
    print(f"Manifest written to {MANIFEST_CSV}\n")

    print("Per-class counts:")
    for grade in sorted({g for _, g in images}):
        out_dir = OUTPUT_DATASET_DIR / grade
        count = sum(1 for p in out_dir.iterdir() if p.suffix.lower() in SUPPORTED_EXTENSIONS)
        print(f"  {grade:4s} {count}")


if __name__ == "__main__":
    main()
