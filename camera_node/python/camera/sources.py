"""Where video comes from and goes to: recording folders, the files that can be
replayed, and the rule for which paths the dashboard may open.

Pure path/metadata logic with no camera and no OpenCV, so it is testable on any
machine. The capture and recording themselves live in capture.py / recorder.py.

Layout of a recording (config.yaml `recording.output_dir`, under the gitignored
data/ folder, named by the date and time it started):

    data/recordings/2026-10-11_14-30-05/
        video.mp4        raw, un-annotated camera frames
        meta.json        source, fps, size, frame count, start/end time

Raw frames are stored, not the annotated feed, so a recording can be replayed
through the whole pipeline later and give the result the live run would have.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

# camera/sources.py -> camera/ -> python/ -> camera_node/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]

VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".m4v"}
# A recording folder holds exactly one of these; mp4 first, avi is the fallback
# codec's container (see recorder.py).
RECORDING_VIDEO_NAMES = ("video.mp4", "video.avi")
META_NAME = "meta.json"


def recordings_root(config: dict) -> Path:
    """config.yaml's recording.output_dir, anchored to the repo root so the
    location does not depend on the directory a script was launched from."""
    return REPO_ROOT / config["recording"]["output_dir"]


def allowed_roots(config: dict) -> list[Path]:
    """The only places the dashboard may open video files from. The dashboard
    is served on 0.0.0.0 (reachable over WiFi), so "open any path on this
    machine" would be a way to probe the whole disk; this keeps it to data/."""
    return [(REPO_ROOT / "data").resolve(), recordings_root(config).resolve()]


def recording_name(now: datetime) -> str:
    """Folder name for a recording started at `now`: 2026-10-11_14-30-05.
    Sorts chronologically as plain text, and contains nothing a filesystem
    objects to (no colons)."""
    return now.strftime("%Y-%m-%d_%H-%M-%S")


def new_recording_dir(root: Path, now: datetime) -> Path:
    """Create and return a fresh recording folder under `root`. Two recordings
    started within the same second get a _2, _3 ... suffix rather than one
    silently overwriting the other."""
    root.mkdir(parents=True, exist_ok=True)
    base = recording_name(now)
    for n in range(1, 1000):
        candidate = root / (base if n == 1 else f"{base}_{n}")
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            continue
    raise RuntimeError(f"could not create a recording folder for {base} under {root}")


def find_recording_video(folder: Path) -> Optional[Path]:
    for name in RECORDING_VIDEO_NAMES:
        path = folder / name
        if path.is_file():
            return path
    return None


def read_meta(folder: Path) -> dict:
    """meta.json of a recording folder, or {} if missing/corrupt -- a half-written
    recording (the app was killed mid-recording) must still be listable."""
    try:
        with open(folder / META_NAME, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def describe_recording(folder: Path) -> Optional[dict]:
    """One row for the dashboard's file list, or None if the folder holds no
    video."""
    video = find_recording_video(folder)
    if video is None:
        return None
    meta = read_meta(folder)
    duration = meta.get("duration_s")
    # "2026-10-11_14-30-05" -> "2026-10-11 14:30:05": the date keeps its dashes,
    # only the time part gets colons (and a _2 collision suffix is dropped).
    date_part, _, time_part = folder.name.partition("_")
    parts = [f"{date_part} {time_part[:8].replace('-', ':')}"]
    if duration is not None:
        parts.append(f"{duration:.1f} s")
    if meta.get("width") and meta.get("height"):
        parts.append(f"{meta['width']}×{meta['height']}")
    return {
        "id": folder.name,
        "label": " · ".join(parts),
        "path": str(video),
        "duration_s": duration,
        "frames": meta.get("frames"),
        "source": meta.get("source"),
        # recorder.py writes status "recording" at the start and "complete" on a
        # clean stop; anything else (no meta, "recording") means the app was
        # killed mid-recording. Still listed and playable, just flagged.
        "complete": meta.get("status") == "complete",
    }


def list_recordings(root: Path) -> list[dict]:
    """Every recording under `root`, newest first."""
    if not root.is_dir():
        return []
    rows = [describe_recording(p) for p in sorted(root.iterdir(), reverse=True) if p.is_dir()]
    return [r for r in rows if r is not None]


def list_media_files(config: dict) -> list[dict]:
    """Loose video files under data/ (e.g. footage copied in by hand), newest
    first, excluding the recordings folder which has its own listing."""
    data_dir = (REPO_ROOT / "data").resolve()
    rec_root = recordings_root(config).resolve()
    if not data_dir.is_dir():
        return []
    found = []
    for path in data_dir.rglob("*"):
        if path.suffix.lower() not in VIDEO_SUFFIXES or not path.is_file():
            continue
        try:
            path.resolve().relative_to(rec_root)
            continue  # inside the recordings folder
        except ValueError:
            pass
        found.append(path)
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {"path": str(p), "label": str(p.relative_to(data_dir)).replace("\\", "/"),
         "size_mb": round(p.stat().st_size / 1_048_576, 1)}
        for p in found
    ]


def resolve_media_path(path_str: str, roots: list[Path]) -> Path:
    """Turn a user-supplied path into a safe, existing video file, or raise
    ValueError saying why not.

    Relative paths are taken relative to the repo root. The result must be a
    real file with a video extension and must live under one of `roots` after
    symlinks and ".." are resolved -- so "data/../config.yaml" and a symlink
    pointing out of data/ are both refused.
    """
    if not path_str or not str(path_str).strip():
        raise ValueError("no file path given")
    candidate = Path(str(path_str).strip().strip('"'))
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError(f"file not found: {path_str}") from None
    if not resolved.is_file():
        raise ValueError(f"not a file: {path_str}")
    if resolved.suffix.lower() not in VIDEO_SUFFIXES:
        raise ValueError(
            f"not a video file ({resolved.suffix or 'no extension'}); expected one of {sorted(VIDEO_SUFFIXES)}"
        )
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return resolved
        except ValueError:
            continue
    raise ValueError("only files inside the data/ folder can be opened")
