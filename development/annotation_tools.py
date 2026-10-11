"""Pure logic behind the tile annotator (annotate_tiles.py): finding the tiles in a
folder, remembering one human verdict per tile, and writing the verdicts out in the
review-sheet export format so `tune_from_labels.py` and `export_tile_dataset.py
--merge-labels` accept them unchanged. No Tkinter, no OpenCV -- unit-tested.

Two kinds of folder are understood:

  * a **tile dataset** (made by export_tile_dataset.py): has labels.csv, tiles/, context/.
    Each tile carries the pipeline's measurements, so labels from it can be used to tune
    the thresholds.
  * a **plain folder of pictures** (e.g. data/camera_captures/ pulled from the Pi): just
    images. Labels are kept, but there are no measurements, so they cannot tune anything
    until the pictures are put through the pipeline.

Verdicts are saved next to the pictures in `human_labels.json`, one entry per tile, after
every change.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# Same vocabulary as the review sheet (review_tools.LABELS / dataset_tools.HUMAN_LABELS),
# so a verdict means the same thing wherever it was made.
DEFECT_LABELS = ("fine", "chipped", "cracked", "both", "unsure")
# The model's cosmetic tiers (config grading.tier_map). A second, independent axis: a tile
# can be undamaged and still a 3B.
TIER_LABELS = ("3A", "3B", "4", "5")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}
LABELS_FILE = "human_labels.json"


@dataclass
class Tile:
    """One tile to label. `readings` is the pipeline's own opinion (empty for a plain
    folder); it is shown only on request, so the verdict is not biased by it."""
    id: str
    image: Path
    context: Optional[Path] = None
    video: str = ""
    seq: int = 0
    readings: dict = field(default_factory=dict)


@dataclass
class Verdict:
    label: Optional[str] = None   # one of DEFECT_LABELS
    tier: Optional[str] = None    # one of TIER_LABELS
    note: str = ""
    # Where the defect is: points on the TILE picture, in its own pixels
    # ({"x": 41, "y": 17, "kind": "crack" | "chip" | "defect"}). A crack is two or more
    # points along it; a chip, one at the damaged corner.
    marks: list = field(default_factory=list)

    def is_empty(self) -> bool:
        return self.label is None and self.tier is None and not self.note and not self.marks


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes")


def load_dataset(folder: Path) -> list[Tile]:
    """Tiles of an export_tile_dataset folder, in tile order. Rows whose picture is
    missing are skipped, not fatal (a partly copied folder is still labelable)."""
    tiles: list[Tile] = []
    with open(folder / "labels.csv", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            image = folder / row["tile_file"]
            if not image.is_file():
                continue
            context = folder / row["context_file"] if row.get("context_file") else None
            tiles.append(Tile(
                id=row["id"], image=image,
                context=context if context and context.is_file() else None,
                video=row.get("video", ""), seq=int(_to_float(row.get("seq"))),
                readings={
                    "grade": row.get("grade", ""),
                    "crack_severity": row.get("crack_severity", "none"),
                    "crack_length_px": _to_float(row.get("crack_length_px")),
                    "corner_broken": _to_bool(row.get("corner_broken")),
                    "fill": _to_float(row.get("corner_fill_ratio")),
                    "extent": _to_float(row.get("corner_missing_extent_fraction")),
                    "model_grade": row.get("model_grade") or None,
                    "model_confidence": _to_float(row.get("model_confidence"), None) if row.get("model_confidence") else None,
                    "frame": row.get("frame"), "time_s": row.get("time_s"),
                },
            ))
    return tiles


def load_images(folder: Path) -> list[Tile]:
    """Every picture directly in `folder`, oldest name first. Tiles saved by the live
    station are tile_<seq>_<timestamp>.jpg, so the sequence number is read from the name
    when it is there."""
    tiles = []
    for i, path in enumerate(sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES), start=1):
        m = re.match(r"tile_(\d+)_", path.name)
        tiles.append(Tile(id=path.stem, image=path, video=folder.name, seq=int(m.group(1)) if m else i))
    return tiles


def load_tiles(folder: Path) -> tuple[list[Tile], str]:
    """(tiles, kind) where kind is "dataset" or "images". Raises ValueError if the folder
    has nothing to label."""
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(f"{folder} is not a folder")
    if (folder / "labels.csv").is_file():
        tiles, kind = load_dataset(folder), "dataset"
    else:
        tiles, kind = load_images(folder), "images"
    if not tiles:
        raise ValueError(f"no tile pictures found in {folder}")
    return tiles, kind


class LabelStore:
    """One Verdict per tile id, written to <folder>/human_labels.json after every change.

    Reading an existing file is forgiving (a half-written or hand-edited file must not
    cost the session); writing is atomic (temp file + replace), so a crash mid-save keeps
    the previous labels."""

    def __init__(self, folder: Path, tiles: list[Tile]):
        self.folder = Path(folder)
        self.path = self.folder / LABELS_FILE
        self.tiles = tiles
        self.verdicts: dict[str, Verdict] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            items = data["items"]
        except (OSError, ValueError, KeyError, TypeError):
            return
        known = {t.id for t in self.tiles}
        for item in items:
            tid = item.get("id")
            if tid not in known:
                continue
            label, tier = item.get("label"), item.get("tier")
            marks = [
                {"x": int(m["x"]), "y": int(m["y"]), "kind": str(m.get("kind") or "defect")}
                for m in (item.get("marks") or [])
                if isinstance(m, dict) and "x" in m and "y" in m
            ]
            self.verdicts[tid] = Verdict(
                label if label in DEFECT_LABELS else None,
                tier if tier in TIER_LABELS else None,
                str(item.get("note") or ""),
                marks,
            )

    def get(self, tile_id: str) -> Verdict:
        return self.verdicts.get(tile_id, Verdict())

    def set_label(self, tile_id: str, label: Optional[str]) -> None:
        if label is not None and label not in DEFECT_LABELS:
            raise ValueError(f"unknown label {label!r}")
        self._update(tile_id, label=label)

    def set_tier(self, tile_id: str, tier: Optional[str]) -> None:
        if tier is not None and tier not in TIER_LABELS:
            raise ValueError(f"unknown tier {tier!r}")
        self._update(tile_id, tier=tier)

    def set_note(self, tile_id: str, note: str) -> None:
        self._update(tile_id, note=note.strip())

    MARK_KINDS = ("crack", "chip", "defect")

    def add_mark(self, tile_id: str, x: int, y: int, kind: str = "defect") -> None:
        if kind not in self.MARK_KINDS:
            raise ValueError(f"unknown mark kind {kind!r}")
        v = self.verdicts.get(tile_id, Verdict())
        self._update(tile_id, marks=[*v.marks, {"x": int(x), "y": int(y), "kind": kind}])

    def remove_last_mark(self, tile_id: str) -> bool:
        v = self.verdicts.get(tile_id)
        if v is None or not v.marks:
            return False
        self._update(tile_id, marks=v.marks[:-1])
        return True

    def _update(self, tile_id: str, **changes) -> None:
        v = self.verdicts.get(tile_id, Verdict())
        for key, value in changes.items():
            setattr(v, key, value)
        if v.is_empty():
            self.verdicts.pop(tile_id, None)
        else:
            self.verdicts[tile_id] = v
        self.save()

    def counts(self) -> dict:
        labelled = [v for v in self.verdicts.values() if v.label]
        out = {"total": len(self.tiles), "labelled": len(labelled), "tiered": sum(1 for v in self.verdicts.values() if v.tier)}
        for name in DEFECT_LABELS:
            out[name] = sum(1 for v in labelled if v.label == name)
        return out

    def next_unlabelled(self, after: int = -1) -> Optional[int]:
        """Index of the first tile with no defect label after position `after`, wrapping
        round; None when every tile has one."""
        n = len(self.tiles)
        for step in range(1, n + 1):
            i = (after + step) % n
            if self.get(self.tiles[i].id).label is None:
                return i
        return None

    def export(self) -> dict:
        """The review-sheet export shape. `items` lists EVERY tile (unlabelled ones with
        label null), which is what tune_from_labels / --merge-labels already skip over."""
        items = []
        for t in self.tiles:
            v, r = self.get(t.id), t.readings
            item = {"id": t.id, "video": t.video, "seq": t.seq, "label": v.label, "tier": v.tier, "note": v.note or None}
            if v.marks:
                item["marks"] = v.marks
            if r:  # only a dataset tile has measurements; tune_from_labels needs these
                item.update({
                    "frame": r.get("frame"), "time_s": r.get("time_s"), "grade": r.get("grade"),
                    "fill": r.get("fill"), "extent": r.get("extent"), "corner_broken": r.get("corner_broken"),
                    "crack_severity": r.get("crack_severity"), "crack_length_px": r.get("crack_length_px"),
                })
            else:
                item["file"] = t.image.name
            items.append(item)
        return {
            "sheet": self.folder.name, "exported": datetime.now().isoformat(timespec="seconds"),
            "tool": "development/annotate_tiles.py", "items": items,
        }

    def save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.export(), indent=2), encoding="utf-8")
        tmp.replace(self.path)
