import cv2
import numpy as np
import pytest

from camera.crack_detection import detect_cracks

DEFAULTS = dict(canny_low=50, canny_high=150, min_crack_length_px=40, min_aspect_ratio=4.0, minor_severity_max_length_px=120, blur_kernel_size=3)


def make_blank_tile(size: int = 200, gray_value: int = 150) -> np.ndarray:
    return np.full((size, size, 3), gray_value, dtype=np.uint8)


def test_detect_cracks_finds_a_drawn_line():
    tile = make_blank_tile()
    cv2.line(tile, (30, 100), (170, 100), (40, 40, 40), thickness=3)  # ~140px dark line

    result = detect_cracks(tile, **DEFAULTS)

    assert result.crack_detected
    assert result.crack_length_px > 80  # loose bound around the ~140px drawn line
    assert result.severity in ("minor", "major")
    assert result.contour is not None


def test_detect_cracks_finds_nothing_on_a_blank_tile():
    tile = make_blank_tile()

    result = detect_cracks(tile, **DEFAULTS)

    assert not result.crack_detected
    assert result.crack_length_px == 0.0
    assert result.severity == "none"
    assert result.contour is None


def test_detect_cracks_ignores_a_compact_blob_not_a_line():
    tile = make_blank_tile()
    # Diameter (~60px) clears min_crack_length_px, but a circle's aspect ratio
    # (~1.0) should still fail min_aspect_ratio — this is what tells a crack
    # apart from a compact defect like a broken corner (corner_detection.py).
    cv2.circle(tile, (100, 100), radius=30, color=(40, 40, 40), thickness=-1)

    result = detect_cracks(tile, **DEFAULTS)

    assert not result.crack_detected


def test_detect_cracks_severity_thresholds():
    short_tile = make_blank_tile()
    cv2.line(short_tile, (80, 100), (130, 100), (40, 40, 40), thickness=3)  # ~50px, short
    short_result = detect_cracks(short_tile, **DEFAULTS)
    assert short_result.crack_detected
    assert short_result.severity == "minor"

    long_tile = make_blank_tile()
    cv2.line(long_tile, (10, 100), (190, 100), (40, 40, 40), thickness=3)  # ~180px, long
    long_result = detect_cracks(long_tile, **DEFAULTS)
    assert long_result.crack_detected
    assert long_result.severity == "major"


def test_detect_cracks_rejects_empty_array():
    with pytest.raises(ValueError):
        detect_cracks(np.array([]), **DEFAULTS)


def test_detect_cracks_ignores_border_silhouette_with_margin():
    # Simulates segmentation's bounding-box crop, where the tile's own edge
    # against the background sits right along one side of the crop border -
    # a long, thin line that would otherwise look exactly like a crack (see
    # development/analyze_dataset.py: ~100% false-positive rate on real
    # photos before border_margin_px was added).
    tile = make_blank_tile()
    cv2.line(tile, (5, 3), (195, 3), (40, 40, 40), thickness=2)

    without_margin = detect_cracks(tile, **DEFAULTS)
    assert without_margin.crack_detected

    with_margin = detect_cracks(tile, **DEFAULTS, border_margin_px=10)
    assert not with_margin.crack_detected


def test_detect_cracks_still_finds_interior_line_with_margin():
    tile = make_blank_tile()
    cv2.line(tile, (30, 100), (170, 100), (40, 40, 40), thickness=3)

    result = detect_cracks(tile, **DEFAULTS, border_margin_px=10)

    assert result.crack_detected
    assert result.severity in ("minor", "major")
