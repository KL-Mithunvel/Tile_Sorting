"""Pure broken-corner detection: how much of the tile's own bounding
rectangle is missing, and how deep that missing region reaches into the
tile.

No camera I/O — operates on a tile contour (e.g. TileRegion.contour from
segmentation.py), so it's unit-testable with synthetic contours. A tile is
expected to be roughly rectangular; a broken/chipped corner removes material
near one corner, which drops the ratio of (actual contour area) to (area of
the tile's own minimum-area bounding rectangle) below what an intact tile
would show.

fill_ratio alone under-catches diagonal/triangular chips — the realistic way
ceramic actually breaks — because a triangular chip only removes half the
area of an axis-aligned square notch reaching the same distance into the
tile (area scales with the square of how far a triangular chip reaches; area
ratio doesn't reflect that linear reach directly). Confirmed empirically: a
chip whose two legs each span half the tile's side length only drops
fill_ratio to ~0.87 — above the production min_fill_ratio (0.83, itself
derived from real photos, see config.yaml) — so a chip reaching halfway
across the tile's edge would not be flagged by fill_ratio alone.

missing_depth_px (and missing_extent_fraction, its scale-independent form)
measures how far the *deepest point* of the gap between the actual contour
and the ideal rectangle reaches, via a distance transform, and is used as a
second, independent broken-corner trigger. Deliberately NOT the gap
region's bounding box: an intact tile is never pixel-perfectly identical to
its own ideal rotated rect (segmentation roughness, a real tile's corners
are never perfectly sharp, float-to-int rounding of the rect's corners), and
that mismatch forms one thin band running the *whole* perimeter — a single
connected ring whose bounding box spans nearly the entire tile despite being
1-2px of harmless noise everywhere (confirmed empirically: this measured a
~50% "extent" on known-intact photos when tried). A thin ring is never far
from the true boundary anywhere along it, so its distance-transform depth
stays small (confirmed: ~3% on the same photos) while a genuine chip — many
pixels thick, not a sliver — has real depth (confirmed: ~35% on a synthetic
chip reaching halfway across a tile's edge).

Still does not try to localize *which* corner is broken — the deepest
missing point's position is a good proxy (implies a corner), but full
per-corner localization would need real broken-tile photos to validate
against (only one crack photo found so far, no broken-corner photos yet —
see .CLAUDE/CLAUDE.md Known Technical Debt).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


@dataclass
class CornerResult:
    corner_broken: bool
    fill_ratio: float
    missing_area_px: float
    missing_depth_px: float  # how far the deepest point of the gap reaches from the tile's actual boundary
    missing_extent_fraction: float  # missing_depth_px / tile's own side length - scale-independent
    missing_area_sq_inches: Optional[float] = None
    missing_depth_inches: Optional[float] = None


def _missing_region_max_depth(contour: np.ndarray, rect) -> float:
    """Max distance-transform value inside the gap between the tile's actual
    contour and its own ideal (minimum-area) bounding rectangle — how far
    the deepest point of the missing region is from the nearest actual-tile
    pixel. 0.0 if there's no gap."""
    box = cv2.boxPoints(rect)
    x, y, w, h = cv2.boundingRect(box.astype(np.int32))
    if w <= 0 or h <= 0:
        return 0.0

    offset = np.array([x, y])
    ideal_mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(ideal_mask, [(box - offset).astype(np.int32)], 255)
    actual_mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(actual_mask, [(contour.reshape(-1, 2) - offset).astype(np.int32)], 255)
    missing_mask = cv2.subtract(ideal_mask, actual_mask)

    if not missing_mask.any():
        return 0.0
    dist = cv2.distanceTransform(missing_mask, cv2.DIST_L2, 5)
    return float(dist.max())


def detect_broken_corner(
    contour: np.ndarray,
    min_fill_ratio: float,
    max_missing_extent_fraction: float = 1.0,
    tile_size_inches: Optional[float] = None,
) -> CornerResult:
    """max_missing_extent_fraction defaults to 1.0 (never triggers, since the
    deepest possible gap can't exceed the tile's own side length) so existing
    fill_ratio-only callers are unaffected; config.yaml sets a real value for
    production."""
    contour_area = cv2.contourArea(contour)
    rect = cv2.minAreaRect(contour)
    (_, _), (rect_w, rect_h), _ = rect
    rect_area = rect_w * rect_h

    if rect_area <= 0:
        raise ValueError("detect_broken_corner: contour has zero-area bounding rectangle")

    fill_ratio = contour_area / rect_area
    missing_area_px = max(0.0, rect_area - contour_area)

    missing_depth_px = _missing_region_max_depth(contour, rect)
    tile_side_px = max(rect_w, rect_h)
    missing_extent_fraction = (missing_depth_px / tile_side_px) if tile_side_px > 0 else 0.0

    corner_broken = fill_ratio < min_fill_ratio or missing_extent_fraction > max_missing_extent_fraction

    missing_area_sq_inches = None
    missing_depth_inches = None
    if tile_size_inches is not None and tile_side_px > 0:
        # The tile's own detected side length (px) doubles as the scale
        # reference against its known real-world size - no separate
        # calibration step needed, and it stays correct even before the
        # live camera's distance/resolution is fixed.
        px_per_inch = tile_side_px / tile_size_inches
        missing_area_sq_inches = missing_area_px / (px_per_inch**2)
        missing_depth_inches = missing_depth_px / px_per_inch

    return CornerResult(
        corner_broken=corner_broken,
        fill_ratio=fill_ratio,
        missing_area_px=missing_area_px,
        missing_depth_px=missing_depth_px,
        missing_extent_fraction=missing_extent_fraction,
        missing_area_sq_inches=missing_area_sq_inches,
        missing_depth_inches=missing_depth_inches,
    )
