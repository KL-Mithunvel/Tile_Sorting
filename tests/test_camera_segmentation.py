import cv2
import numpy as np

from camera.segmentation import segment_tile


def make_frame(width: int = 200, height: int = 200, bg_bgr: tuple[int, int, int] = (40, 40, 40)) -> np.ndarray:
    return np.full((height, width, 3), bg_bgr, dtype=np.uint8)


def hsv_bounds_for_bgr(bgr: tuple[int, int, int], margin: int = 10) -> tuple[tuple, tuple]:
    """Derive a tight HSV range around a known BGR color, for deterministic tests."""
    hsv_pixel = cv2.cvtColor(np.uint8([[bgr]]), cv2.COLOR_BGR2HSV)[0][0]
    lower = tuple(max(0, int(c) - margin) for c in hsv_pixel)
    upper = tuple(min(255, int(c) + margin) for c in hsv_pixel)
    upper = (min(179, upper[0]), upper[1], upper[2])
    return lower, upper


def test_segment_tile_finds_rectangle():
    frame = make_frame()
    tile_bgr = (30, 90, 180)  # an orange/brown-ish color, away from hue 0/179 boundary
    cv2.rectangle(frame, (50, 60), (150, 140), tile_bgr, thickness=-1)  # 100x80 filled rect

    lower, upper = hsv_bounds_for_bgr(tile_bgr)
    region = segment_tile(frame, hsv_lower=lower, hsv_upper=upper, min_area_px=1000, morph_kernel_size=3)

    assert region is not None
    x, y, w, h = region.bbox
    assert abs(w - 100) <= 3
    assert abs(h - 80) <= 3
    assert region.area_px > 7000
    assert region.cropped_bgr.shape[:2] == (h, w)


def test_segment_tile_returns_none_when_nothing_matches():
    frame = make_frame(bg_bgr=(40, 40, 40))
    # HSV range that the uniform background frame can't satisfy.
    region = segment_tile(frame, hsv_lower=(0, 200, 200), hsv_upper=(10, 255, 255), min_area_px=100)
    assert region is None


def test_segment_tile_rejects_regions_smaller_than_min_area():
    frame = make_frame()
    tile_bgr = (30, 90, 180)
    cv2.rectangle(frame, (90, 90), (110, 110), tile_bgr, thickness=-1)  # small 20x20 = 400px

    lower, upper = hsv_bounds_for_bgr(tile_bgr)
    region = segment_tile(frame, hsv_lower=lower, hsv_upper=upper, min_area_px=1000, morph_kernel_size=3)
    assert region is None
