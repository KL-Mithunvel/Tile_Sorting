"""Live labelling: save each graded tile with a HUMAN verdict, as it passes the camera.

The dashboard's recordings are raw video that has to be re-processed and labelled
afterwards, by judging blurry crops. A labelling session instead captures ground truth at
the moment it is known: the operator runs a batch of tiles they already know to be fine /
chipped / cracked past the line (or presses a key for the one that just crossed), and every
tile is saved as lossless PNGs with that verdict beside the pipeline's own readings.

A session is one folder, named like a recording (data/labelled/<date_time>/):

    tiles/<id>.png      the exact crop the checks and the model were given (lossless)
    context/<id>.png    the same tile with belt around it (lossless)
    frames/<id>.png     the whole frame it was graded in (lossless; config-switchable)
    labels.csv          one row per tile: pipeline readings + human_label / human_tier / human_note
    human_labels.json   the same verdicts in the review-sheet export format

The folder is deliberately readable by development/annotate_tiles.py (open it to review or
correct verdicts) and development/tune_from_labels.py (threshold advice) with no conversion.
This module re-states the few column names it needs rather than importing from
development/: camera_node must never import from dev-only tooling (same one-way rule as
grading_model.py).

Pure of cameras and threads apart from a lock; the file writes are the only I/O, so it is
tested with a temp folder.
"""

from __future__ import annotations

import csv
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from camera.sources import new_recording_dir

# Same vocabulary as development/annotation_tools.py and the review sheet.
LABELS = ("fine", "chipped", "cracked", "both", "unsure")
TIERS = ("3A", "3B", "4", "5")
LABELS_FILE = "human_labels.json"
CSV_FILE = "labels.csv"

CSV_COLUMNS = [
    "id", "video", "seq", "frame", "time_s",
    "grade", "rule_grade", "crack_severity", "crack_detected", "crack_length_px",
    "corner_broken", "corner_fill_ratio", "corner_missing_extent_fraction", "corner_missing_area_px",
    "sharpness", "model_grade", "model_confidence",
    "bbox_x", "bbox_y", "bbox_w", "bbox_h",
    "tile_file", "context_file", "frame_file",
    "human_label", "human_tier", "human_note",
]

_UNSET = object()


def context_crop(frame: np.ndarray, bbox: tuple[int, int, int, int], pad_fraction: float = 0.12, min_pad: int = 24) -> np.ndarray:
    """The tile with some belt around it, clipped to the frame."""
    x, y, w, h = bbox
    pad = max(min_pad, int(pad_fraction * max(w, h)))
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(frame.shape[1], x + w + pad), min(frame.shape[0], y + h + pad)
    return frame[y0:y1, x0:x1]


def _check_label(label) -> None:
    if label is not None and label not in LABELS:
        raise ValueError(f"label must be one of {', '.join(LABELS)} (or empty), got {label!r}")


def _check_tier(tier) -> None:
    if tier is not None and tier not in TIERS:
        raise ValueError(f"tier must be one of {', '.join(TIERS)} (or empty), got {tier!r}")


class LabelSession:
    UNSET = _UNSET  # "leave this field alone", as opposed to None = "clear it"

    def __init__(
        self,
        root: Path,
        now: Optional[datetime] = None,
        batch_label: Optional[str] = None,
        batch_tier: Optional[str] = None,
        save_full_frame: bool = True,
        source_label: str = "camera",
    ):
        _check_label(batch_label)
        _check_tier(batch_tier)
        self.folder = new_recording_dir(Path(root), now or datetime.now())
        for sub in ("tiles", "context") + (("frames",) if save_full_frame else ()):
            (self.folder / sub).mkdir(exist_ok=True)
        self.save_full_frame = save_full_frame
        self.source_label = source_label
        self._batch_label = batch_label
        self._batch_tier = batch_tier
        self._rows: list[dict] = []
        self._lock = threading.RLock()
        self._write()

    # ---- adding tiles ------------------------------------------------------

    def add_tile(self, record: dict, tile_bgr: np.ndarray, frame_bgr: np.ndarray, bbox: tuple[int, int, int, int]) -> str:
        """Save one graded tile. `record` is camera.pipeline.tile_record_to_dict's output;
        the current batch verdict (if any) is applied. Returns the tile's id."""
        seq = int(record["seq"])
        tid = f"{self.folder.name}_t{seq:03d}"
        tile_file, context_file = f"tiles/{tid}.png", f"context/{tid}.png"
        frame_file = f"frames/{tid}.png" if self.save_full_frame else ""
        cv2.imwrite(str(self.folder / tile_file), tile_bgr)
        cv2.imwrite(str(self.folder / context_file), context_crop(frame_bgr, bbox))
        if frame_file:
            cv2.imwrite(str(self.folder / frame_file), frame_bgr)
        x, y, w, h = bbox
        row = {
            "id": tid, "video": self.folder.name, "seq": seq,
            "frame": record.get("frame"), "time_s": record.get("time_s"),
            "grade": record.get("grade"), "rule_grade": record.get("rule_grade"),
            "crack_severity": record.get("crack_severity", "none"), "crack_detected": record.get("crack_detected", False),
            "crack_length_px": record.get("crack_length_px", 0.0),
            "corner_broken": record.get("corner_broken", False),
            "corner_fill_ratio": record.get("corner_fill_ratio"),
            "corner_missing_extent_fraction": record.get("corner_missing_extent_fraction"),
            "corner_missing_area_px": record.get("corner_missing_area_px"),
            "sharpness": record.get("sharpness"),
            "model_grade": record.get("model_grade"), "model_confidence": record.get("model_confidence"),
            "bbox_x": x, "bbox_y": y, "bbox_w": w, "bbox_h": h,
            "tile_file": tile_file, "context_file": context_file, "frame_file": frame_file,
            "human_label": None, "human_tier": None, "human_note": None,
        }
        with self._lock:
            row["human_label"], row["human_tier"] = self._batch_label, self._batch_tier
            self._rows.append(row)
            self._write()
        return tid

    # ---- verdicts ----------------------------------------------------------

    def set_batch(self, label=_UNSET, tier=_UNSET) -> None:
        """The verdict given to every tile that is saved from now on. None = no batch
        verdict (tiles come in unlabelled, to be keyed one at a time)."""
        with self._lock:
            if label is not _UNSET:
                _check_label(label)
                self._batch_label = label
            if tier is not _UNSET:
                _check_tier(tier)
                self._batch_tier = tier

    def set_verdict(self, seq: Optional[int] = None, label=_UNSET, tier=_UNSET, note=_UNSET) -> dict:
        """Set (or, with None, clear) a tile's verdict. `seq` defaults to the most recent
        tile. Pressing the same value again is the caller's business, not this module's.
        Raises ValueError for an unknown label/tier or a tile that is not in this session."""
        if label is not _UNSET:
            _check_label(label)
        if tier is not _UNSET:
            _check_tier(tier)
        with self._lock:
            row = self._find(seq)
            if label is not _UNSET:
                row["human_label"] = label
            if tier is not _UNSET:
                row["human_tier"] = tier
            if note is not _UNSET:
                row["human_note"] = (str(note).strip() or None) if note is not None else None
            self._write()
            return self._verdict(row)

    def _find(self, seq: Optional[int]) -> dict:
        if not self._rows:
            raise ValueError("no tile has been saved in this session yet")
        if seq is None:
            return self._rows[-1]
        for row in self._rows:
            if row["seq"] == seq:
                return row
        raise ValueError(f"tile #{seq} is not in this session")

    @staticmethod
    def _verdict(row: dict) -> dict:
        return {"seq": row["seq"], "id": row["id"], "label": row["human_label"],
                "tier": row["human_tier"], "note": row["human_note"], "grade": row["grade"]}

    # ---- reading back ------------------------------------------------------

    def summary(self) -> dict:
        with self._lock:
            return {
                "folder": self.folder.name,
                "path": str(self.folder),
                "batch_label": self._batch_label,
                "batch_tier": self._batch_tier,
                "count": len(self._rows),
                "labelled": sum(1 for r in self._rows if r["human_label"]),
                "last": self._verdict(self._rows[-1]) if self._rows else None,
            }

    # ---- files -------------------------------------------------------------

    def _write(self) -> None:
        """Rewrite both label files. Atomic (temp + replace) so a kill mid-save keeps the
        previous labels; called after every change so nothing is lost with the session."""
        csv_path = self.folder / CSV_FILE
        tmp = csv_path.with_suffix(".csv.tmp")
        with open(tmp, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, lineterminator="\n")
            writer.writeheader()
            for row in self._rows:
                writer.writerow({k: ("" if row.get(k) is None else row[k]) for k in CSV_COLUMNS})
        tmp.replace(csv_path)

        items = [
            {
                "id": r["id"], "video": r["video"], "seq": r["seq"], "label": r["human_label"],
                "tier": r["human_tier"], "note": r["human_note"], "frame": r["frame"], "time_s": r["time_s"],
                "grade": r["grade"], "fill": r["corner_fill_ratio"], "extent": r["corner_missing_extent_fraction"],
                "corner_broken": r["corner_broken"], "crack_severity": r["crack_severity"],
                "crack_length_px": r["crack_length_px"],
            }
            for r in self._rows
        ]
        payload = {
            "sheet": self.folder.name, "exported": datetime.now().isoformat(timespec="seconds"),
            "tool": "camera/label_session.py", "source": self.source_label, "items": items,
        }
        json_path = self.folder / LABELS_FILE
        tmp = json_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(json_path)
