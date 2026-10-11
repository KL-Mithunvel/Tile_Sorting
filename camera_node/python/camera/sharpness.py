"""How sharp is this tile in this frame? Used to choose the best frame to grade.

The tile is MOVING when it is photographed, so each frame is motion-blurred by
(speed x exposure time) -- ~7 px at the webcam's auto exposure on the sample
recordings -- and the blur differs from frame to frame (exposure jitter, video
compression). A hairline crack is 1-3 px wide; smear the picture by 7 px and it is
gone. Grading the sharpest frame near the line instead of whichever frame happened
to cross it recovers some of that.

The metric: the strength of edges ALONG the belt's direction, measured in a thin band
around the tile's outline. Motion along the belt smears exactly those edges (the
tile's leading and trailing edges) and leaves edges across the belt alone, so the
steepness of the former is a direct read on the blur. The 95th percentile of the
band's gradient magnitudes ignores a chipped or noisy stretch of outline.

Chosen by experiment, not assumption: real tile frames blurred synthetically by 0-30
px along the belt gave 1.00, 1.00, 0.96, 0.91, 0.87, 0.74, 0.57 (this metric, p75)
and 1.00, 0.97, 0.89, 0.81, 0.75, 0.67, 0.50 (p95: the one used) -- steadily
falling -- where the first idea, the Laplacian variance inside the tile, stayed flat
and was not even monotone (the tile's surface is nearly featureless).

Scores are only comparable between frames of the SAME tile (the contrast of the
outline differs between tiles), which is how the picker uses them.

Pure: arrays in, a number out.
"""

from __future__ import annotations

from typing import Any, Optional

import cv2
import numpy as np

_MIN_BAND_PIXELS = 50


def edge_sharpness(frame_bgr: np.ndarray, region: Any, belt_axis: Optional[str]) -> float:
    """Higher = sharper. 0.0 if the tile's outline is too small to measure.

    `belt_axis` is the way the belt runs in the frame ("vertical" = top-to-bottom,
    "horizontal" = left-to-right, as belt_roi reports it); only edges changing along
    it are used. None (direction unknown) uses edges in every direction.
    """
    x, y, w, h = region.bbox
    band_px = max(3, int(round(0.02 * max(w, h))))
    pad = band_px + 2
    fh, fw = frame_bgr.shape[:2]
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(fw, x + w + pad), min(fh, y + h + pad)
    if x1 <= x0 or y1 <= y0:
        return 0.0

    gray = cv2.cvtColor(frame_bgr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.float32)
    mask = (region.mask[y0:y1, x0:x1] > 0).astype(np.uint8)
    kernel = np.ones((2 * band_px + 1, 2 * band_px + 1), np.uint8)
    band = (cv2.dilate(mask, kernel) > 0) & ~(cv2.erode(mask, kernel) > 0)
    if int(band.sum()) < _MIN_BAND_PIXELS:
        return 0.0

    if belt_axis == "vertical":
        grad = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
    elif belt_axis == "horizontal":
        grad = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
    else:
        grad = cv2.magnitude(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
    return float(np.percentile(grad[band], 95))
