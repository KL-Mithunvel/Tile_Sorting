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
