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


def test_diagonal_chip_missed_by_fill_ratio_alone_is_caught_by_extent():
    # A 200x200 tile with a diagonal corner chip whose two legs each span
    # half the tile's side length - realistic for how ceramic actually
    # chips, unlike an axis-aligned square notch. Only removes 1/8 of the
    # tile's area, so fill_ratio stays above the production threshold
    # (~0.87 > 0.83) even though the chip reaches halfway across the edge -
    # this is the real gap max_missing_extent_fraction exists to close.
    contour = contour_from_points([(100, 0), (199, 0), (199, 199), (0, 199), (0, 100)])

    fill_ratio_only = detect_broken_corner(contour, min_fill_ratio=0.83)
    assert not fill_ratio_only.corner_broken  # confirms the gap actually exists

    with_extent_check = detect_broken_corner(contour, min_fill_ratio=0.83, max_missing_extent_fraction=0.22)
    assert with_extent_check.corner_broken
    assert with_extent_check.missing_extent_fraction > 0.3


def test_intact_rectangle_has_near_zero_missing_extent():
    contour = contour_from_points([(0, 0), (100, 0), (100, 100), (0, 100)])

    result = detect_broken_corner(contour, min_fill_ratio=MIN_FILL_RATIO, max_missing_extent_fraction=0.22)

    assert not result.corner_broken
    assert result.missing_extent_fraction < 0.05


def test_tile_size_inches_converts_missing_measurements():
    # Same diagonal chip as above, on a tile declared to be 9 inches per side.
    contour = contour_from_points([(100, 0), (199, 0), (199, 199), (0, 199), (0, 100)])

    result = detect_broken_corner(
        contour, min_fill_ratio=0.83, max_missing_extent_fraction=0.22, tile_size_inches=9.0
    )

    assert result.missing_area_sq_inches is not None
    assert result.missing_depth_inches is not None
    assert result.missing_area_sq_inches == pytest.approx(9.0 * 9.0 * (5000 / 39601), rel=0.05)
    assert 2.0 < result.missing_depth_inches < 4.0


def test_tile_size_inches_omitted_leaves_conversions_none():
    contour = contour_from_points([(0, 0), (100, 0), (100, 100), (0, 100)])

    result = detect_broken_corner(contour, min_fill_ratio=MIN_FILL_RATIO)

    assert result.missing_area_sq_inches is None
    assert result.missing_depth_inches is None
