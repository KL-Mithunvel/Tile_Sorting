"""Per-tile orchestration: wires segmentation + crack_detection +
corner_detection together into one result per tile. Pure glue over the pure
modules above — no camera I/O.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from camera.corner_detection import CornerResult, detect_broken_corner
from camera.crack_detection import CrackResult, detect_cracks
from camera.segmentation import TileRegion


@dataclass
class TileRecord:
    seq: int
    timestamp: float
    crack: CrackResult
    corner: CornerResult
    grade: str
    tile_bgr: np.ndarray = field(repr=False)
    # Set after construction, once camera/snapshot.py has persisted tile_bgr
    # to disk (see worker.py / process_video.py) — None if snapshot saving is
    # disabled or hasn't happened yet.
    snapshot_path: Optional[str] = None


def grade_tile(crack: CrackResult, corner: CornerResult) -> str:
    """First-pass rule-based grading — see project_charter.md §10.1 for the
    same style of rule used for the full multi-station system. This is the
    camera station's own local grade, not the master's fused final grade."""
    if corner.corner_broken or crack.severity == "major":
        return "Reject"
    if crack.severity == "minor":
        return "Grade B"
    return "Grade A"


def process_tile(
    seq: int,
    region: TileRegion,
    canny_low: int,
    canny_high: int,
    min_crack_length_px: float,
    min_aspect_ratio: float,
    minor_severity_max_length_px: float,
    blur_kernel_size: int,
    min_fill_ratio: float,
    border_margin_px: int = 0,
    max_missing_extent_fraction: float = 1.0,
    tile_size_inches: Optional[float] = None,
) -> TileRecord:
    crack = detect_cracks(
        region.cropped_bgr,
        canny_low=canny_low,
        canny_high=canny_high,
        min_crack_length_px=min_crack_length_px,
        min_aspect_ratio=min_aspect_ratio,
        minor_severity_max_length_px=minor_severity_max_length_px,
        blur_kernel_size=blur_kernel_size,
        border_margin_px=border_margin_px,
    )
    corner = detect_broken_corner(
        region.contour,
        min_fill_ratio=min_fill_ratio,
        max_missing_extent_fraction=max_missing_extent_fraction,
        tile_size_inches=tile_size_inches,
    )
    grade = grade_tile(crack, corner)

    return TileRecord(
        seq=seq,
        timestamp=time.time(),
        crack=crack,
        corner=corner,
        grade=grade,
        tile_bgr=region.cropped_bgr,
    )


def tile_record_to_dict(record: TileRecord) -> dict:
    """JSON/API-friendly view of a TileRecord — everything except the raw
    tile_bgr pixel array. Shared by the live dashboard (dashboard.py) and the
    offline video-file tool (process_video.py) so both report tiles the same
    way."""
    return {
        "seq": record.seq,
        "timestamp": record.timestamp,
        "grade": record.grade,
        "crack_detected": record.crack.crack_detected,
        "crack_length_px": round(record.crack.crack_length_px, 1),
        "crack_severity": record.crack.severity,
        "corner_broken": record.corner.corner_broken,
        "corner_fill_ratio": round(record.corner.fill_ratio, 3),
        "corner_missing_area_px": round(record.corner.missing_area_px, 1),
        "corner_missing_depth_px": round(record.corner.missing_depth_px, 1),
        "corner_missing_extent_fraction": round(record.corner.missing_extent_fraction, 3),
        "corner_missing_area_sq_inches": (
            round(record.corner.missing_area_sq_inches, 2)
            if record.corner.missing_area_sq_inches is not None
            else None
        ),
        "corner_missing_depth_inches": (
            round(record.corner.missing_depth_inches, 2) if record.corner.missing_depth_inches is not None else None
        ),
        "snapshot_path": record.snapshot_path,
    }
