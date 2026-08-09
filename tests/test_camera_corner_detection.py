import numpy as np
import pytest

from camera.corner_detection import detect_broken_corner

MIN_FILL_RATIO = 0.92


def contour_from_points(points: list[tuple[int, int]]) -> np.ndarray:
    return np.array([[p] for p in points], dtype=np.int32)


def test_intact_rectangle_is_not_broken():
    contour = contour_from_points([(0, 0), (100, 0), (100, 100), (0, 100)])

    result = detect_broken_corner(contour, min_fill_ratio=MIN_FILL_RATIO)

    assert not result.corner_broken
    assert result.fill_ratio > 0.98
    assert result.missing_area_px < 200


def test_clipped_corner_is_detected_as_broken():
    # Same 100x100 square with the top-right corner cut off by a 50x50 triangle.
    contour = contour_from_points([(0, 0), (50, 0), (100, 50), (100, 100), (0, 100)])

    result = detect_broken_corner(contour, min_fill_ratio=MIN_FILL_RATIO)

    assert result.corner_broken
    assert result.fill_ratio < MIN_FILL_RATIO
    assert result.missing_area_px > 500


def test_detect_broken_corner_rejects_degenerate_contour():
    contour = contour_from_points([(0, 0), (0, 0), (0, 0)])
    with pytest.raises(ValueError):
        detect_broken_corner(contour, min_fill_ratio=MIN_FILL_RATIO)
