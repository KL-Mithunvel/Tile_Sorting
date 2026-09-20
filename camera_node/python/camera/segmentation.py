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


def segment_tile(
    frame_bgr: np.ndarray,
    hsv_lower: tuple[int, int, int],
    hsv_upper: tuple[int, int, int],
    min_area_px: float,
    morph_kernel_size: int = 5,
) -> Optional[TileRegion]:
    """Find the largest tile-colored region in a frame.

    Returns None if nothing large enough matches the given HSV range.
    """
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(hsv_lower, dtype=np.uint8), np.array(hsv_upper, dtype=np.uint8))

    if morph_kernel_size > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (morph_kernel_size, morph_kernel_size))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    largest = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(largest)
    if area < min_area_px:
        return None

    x, y, w, h = cv2.boundingRect(largest)
    return TileRegion(
        bbox=(x, y, w, h),
        area_px=float(area),
        contour=largest,
        mask=mask,
        cropped_bgr=frame_bgr[y : y + h, x : x + w].copy(),
    )
