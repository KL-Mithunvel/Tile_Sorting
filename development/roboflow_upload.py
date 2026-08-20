"""Uploads the cropped tile images under data/roboflow_dataset/ (built by
prepare_roboflow_dataset.py) to two Roboflow projects:

- tile-detection (object detection): every image, tagged with grade/source,
  for later tile-box (and eventually defect-box) annotation.
- tile-grade-classification (single-label classification): every image,
  labeled with its grade (3A/3B/4/5) taken directly from the folder it's
  in - confirmed by the repo owner (2026-08-20 session) as a reliable
  per-tile ground truth, not just a batch tendency.

Requires ROBOFLOW_API_KEY / ROBOFLOW_WORKSPACE in a .env file at the repo
root (gitignored - see api_keys_create in that session).

Usage:
    venv\\Scripts\\activate
    python development\\roboflow_upload.py
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
import os

import roboflow

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = REPO_ROOT / "data" / "roboflow_dataset"

DETECTION_PROJECT_ID = "tile-detection-bvnh4"
CLASSIFICATION_PROJECT_ID = "tile-grade-classification"
BATCH_NAME = "initial-import-2026-08-20"


def iter_dataset_images():
    for grade_dir in sorted(DATASET_DIR.iterdir()):
        if not grade_dir.is_dir():
            continue
        grade = grade_dir.name
        for path in sorted(grade_dir.iterdir()):
            if path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
                continue
            capture_type = "dslr" if path.stem.startswith("dslr__") else "phone"
            yield path, grade, capture_type


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    api_key = os.environ["ROBOFLOW_API_KEY"]
    workspace_name = os.environ["ROBOFLOW_WORKSPACE"]

    rf = roboflow.Roboflow(api_key=api_key)
    workspace = rf.workspace(workspace_name)
    detection_project = workspace.project(DETECTION_PROJECT_ID)
    classification_project = workspace.project(CLASSIFICATION_PROJECT_ID)

    images = list(iter_dataset_images())
    print(f"Found {len(images)} images under {DATASET_DIR}\n")

    det_ok = det_fail = cls_ok = cls_fail = 0
    for i, (path, grade, capture_type) in enumerate(images, start=1):
        tags = [f"grade:{grade.lower()}", f"source:{capture_type}"]

        try:
            detection_project.upload(
                image_path=str(path), split="train",
                batch_name=BATCH_NAME, tag_names=tags,
            )
            det_ok += 1
        except Exception as exc:
            det_fail += 1
            print(f"  [{i}/{len(images)}] detection upload FAILED for {path.name}: {exc}")

        try:
            classification_project.upload(
                image_path=str(path), annotation_path=grade, split="train",
                batch_name=BATCH_NAME, tag_names=[f"source:{capture_type}"],
            )
            cls_ok += 1
        except Exception as exc:
            cls_fail += 1
            print(f"  [{i}/{len(images)}] classification upload FAILED for {path.name}: {exc}")

        if i % 25 == 0 or i == len(images):
            print(f"  ...{i}/{len(images)} processed")

    print(f"\ntile-detection:            {det_ok} uploaded, {det_fail} failed")
    print(f"tile-grade-classification: {cls_ok} uploaded, {cls_fail} failed")


if __name__ == "__main__":
    main()
