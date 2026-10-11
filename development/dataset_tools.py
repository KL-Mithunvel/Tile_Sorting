"""Pure helpers for the tile dataset export: stable ids, label rows, CSV, and merging a
human reviewer's labels back in. No cameras, no OpenCV, no video -- unit-tested.

A dataset folder holds one row per graded tile. The row's `id` is stable for a given
video and tile number, so a later run, a review-sheet export or a hand-edited file can
all be matched to the same tile.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Iterable, Optional

# Column order of labels.csv. `human_label` / `human_note` are empty until someone fills
# them (by hand, or with merge_human_labels from a review-sheet export).
CSV_COLUMNS = [
    "id", "video", "seq", "track_id", "frame", "time_s",
    "grade", "rule_grade", "crack_severity", "crack_detected", "crack_length_px",
    "corner_broken", "corner_fill_ratio", "corner_missing_extent_fraction", "corner_missing_area_px",
    "sharpness", "sharpness_candidates",
    "model_grade", "model_confidence", "model_station_grade", "model_used",
    "bbox_x", "bbox_y", "bbox_w", "bbox_h", "crop_w", "crop_h", "center_x", "center_y",
    "tile_file", "context_file", "human_label", "human_note",
]
HUMAN_LABELS = ("fine", "chipped", "cracked", "both", "unsure")


def video_stem(video_name: str) -> str:
    """"WIN_20261011_12_16_03_Pro.mp4" -> "WIN_20261011_12_16_03"; anything awkward
    (spaces, punctuation) becomes underscores so the id is safe as a file name."""
    stem = video_name.rsplit(".", 1)[0] if "." in video_name else video_name
    stem = re.sub(r"_Pro$", "", stem)
    stem = re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_")
    return stem or "video"


def make_id(video_name: str, seq: int) -> str:
    """Stable id for tile `seq` of a video: WIN_20261011_12_16_03_t006."""
    return f"{video_stem(video_name)}_t{int(seq):03d}"


def unique_ids(video_names: Iterable[str]) -> dict[str, str]:
    """video file name -> stem, guaranteed distinct. Two different files can sanitise to the
    same stem ("a b.mp4" and "a_b.mp4"); the second gets a _2 so their tiles cannot collide."""
    out: dict[str, str] = {}
    used: set[str] = set()
    for name in video_names:
        stem = video_stem(name)
        candidate, n = stem, 1
        while candidate in used:
            n += 1
            candidate = f"{stem}_{n}"
        used.add(candidate)
        out[name] = candidate
    return out


def build_row(record: dict, *, video: str, stem: str, frame: Optional[int], fps: float,
              bbox: tuple[int, int, int, int], tile_file: str, context_file: str) -> dict:
    """One labels row from a `tile_record_to_dict` result plus where the tile came from.
    Every key of CSV_COLUMNS is present; unknown values are None, not missing."""
    x, y, w, h = bbox
    row = {
        "id": f"{stem}_t{int(record['seq']):03d}",
        "video": video, "seq": record["seq"], "track_id": record.get("track_id"),
        "frame": frame, "time_s": round(frame / fps, 3) if frame is not None and fps else None,
        "grade": record["grade"], "rule_grade": record.get("rule_grade"),
        "crack_severity": record["crack_severity"], "crack_detected": record["crack_detected"],
        "crack_length_px": record["crack_length_px"],
        "corner_broken": record["corner_broken"], "corner_fill_ratio": record["corner_fill_ratio"],
        "corner_missing_extent_fraction": record["corner_missing_extent_fraction"],
        "corner_missing_area_px": record["corner_missing_area_px"],
        "sharpness": record.get("sharpness"), "sharpness_candidates": record.get("sharpness_candidates"),
        "model_grade": record.get("model_grade"), "model_confidence": record.get("model_confidence"),
        "model_station_grade": record.get("model_station_grade"), "model_used": record.get("model_used"),
        "bbox_x": x, "bbox_y": y, "bbox_w": w, "bbox_h": h,
        "crop_w": record.get("crop_w"), "crop_h": record.get("crop_h"),
        "center_x": record.get("center_x"), "center_y": record.get("center_y"),
        "tile_file": tile_file, "context_file": context_file,
        "human_label": None, "human_note": None,
    }
    assert list(row) == CSV_COLUMNS, "build_row and CSV_COLUMNS have drifted apart"
    return row


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: ("" if row.get(k) is None else row[k]) for k in CSV_COLUMNS})
    return buf.getvalue()


def merge_human_labels(rows: list[dict], export: dict) -> tuple[int, list[str], list[str]]:
    """Copy a review-sheet export's labels onto the matching rows (matched on video file
    name + tile number), in place. Returns (how many were applied, labels that matched no
    tile, labels that were not a recognised label). A label of None (left blank in the
    sheet) leaves the row alone."""
    if not isinstance(export, dict) or "items" not in export:
        raise ValueError("not a review-sheet export (no 'items')")
    index = {(r["video"], int(r["seq"])): r for r in rows}
    applied, unmatched, invalid = 0, [], []
    for item in export["items"]:
        label = item.get("label")
        if label is None:
            continue
        if label not in HUMAN_LABELS:
            invalid.append(f"{item.get('video')} #{item.get('seq')}: {label!r}")
            continue
        row = index.get((item.get("video"), int(item.get("seq", -1))))
        if row is None:
            unmatched.append(f"{item.get('video')} #{item.get('seq')}")
            continue
        row["human_label"] = label
        if item.get("note"):
            row["human_note"] = item["note"]
        applied += 1
    return applied, unmatched, invalid


def summarise(rows: list[dict]) -> dict:
    """Counts for the manifest and the console."""
    def count(key, value=None):
        return sum(1 for r in rows if (r.get(key) == value if value is not None else r.get(key)))
    return {
        "tiles": len(rows),
        "by_grade": {g: count("grade", g) for g in sorted({r["grade"] for r in rows})},
        "corner_broken": count("corner_broken"),
        "crack_flagged": sum(1 for r in rows if r["crack_severity"] != "none"),
        "human_labelled": count("human_label"),
    }
