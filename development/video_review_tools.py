"""Pure logic behind the video review tool (review_video.py): what a reviewer can mark
on a recording, where it is stored, and what it says about the pipeline's tile count.
No Tkinter, no OpenCV -- unit-tested.

Why this exists: the tile annotator only ever shows tiles the pipeline ALREADY picked, so
it cannot say "you missed one" or "that was not a tile". Those two errors change the
station's tile count and tally, which the crack/corner labels never reveal. A reviewer
scrubs the raw recording with the pipeline's picks drawn on it and marks:

    missed    a tile that crossed the line and was never graded (click where it is)
    false     a graded "tile" that is not a tile (a shoe, the table, a shadow)
    note      any frame worth coming back to (blurry, odd lighting, a double tile...)

and the file then gives a corrected count:  counted - false + missed.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

MARK_TYPES = ("missed", "false", "note")
# A "false detection" mark is attached to the graded tile nearest in time, but only if one
# is this close: pressing it on an empty stretch of belt must not silently pick a stranger.
DEFAULT_MATCH_FRAMES = 45


@dataclass
class Mark:
    type: str
    frame: int
    time_s: Optional[float] = None
    x: Optional[int] = None        # video pixels (missed tiles)
    y: Optional[int] = None
    seq: Optional[int] = None      # the pipeline tile this refers to (false detections)
    note: str = ""


@dataclass
class GradedTile:
    """One tile the pipeline graded: where in the video, and what it said."""
    seq: int
    frame: int
    bbox: tuple[int, int, int, int]
    grade: str = ""
    crack_severity: str = "none"
    corner_broken: bool = False
    track_id: Optional[int] = None


def nearest_graded(graded: list[GradedTile], frame: int, window: int = DEFAULT_MATCH_FRAMES) -> Optional[GradedTile]:
    """The graded tile closest in time to `frame`, or None if none is within `window`."""
    best, best_d = None, window + 1
    for g in graded:
        d = abs(g.frame - frame)
        if d < best_d:
            best, best_d = g, d
    return best


def video_signature(path: Path) -> dict:
    """What identifies a video file for caching: not a content hash (videos are
    hundreds of MB), but size + modification time, which changes on any re-save."""
    st = Path(path).stat()
    return {"name": Path(path).name, "size": st.st_size, "mtime": int(st.st_mtime)}


def review_path(video: Path) -> Path:
    """Where a video's marks are kept. A recording folder holds one video.mp4, so the file
    sits inside it; a loose video gets a sibling file."""
    video = Path(video)
    if video.stem == "video":
        return video.with_name("review_marks.json")
    return video.with_name(video.stem + ".review.json")


def cache_path(video: Path) -> Path:
    video = Path(video)
    if video.stem == "video":
        return video.with_name("pipeline_cache.json")
    return video.with_name(video.stem + ".pipeline.json")


def load_cache(video: Path) -> Optional[list[GradedTile]]:
    """The pipeline's picks from an earlier run on this exact file, or None when there is
    no cache, it is unreadable, or the video changed since."""
    path = cache_path(video)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("video") != video_signature(video):
            return None
        return [GradedTile(**{**t, "bbox": tuple(t["bbox"])}) for t in data["tiles"]]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_cache(video: Path, graded: list[GradedTile]) -> None:
    path = cache_path(video)
    payload = {
        "video": video_signature(video),
        "created": datetime.now().isoformat(timespec="seconds"),
        "tiles": [asdict(g) for g in graded],
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    tmp.replace(path)


@dataclass
class ReviewStore:
    """The marks for one video, saved to disk after every change."""
    video: Path
    fps: float
    frames: int
    graded: list[GradedTile] = field(default_factory=list)
    marks: list[Mark] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.video = Path(self.video)
        self.path = review_path(self.video)
        self._load()

    # ---- persistence ------------------------------------------------------
    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for m in data.get("marks", []):
                if m.get("type") in MARK_TYPES and isinstance(m.get("frame"), int):
                    self.marks.append(Mark(**{k: m.get(k) for k in Mark.__dataclass_fields__ if k in m}))
        except (OSError, ValueError, TypeError):
            pass  # a missing or damaged file is an empty review, not a failure

    def save(self) -> None:
        payload = {
            "video": self.video.name, "fps": self.fps, "frames": self.frames,
            "saved": datetime.now().isoformat(timespec="seconds"),
            "tool": "development/review_video.py",
            "summary": self.summary(),
            "graded": [asdict(g) for g in self.graded],
            "marks": [asdict(m) for m in sorted(self.marks, key=lambda m: m.frame)],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    # ---- editing ----------------------------------------------------------
    def _time(self, frame: int) -> Optional[float]:
        return round(frame / self.fps, 3) if self.fps else None

    def add_missed(self, frame: int, x: int, y: int, note: str = "") -> Mark:
        m = Mark("missed", int(frame), self._time(frame), int(x), int(y), note=note.strip())
        self.marks.append(m)
        self.save()
        return m

    def add_false(self, frame: int, window: int = DEFAULT_MATCH_FRAMES, note: str = "") -> Mark:
        """Mark the graded tile nearest `frame` as not being a tile. ValueError if none is
        near, or it has already been marked."""
        tile = nearest_graded(self.graded, frame, window)
        if tile is None:
            raise ValueError(f"no graded tile within {window} frames of frame {frame}")
        if any(m.type == "false" and m.seq == tile.seq for m in self.marks):
            raise ValueError(f"tile #{tile.seq} is already marked as a false detection")
        m = Mark("false", tile.frame, self._time(tile.frame), tile.bbox[0] + tile.bbox[2] // 2,
                 tile.bbox[1] + tile.bbox[3] // 2, seq=tile.seq, note=note.strip())
        self.marks.append(m)
        self.save()
        return m

    def add_note(self, frame: int, note: str) -> Mark:
        if not note.strip():
            raise ValueError("a note needs some text")
        m = Mark("note", int(frame), self._time(frame), note=note.strip())
        self.marks.append(m)
        self.save()
        return m

    def remove_near(self, frame: int, window: int = 5) -> Optional[Mark]:
        """Remove the mark closest to `frame` (within `window`); returns it, or None."""
        best = min((m for m in self.marks if abs(m.frame - frame) <= window),
                   key=lambda m: abs(m.frame - frame), default=None)
        if best is not None:
            self.marks.remove(best)
            self.save()
        return best

    def undo(self) -> Optional[Mark]:
        if not self.marks:
            return None
        m = self.marks.pop()
        self.save()
        return m

    # ---- what it says ------------------------------------------------------
    def summary(self) -> dict:
        counted = len(self.graded)
        missed = sum(1 for m in self.marks if m.type == "missed")
        false = sum(1 for m in self.marks if m.type == "false")
        return {
            "pipeline_counted": counted, "missed": missed, "false_detections": false,
            "corrected_count": counted - false + missed,
            "notes": sum(1 for m in self.marks if m.type == "note"),
        }
