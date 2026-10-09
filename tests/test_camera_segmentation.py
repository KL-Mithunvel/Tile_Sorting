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


# ---- shape gate (added 2026-10-09) -------------------------------------
# The real failure: a brown sandal merged with the wood rim beside the belt
# into a thin, ragged, tile-coloured blob and was graded as a tile.

TILE = (30, 90, 180)
GATE = dict(min_fill_ratio=0.6, min_aspect=0.5, max_aspect=2.0)


def seg(frame, **kw):
    lower, upper = hsv_bounds_for_bgr(TILE)
    return segment_tile(frame, hsv_lower=lower, hsv_upper=upper, min_area_px=1000, morph_kernel_size=3, **kw)


def test_gate_rejects_a_thin_sliver_that_colour_alone_accepts():
    frame = make_frame(300, 400)
    cv2.rectangle(frame, (200, 20), (232, 380), TILE, thickness=-1)  # 33x361 rim-like strip
    assert seg(frame) is not None          # colour + area alone: accepted (the bug)
    assert seg(frame, **GATE) is None      # shape gate: rejected


def test_gate_rejects_a_ragged_l_shaped_blob():
    frame = make_frame(300, 300)
    cv2.rectangle(frame, (50, 50), (250, 100), TILE, thickness=-1)   # an L: 200x50 + 50x150
    cv2.rectangle(frame, (50, 50), (100, 250), TILE, thickness=-1)
    assert seg(frame) is not None
    assert seg(frame, **GATE) is None      # fill ~0.45 of its bounding rectangle


def test_gate_keeps_a_normal_square_tile():
    frame = make_frame(300, 300)
    cv2.rectangle(frame, (80, 80), (220, 220), TILE, thickness=-1)
    assert seg(frame, **GATE) is not None


def test_gate_keeps_a_chipped_tile_so_the_corner_check_can_flag_it():
    frame = make_frame(300, 300)
    cv2.rectangle(frame, (80, 80), (220, 220), TILE, thickness=-1)
    cv2.fillPoly(frame, [np.array([[80, 80], [140, 80], [80, 140]])], (40, 40, 40))  # diagonal chip
    assert seg(frame, **GATE) is not None


def test_gate_picks_the_largest_region_that_passes_not_the_largest_overall():
    # A sliver bigger than the tile must not hide the real tile behind it.
    frame = make_frame(400, 500)
    cv2.rectangle(frame, (20, 20), (60, 480), TILE, thickness=-1)       # 41x461 sliver, bigger area
    cv2.rectangle(frame, (200, 150), (320, 270), TILE, thickness=-1)    # 121x121 tile
    region = seg(frame, **GATE)
    assert region is not None
    x, y, w, h = region.bbox
    assert x >= 190 and abs(w - 121) <= 3


def test_gate_arguments_default_to_off():
    frame = make_frame(300, 400)
    cv2.rectangle(frame, (200, 20), (232, 380), TILE, thickness=-1)
    assert seg(frame, min_fill_ratio=None, min_aspect=None, max_aspect=None) is not None
