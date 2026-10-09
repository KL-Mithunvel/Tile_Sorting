"""Pure belt region-of-interest: where in the frame a tile can possibly be.

The camera looks straight down at the conveyor, so everything outside the belt
(the wooden bench, a person's feet, the rails) can never be a tile -- yet all of
it is searched by colour, and some of it (wood, leather) is tile-coloured. On
the real belt clip a sandal beside the belt, and the wood bench, were both
picked up as "the tile". Restricting segmentation to the belt removes that whole
class of false positive at the source, instead of rejecting it after the fact.

Three modes (config.yaml `segmentation.belt_roi.mode`):

* "off"   -- search the whole frame (bench work, no belt in view).
* "fixed" -- a normalised rectangle. The right answer on the real rig, where the
             camera is bolted above the belt and the belt never moves in frame.
* "color" -- find the belt by its own hue each frame. Survives a hand-held
             camera (the recorded clip drifts ~100 px sideways), at the cost of
             depending on the belt having a distinctive colour.

No I/O: plain numpy images in, a plain tuple out, so it is synthetic-testable.
"""

from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

ROI = tuple[int, int, int, int]  # x0, y0, x1, y1 in pixels, x1/y1 exclusive

_VALID_MODES = {"off", "fixed", "color"}


def find_belt_roi(frame_bgr: np.ndarray, cfg: Optional[dict]) -> Optional[ROI]:
    """The belt's bounding rectangle in this frame, or None.

    None means "no ROI applies" ONLY for mode "off" / a missing config. For
    "fixed" and "color" there is always an answer, except that "color" returns
    None when no belt-coloured area is visible -- and callers must then treat
    that as *no tile can be seen*, not as "search everywhere". See
    `roi_applies()`.
    """
    if not cfg:
        return None
    mode = cfg.get("mode", "off")
    if mode not in _VALID_MODES:
        raise ValueError(f"belt_roi.mode must be one of {sorted(_VALID_MODES)}, got {mode!r}")
    if mode == "off":
        return None

    frame_h, frame_w = frame_bgr.shape[:2]

    if mode == "fixed":
        rect = cfg.get("rect")
        if not rect or len(rect) != 4:
            raise ValueError("belt_roi.mode 'fixed' needs rect: [x0, y0, x1, y1] (0.0-1.0)")
        x0, y0, x1, y1 = rect
        if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
            raise ValueError(f"belt_roi.rect must satisfy 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1, got {rect}")
        return (int(x0 * frame_w), int(y0 * frame_h), int(round(x1 * frame_w)), int(round(y1 * frame_h)))

    # mode == "color"
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv,
        np.array(cfg["hsv_lower"], dtype=np.uint8),
        np.array(cfg["hsv_upper"], dtype=np.uint8),
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # Union of every sizeable belt-coloured area, not just the largest: a tile
    # lying across the whole belt splits the green into an upper and a lower
    # piece, and the largest piece alone would cut the tile out of its own ROI.
    min_area = cfg.get("min_area_px", 2000)
    boxes = [cv2.boundingRect(c) for c in contours if cv2.contourArea(c) >= min_area]
    if not boxes:
        return None

    margin = int(cfg.get("margin_px", 25))  # a tile can overhang the belt edge
    x0 = min(x for x, _, _, _ in boxes)
    y0 = min(y for _, y, _, _ in boxes)
    x1 = max(x + w for x, _, w, _ in boxes)
    y1 = max(y + h for _, y, _, h in boxes)
    return (max(0, x0 - margin), max(0, y0 - margin), min(frame_w, x1 + margin), min(frame_h, y1 + margin))


def roi_applies(cfg: Optional[dict]) -> bool:
    """True when a belt ROI is configured at all (mode is not "off")."""
    return bool(cfg) and cfg.get("mode", "off") != "off"
