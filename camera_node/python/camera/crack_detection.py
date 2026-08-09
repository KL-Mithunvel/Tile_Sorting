"""Pure crack-detection logic: grayscale + edge detection + shape filtering.

No camera I/O — operates on an already-isolated tile image (BGR numpy array,
e.g. TileRegion.cropped_bgr from segmentation.py), so it's unit-testable with
synthetic images. Approach, as requested: convert to grayscale, run an edge
detector (Canny), then keep only edge contours that are long *and* thin
(elongated) — that's what separates an actual crack from surface texture
noise or a compact defect like a broken corner (see corner_detection.py,
which handles that case separately).

This measures crack length directly from the image rather than classifying
against a trained model, on purpose: it gives an actual size/severity number
per tile instead of just a category, which is what this project needs (see
.CLAUDE/CLAUDE-LOG.md, 2026-08-07 — an earlier attempt at a pretrained
crack-classification model only produced a grade, not a measurement).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


@dataclass
class CrackResult:
    crack_detected: bool
    crack_length_px: float
    severity: str  # "none" | "minor" | "major"
    contour: Optional[np.ndarray]


def detect_cracks(
    tile_bgr: np.ndarray,
    canny_low: int,
    canny_high: int,
    min_crack_length_px: float,
    min_aspect_ratio: float,
    minor_severity_max_length_px: float,
    blur_kernel_size: int = 5,
) -> CrackResult:
    if tile_bgr.size == 0:
        raise ValueError("detect_cracks: tile_bgr is empty")

    gray = cv2.cvtColor(tile_bgr, cv2.COLOR_BGR2GRAY)
    k = max(1, blur_kernel_size | 1)  # GaussianBlur needs an odd kernel size
    blurred = cv2.GaussianBlur(gray, (k, k), 0)
    edges = cv2.Canny(blurred, canny_low, canny_high)

    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    best_contour = None
    best_length = 0.0
    for contour in contours:
        if len(contour) < 5:
            continue  # minAreaRect needs at least a handful of points to be meaningful
        (_, _), (rect_w, rect_h), _ = cv2.minAreaRect(contour)
        length = max(rect_w, rect_h)
        width = max(min(rect_w, rect_h), 1e-6)
        aspect_ratio = length / width

        if length < min_crack_length_px or aspect_ratio < min_aspect_ratio:
            continue
        if length > best_length:
            best_length = length
            best_contour = contour

    if best_contour is None:
        return CrackResult(crack_detected=False, crack_length_px=0.0, severity="none", contour=None)

    severity = "minor" if best_length < minor_severity_max_length_px else "major"
    return CrackResult(crack_detected=True, crack_length_px=best_length, severity=severity, contour=best_contour)
