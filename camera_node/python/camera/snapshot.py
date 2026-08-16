"""Persists a tile's isolated photo to disk, tagged with its sequence
number, so a departed tile's picture can be reviewed later (or reused for
re-labeling/training) instead of only living in memory for one process
lifetime.

I/O only — no detection logic (see .CLAUDE/CLAUDE.md Development Rule 1).
Shared by the live dashboard worker (worker.py) and the offline video-file
tool (process_video.py) so both save snapshots the same way.
"""

from __future__ import annotations

from pathlib import Path

import cv2

from camera.pipeline import TileRecord

# camera/snapshot.py -> camera/ -> python/ -> camera_node/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]


def resolve_output_dir(config: dict) -> Path:
    """output_dir in config.yaml is relative to the repo root (e.g.
    "data/camera_captures") so it lands under the gitignored data/ folder
    regardless of the current working directory a script was launched from."""
    return REPO_ROOT / config["capture_snapshots"]["output_dir"]


def save_tile_snapshot(record: TileRecord, output_dir: Path) -> Path:
    """Writes record.tile_bgr to <output_dir>/tile_<seq>_<timestamp>.jpg and
    returns the path. Does not mutate record — callers set
    record.snapshot_path themselves so this stays a pure I/O side effect."""
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"tile_{record.seq:05d}_{int(record.timestamp)}.jpg"
    path = output_dir / filename
    cv2.imwrite(str(path), record.tile_bgr)
    return path
