"""Pure broken-corner detection: how much of the tile's own bounding
rectangle is missing.

No camera I/O — operates on a tile contour (e.g. TileRegion.contour from
segmentation.py), so it's unit-testable with synthetic contours. A tile is
expected to be roughly rectangular; a broken/chipped corner removes material
near one corner, which drops the ratio of (actual contour area) to (area of
the tile's own minimum-area bounding rectangle) below what an intact tile
would show. This gives a missing-area measurement directly, the same way
crack_detection.py measures crack length rather than just classifying.

Deliberately does not try to localize *which* corner is broken yet — that
would need convexity-defect analysis against the rectangle's corner points,
which needs real broken-tile photos to validate against (none available on
this machine yet, see .CLAUDE/CLAUDE.md Known Technical Debt). Detecting and
measuring "how much is missing" is the useful, validated part for now.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class CornerResult:
    corner_broken: bool
    fill_ratio: float
    missing_area_px: float


def detect_broken_corner(contour: np.ndarray, min_fill_ratio: float) -> CornerResult:
    contour_area = cv2.contourArea(contour)
    (_, _), (rect_w, rect_h), _ = cv2.minAreaRect(contour)
    rect_area = rect_w * rect_h

    if rect_area <= 0:
        raise ValueError("detect_broken_corner: contour has zero-area bounding rectangle")

    fill_ratio = contour_area / rect_area
    missing_area_px = max(0.0, rect_area - contour_area)

    return CornerResult(
        corner_broken=fill_ratio < min_fill_ratio,
        fill_ratio=fill_ratio,
        missing_area_px=missing_area_px,
    )
