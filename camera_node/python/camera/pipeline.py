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
) -> TileRecord:
    crack = detect_cracks(
        region.cropped_bgr,
        canny_low=canny_low,
        canny_high=canny_high,
        min_crack_length_px=min_crack_length_px,
        min_aspect_ratio=min_aspect_ratio,
        minor_severity_max_length_px=minor_severity_max_length_px,
        blur_kernel_size=blur_kernel_size,
    )
    corner = detect_broken_corner(region.contour, min_fill_ratio=min_fill_ratio)
    grade = grade_tile(crack, corner)

    return TileRecord(
        seq=seq,
        timestamp=time.time(),
        crack=crack,
        corner=corner,
        grade=grade,
        tile_bgr=region.cropped_bgr,
    )
