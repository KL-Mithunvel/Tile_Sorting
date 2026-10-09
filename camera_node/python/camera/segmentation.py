"""Pure tile-isolation logic: find the tile in a frame and separate it from
the background (conveyor belt / bench).

No camera I/O here on purpose — everything operates on plain numpy image
arrays (as OpenCV represents them) so it can be unit-tested with synthetic
images on any machine, per the dev-machine-first rule in CLAUDE-COMMON.md.
The HSV range this looks for is a config value (see config.yaml), not
hardcoded, because it's tuned per tile material/lighting, not fixed logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from camera.belt_roi import find_belt_roi, roi_applies


@dataclass
class TileRegion:
    """The isolated tile found in one frame."""

    bbox: tuple[int, int, int, int]  # x, y, w, h in the source frame
    area_px: float
    contour: np.ndarray
    mask: np.ndarray  # full-frame binary mask, same size as the source frame
    cropped_bgr: np.ndarray  # source frame cropped to bbox

    @property
    def center(self) -> tuple[float, float]:
        """Centre of the bounding box, in source-frame pixels.

        Bounding-box centre rather than the contour's centroid on purpose: it
        is what the drawn box visually implies, and it does not shift when a
        corner is missing. A broken corner pulls the area centroid away from
        the tile's geometric middle, which would make a chipped tile trip the
        line trigger at a different moment than an intact one -- exactly the
        inconsistency the trigger exists to avoid.
        """
        x, y, w, h = self.bbox
        return (x + w / 2.0, y + h / 2.0)

    @property
    def centroid(self) -> tuple[float, float]:
        """Area centroid of the contour, in source-frame pixels.

        Not used by the line trigger (see `center`), but kept because it is
        the right anchor for anything measuring the tile's mass distribution,
        and falls back to the bbox centre for a degenerate contour.
        """
        m = cv2.moments(self.contour)
        if m["m00"] == 0:
            return self.center
        return (m["m10"] / m["m00"], m["m01"] / m["m00"])

    @property
    def center_normalized(self) -> tuple[float, float]:
        """`center` as fractions of frame width/height (0.0-1.0).

        This is what `line_trigger.LineCrossingDetector` consumes, so a line
        configured at 0.5 means the middle of the frame at any resolution.
        Frame size comes from `mask`, which is full-frame by construction.
        """
        frame_h, frame_w = self.mask.shape[:2]
        cx, cy = self.center
        return (cx / frame_w, cy / frame_h)


def is_tile_shaped(
    contour: np.ndarray,
    min_fill_ratio: Optional[float] = None,
    min_aspect: Optional[float] = None,
    max_aspect: Optional[float] = None,
) -> bool:
    """Is this colour-matched blob shaped like a tile?

    Colour alone cannot tell a tile from a brown sandal or the dark wood rim
    beside the belt: on the real belt clip a sandal merged with the rim into a
    68x291 px blob, 28% filled, and was graded as a tile. A tile is close to a
    filled rectangle, so two cheap checks separate them by a wide margin
    (tiles on that clip: fill 0.93-0.96, aspect 1.00-1.02):

    * fill ratio -- contour area / its own minAreaRect area. Low means ragged,
      L-shaped or hollow, not a tile.
    * aspect -- bounding-box width/height. Extreme means a sliver (a rail, a
      shadow edge, a rim), not a tile.

    Set deliberately LOOSER than `corner_detection.min_fill_ratio`: a chipped
    tile must still be segmented so the corner check can flag it. This gate
    only has to reject things that are not tiles at all. Any argument left None
    disables that check, so existing callers keep their old behaviour.
    """
    if min_fill_ratio is not None:
        (_, _), (rw, rh), _ = cv2.minAreaRect(contour)
        rect_area = rw * rh
        if rect_area <= 0 or cv2.contourArea(contour) / rect_area < min_fill_ratio:
            return False

    if min_aspect is not None or max_aspect is not None:
        _, _, w, h = cv2.boundingRect(contour)
        if h == 0:
            return False
        aspect = w / h
        if min_aspect is not None and aspect < min_aspect:
            return False
        if max_aspect is not None and aspect > max_aspect:
            return False

    return True


def segment_tiles(
    frame_bgr: np.ndarray,
    hsv_lower: tuple[int, int, int],
    hsv_upper: tuple[int, int, int],
    min_area_px: float,
    morph_kernel_size: int = 5,
    min_fill_ratio: Optional[float] = None,
    min_aspect: Optional[float] = None,
    max_aspect: Optional[float] = None,
    roi: Optional[tuple[int, int, int, int]] = None,
) -> list[TileRegion]:
    """Find EVERY tile-colored, tile-shaped region in a frame, largest first.

    Returning all of them (not just the biggest) is what makes tile identity
    possible: with two tiles in frame and areas within ~0.02% of each other,
    "the largest" flips between two physically different tiles on pure noise,
    so something downstream has to see both and keep them apart
    (see `region_tracker.py`).

    The shape arguments (see `is_tile_shaped`) are optional; a region that
    fails the gate is skipped, so a sandal bigger than a real tile cannot hide
    it. `roi` (x0, y0, x1, y1 pixels) restricts the search to the belt:
    anything outside it is blanked from the mask. The mask stays full-frame so
    every coordinate in a returned region is still in source-frame pixels.
    """
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(hsv_lower, dtype=np.uint8), np.array(hsv_upper, dtype=np.uint8))

    if roi is not None:
        x0, y0, x1, y1 = roi
        keep = np.zeros_like(mask)
        keep[max(0, y0) : y1, max(0, x0) : x1] = 255
        mask = cv2.bitwise_and(mask, keep)

    if morph_kernel_size > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (morph_kernel_size, morph_kernel_size))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    regions: list[TileRegion] = []
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        area = cv2.contourArea(contour)
        if area < min_area_px:
            break  # sorted descending: nothing after this is big enough
        if not is_tile_shaped(contour, min_fill_ratio, min_aspect, max_aspect):
            continue
        x, y, w, h = cv2.boundingRect(contour)
        regions.append(
            TileRegion(
                bbox=(x, y, w, h),
                area_px=float(area),
                contour=contour,
                mask=mask,
                cropped_bgr=frame_bgr[y : y + h, x : x + w].copy(),
            )
        )
    return regions


def segment_tile(*args, **kwargs) -> Optional[TileRegion]:
    """The largest region `segment_tiles()` finds, or None. Same arguments.

    Kept for callers that only want one tile (the offline calibration scripts,
    the model benchmark). The live pipeline uses `segment_tiles()` so it can
    tell two tiles apart.
    """
    regions = segment_tiles(*args, **kwargs)
    return regions[0] if regions else None


def segment_from_config(frame_bgr: np.ndarray, seg_cfg: dict) -> Optional[TileRegion]:
    """`locate_tile()` without the ROI -- for callers that only want the tile."""
    return locate_tile(frame_bgr, seg_cfg)[0]


def locate_tiles(
    frame_bgr: np.ndarray, seg_cfg: dict
) -> tuple[list[TileRegion], Optional[tuple[int, int, int, int]]]:
    """`segment_tiles()` driven by config.yaml's `segmentation` block, plus the
    belt ROI it searched (None when the ROI is off), so the live worker and
    the offline video processor cannot drift apart in which keys they pass and
    the dashboard can draw the ROI.

    If a belt ROI is configured but the belt cannot be seen this frame (colour
    mode, nothing belt-coloured in view) the answer is NO tile -- deliberately
    not "search the whole frame": that fallback is exactly what lets the bench
    and a sandal be graded as tiles whenever the belt is momentarily lost.
    """
    belt_cfg = seg_cfg.get("belt_roi")
    roi = find_belt_roi(frame_bgr, belt_cfg)
    if roi is None and roi_applies(belt_cfg):
        return [], None

    regions = segment_tiles(
        frame_bgr,
        hsv_lower=tuple(seg_cfg["hsv_lower"]),
        hsv_upper=tuple(seg_cfg["hsv_upper"]),
        min_area_px=seg_cfg["min_tile_area_px"],
        morph_kernel_size=seg_cfg["morph_kernel_size"],
        min_fill_ratio=seg_cfg.get("min_fill_ratio"),
        min_aspect=seg_cfg.get("min_aspect"),
        max_aspect=seg_cfg.get("max_aspect"),
        roi=roi,
    )
    return regions, roi


def locate_tile(
    frame_bgr: np.ndarray, seg_cfg: dict
) -> tuple[Optional[TileRegion], Optional[tuple[int, int, int, int]]]:
    """`locate_tiles()` reduced to its largest region."""
    regions, roi = locate_tiles(frame_bgr, seg_cfg)
    return (regions[0] if regions else None), roi
