"""Batch-crops every tile photo under data/ down to just the tile (via the
real production camera.segmentation.segment_tile(), same as
development/analyze_dataset.py) and organizes the crops by grade, as prep for
uploading to Roboflow for annotation.

data/ holds photos from 8 folders, two capture setups per grade (a phone/
generic camera batch and a DSLR "(Cam)" batch), covering grades 3A/3B/4/5 —
per repo owner (2026-08-16 session): the lower grades (3A/3B) contain real
damaged tiles (cracks/broken corners), not yet annotated at the box/polygon
level. This script does NOT do any defect labeling itself — it only crops
each source photo down to the tile (removing the checkerboard calibration
background) and organizes outputs by grade/capture-type, plus builds contact
sheets (grids of numbered thumbnails) so a human (or a vision-capable review
pass) can quickly flag which crops show visible damage before they're
uploaded to Roboflow for real box/polygon annotation.

Usage:
    venv\\Scripts\\activate
    python development\\prepare_roboflow_dataset.py
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import cv2
import numpy as np
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
OUTPUT_DATASET_DIR = DATA_DIR / "roboflow_dataset"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
CONTACT_SHEET_DIR = OUTPUT_DIR / "contact_sheets"
MANIFEST_CSV = OUTPUT_DIR / "roboflow_dataset_manifest.csv"

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO_ROOT / "camera_node" / "python"))

from tile_param_tuner import downscale_for_processing, load_image_bgr  # noqa: E402

from camera.segmentation import segment_tile  # noqa: E402

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
FOLDER_PATTERN = re.compile(r"9x9\s*-?\s*([0-9]+[A-Za-z]?)\s*(\(Cam\))?", re.IGNORECASE)

# Contact sheet layout
THUMB_SIZE = 260
GRID_COLS = 5
GRID_ROWS = 5
TILES_PER_SHEET = GRID_COLS * GRID_ROWS


def load_camera_config() -> dict:
    config_path = REPO_ROOT / "camera_node" / "python" / "camera" / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def parse_grade_and_capture(folder_name: str) -> tuple[str, str]:
    match = FOLDER_PATTERN.match(folder_name.strip())
    if not match:
        return folder_name.strip().upper(), "unknown"
    grade = match.group(1).upper()
    capture_type = "dslr" if match.group(2) else "phone"
    return grade, capture_type


def find_dataset_images() -> list[Path]:
    images = []
    for folder in sorted(DATA_DIR.iterdir()):
        if not folder.is_dir() or folder.name in {"roboflow_dataset", "camera_captures"}:
            continue
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                images.append(path)
    return images


def resize_and_pad(bgr: np.ndarray, size: int) -> np.ndarray:
    h, w = bgr.shape[:2]
    scale = size / float(max(h, w))
    new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
    resized = cv2.resize(bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
    canvas = np.full((size, size, 3), 255, dtype=np.uint8)
    y0 = (size - new_h) // 2
    x0 = (size - new_w) // 2
    canvas[y0 : y0 + new_h, x0 : x0 + new_w] = resized
    return canvas


def build_contact_sheet(thumbs: list[tuple[int, np.ndarray]]) -> np.ndarray:
    canvas = np.full((GRID_ROWS * THUMB_SIZE, GRID_COLS * THUMB_SIZE, 3), 230, dtype=np.uint8)
    for i, (idx, crop) in enumerate(thumbs):
        r, c = divmod(i, GRID_COLS)
        cell = resize_and_pad(crop, THUMB_SIZE - 4)
        y0, x0 = r * THUMB_SIZE + 2, c * THUMB_SIZE + 2
        canvas[y0 : y0 + cell.shape[0], x0 : x0 + cell.shape[1]] = cell
        cv2.rectangle(canvas, (x0 - 2, y0 - 2), (x0 + cell.shape[1], y0 + cell.shape[0]), (120, 120, 120), 1)
        cv2.putText(
            canvas, f"#{idx}", (x0 + 4, y0 + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2, cv2.LINE_AA
        )
    return canvas


def main() -> None:
    config = load_camera_config()
    seg_cfg = config["segmentation"]
    hsv_lower = tuple(seg_cfg["hsv_lower"])
    hsv_upper = tuple(seg_cfg["hsv_upper"])

    OUTPUT_DATASET_DIR.mkdir(parents=True, exist_ok=True)
    CONTACT_SHEET_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    images = find_dataset_images()
    print(f"Found {len(images)} source images under {DATA_DIR}\n")

    manifest_rows = []
    sheet_groups: dict[str, list[tuple[int, np.ndarray]]] = {}
    global_idx = 0
    seg_fail_count = 0

    for path in images:
        grade, capture_type = parse_grade_and_capture(path.parent.name)
        global_idx += 1

        try:
            bgr = downscale_for_processing(load_image_bgr(str(path)))
        except Exception as exc:
            print(f"  [{global_idx}] SKIP unreadable {path}: {exc}")
            manifest_rows.append(
                {"index": global_idx, "grade": grade, "capture_type": capture_type,
                 "source_path": str(path), "output_path": "", "segmented": False,
                 "width": "", "height": "", "area_px": ""}
            )
            continue

        h, w = bgr.shape[:2]
        region = segment_tile(
            bgr, hsv_lower=hsv_lower, hsv_upper=hsv_upper,
            min_area_px=(h * w) * 0.03, morph_kernel_size=seg_cfg["morph_kernel_size"],
        )

        out_group_key = f"{grade}_{capture_type}"
        if region is None:
            seg_fail_count += 1
            manifest_rows.append(
                {"index": global_idx, "grade": grade, "capture_type": capture_type,
                 "source_path": str(path), "output_path": "", "segmented": False,
                 "width": "", "height": "", "area_px": ""}
            )
            # Still put the (unsegmented) full downscaled frame on the contact
            # sheet, boxed differently, so segmentation failures are visible
            # for review too rather than silently dropped.
            sheet_groups.setdefault(out_group_key, []).append((global_idx, bgr))
            continue

        grade_dir = OUTPUT_DATASET_DIR / grade
        grade_dir.mkdir(parents=True, exist_ok=True)
        out_path = grade_dir / f"{capture_type}__{path.stem}.jpg"
        cv2.imwrite(str(out_path), region.cropped_bgr)

        manifest_rows.append(
            {"index": global_idx, "grade": grade, "capture_type": capture_type,
             "source_path": str(path), "output_path": str(out_path), "segmented": True,
             "width": region.bbox[2], "height": region.bbox[3], "area_px": region.area_px}
        )
        sheet_groups.setdefault(out_group_key, []).append((global_idx, region.cropped_bgr))

    with open(MANIFEST_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(manifest_rows[0].keys()))
        writer.writeheader()
        writer.writerows(manifest_rows)

    print(f"Segmentation: {len(images) - seg_fail_count}/{len(images)} cropped successfully "
          f"({seg_fail_count} failed - included on contact sheets unsegmented for review)\n")

    sheet_count = 0
    for group_key, thumbs in sorted(sheet_groups.items()):
        for chunk_start in range(0, len(thumbs), TILES_PER_SHEET):
            chunk = thumbs[chunk_start : chunk_start + TILES_PER_SHEET]
            sheet = build_contact_sheet(chunk)
            sheet_num = chunk_start // TILES_PER_SHEET + 1
            sheet_path = CONTACT_SHEET_DIR / f"{group_key}_sheet{sheet_num:02d}.jpg"
            cv2.imwrite(str(sheet_path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 90])
            sheet_count += 1

    print(f"Wrote {sheet_count} contact sheets to {CONTACT_SHEET_DIR}")
    print(f"Wrote manifest ({len(manifest_rows)} rows) to {MANIFEST_CSV}")
    print(f"Cropped tiles saved under {OUTPUT_DATASET_DIR}/<grade>/<capture_type>__<name>.jpg")

    print("\n=== Per-group counts ===")
    for group_key in sorted(sheet_groups.keys()):
        total = sum(1 for r in manifest_rows if f"{r['grade']}_{r['capture_type']}" == group_key)
        seg = sum(1 for r in manifest_rows if f"{r['grade']}_{r['capture_type']}" == group_key and r["segmented"])
        print(f"  {group_key:16s} {seg}/{total} segmented")


if __name__ == "__main__":
    main()
