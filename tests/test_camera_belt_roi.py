import cv2
import numpy as np
import pytest

from camera.belt_roi import find_belt_roi, roi_applies
from camera.segmentation import locate_tile, segment_tile

BELT = (60, 140, 40)   # green-ish BGR belt mat
TILE = (30, 90, 180)   # orange tile
BENCH = (35, 85, 170)  # tile-coloured wood: same hue as the tile on purpose


def hsv_bounds(bgr, margin=10):
    px = cv2.cvtColor(np.uint8([[bgr]]), cv2.COLOR_BGR2HSV)[0][0]
    lo = tuple(max(0, int(c) - margin) for c in px)
    hi = (min(179, int(px[0]) + margin), min(255, int(px[1]) + margin), min(255, int(px[2]) + margin))
    return lo, hi


def scene(with_tile=True, belt_x=(120, 280)):
    """400x300 frame: tile-coloured bench on the left, green belt, optional tile on it."""
    f = np.full((300, 400, 3), (40, 40, 40), np.uint8)
    cv2.rectangle(f, (0, 0), (100, 299), BENCH, -1)                 # tile-coloured bench
    cv2.rectangle(f, (belt_x[0], 0), (belt_x[1], 299), BELT, -1)    # belt
    if with_tile:
        cv2.rectangle(f, (150, 100), (250, 200), TILE, -1)          # tile on the belt
    return f


def color_cfg(**kw):
    lo, hi = hsv_bounds(BELT, 15)
    return {"mode": "color", "hsv_lower": list(lo), "hsv_upper": list(hi), "margin_px": 10, "min_area_px": 500, **kw}


def seg_cfg(**belt):
    lo, hi = hsv_bounds(TILE, 12)
    return {"hsv_lower": list(lo), "hsv_upper": list(hi), "min_tile_area_px": 1000,
            "morph_kernel_size": 3, "belt_roi": belt or None}


class TestFindBeltRoi:
    def test_off_and_missing_config_mean_no_roi(self):
        f = scene()
        assert find_belt_roi(f, None) is None
        assert find_belt_roi(f, {"mode": "off"}) is None
        assert not roi_applies(None) and not roi_applies({"mode": "off"}) and roi_applies({"mode": "color"})

    def test_fixed_rect_scales_to_the_frame(self):
        assert find_belt_roi(scene(), {"mode": "fixed", "rect": [0.25, 0.0, 0.75, 1.0]}) == (100, 0, 300, 300)

    @pytest.mark.parametrize("rect", [None, [0, 0, 1], [0.5, 0, 0.5, 1], [0, 0, 1.2, 1]])
    def test_fixed_rejects_bad_rect(self, rect):
        with pytest.raises(ValueError):
            find_belt_roi(scene(), {"mode": "fixed", "rect": rect})

    def test_unknown_mode_rejected(self):
        with pytest.raises(ValueError, match="mode"):
            find_belt_roi(scene(), {"mode": "magic"})

    def test_color_finds_the_belt_with_margin(self):
        x0, y0, x1, y1 = find_belt_roi(scene(), color_cfg())
        assert abs(x0 - 110) <= 2 and abs(x1 - 290) <= 3   # belt 120..280 +/- margin 10
        assert (y0, y1) == (0, 300)

    def test_color_follows_a_drifting_belt(self):
        a = find_belt_roi(scene(belt_x=(120, 280)), color_cfg())
        b = find_belt_roi(scene(belt_x=(200, 360)), color_cfg())
        assert b[0] - a[0] >= 70

    def test_color_returns_none_when_no_belt_visible(self):
        assert find_belt_roi(np.full((300, 400, 3), (40, 40, 40), np.uint8), color_cfg()) is None

    def test_a_tile_splitting_the_belt_does_not_shrink_the_roi(self):
        f = scene(with_tile=False)
        cv2.rectangle(f, (110, 100), (290, 200), TILE, -1)   # tile wider than the belt, across it
        roi = find_belt_roi(f, color_cfg())
        assert roi[1] == 0 and roi[3] == 300                 # still the full belt height


class TestSegmentationUsesTheRoi:
    def test_without_roi_the_tile_coloured_bench_wins(self):
        # The bench (100x300) is bigger than the tile: colour alone picks the wrong thing.
        region, roi = locate_tile(scene(), seg_cfg())
        assert roi is None and region.bbox[0] < 100

    def test_with_roi_only_the_tile_on_the_belt_is_seen(self):
        region, roi = locate_tile(scene(), seg_cfg(**color_cfg()))
        assert roi is not None
        x, y, w, h = region.bbox
        assert 145 <= x <= 155 and abs(w - 101) <= 3

    def test_nothing_on_the_belt_means_no_tile_even_if_the_bench_matches(self):
        region, _ = locate_tile(scene(with_tile=False), seg_cfg(**color_cfg()))
        assert region is None

    def test_belt_not_visible_means_no_tile_not_search_everywhere(self):
        f = np.full((300, 400, 3), (40, 40, 40), np.uint8)
        cv2.rectangle(f, (150, 100), (250, 200), TILE, -1)
        region, roi = locate_tile(f, seg_cfg(**color_cfg()))
        assert region is None and roi is None

    def test_roi_keeps_full_frame_coordinates(self):
        region, _ = locate_tile(scene(), seg_cfg(**color_cfg()))
        assert region.mask.shape == (300, 400)

    def test_segment_tile_roi_argument_blanks_outside(self):
        lo, hi = hsv_bounds(TILE, 12)
        r = segment_tile(scene(), lo, hi, 1000, 3, roi=(120, 0, 280, 300))
        assert r is not None and r.bbox[0] >= 120
