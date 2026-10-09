"""Tile identity across frames (step 4, 2026-10-09).

The scenario that matters is the real clip's: two tiles on the belt with
near-identical areas, so "the largest region" flips between them every few
frames. These sequences are what a tracker must survive.
"""
from dataclasses import dataclass

import pytest

from camera.region_tracker import LineCrossingTracker, RegionTracker

LINE = dict(position=0.5, orientation="horizontal", direction="both", hysteresis=0.0)


@dataclass
class Fake:
    """Stands in for a TileRegion: all the tracker needs is a normalised centre."""
    center_normalized: tuple


def at(y, x=0.5):
    return Fake((x, y))


class TestRegionTracker:
    def test_rejects_bad_arguments(self):
        with pytest.raises(ValueError):
            RegionTracker(max_match_distance=0)
        with pytest.raises(ValueError):
            RegionTracker(max_missed=-1)

    def test_a_moving_tile_keeps_its_id(self):
        t = RegionTracker(max_match_distance=0.15)
        ids = [t.update([(0.5, y)])[0] for y in (0.1, 0.15, 0.2, 0.25, 0.3)]
        assert len(set(ids)) == 1

    def test_two_tiles_get_two_ids_and_keep_them_whatever_order_they_arrive_in(self):
        t = RegionTracker(max_match_distance=0.15)
        a1, b1 = t.update([(0.5, 0.2), (0.5, 0.8)])
        assert a1 != b1
        # Next frame the segmenter lists them in the OPPOSITE order (the
        # "largest first" ordering flips on noise). Identities must not swap.
        b2, a2 = t.update([(0.5, 0.83), (0.5, 0.23)])
        assert (a2, b2) == (a1, b1)

    def test_a_new_tile_entering_gets_a_new_id(self):
        t = RegionTracker()
        (a,) = t.update([(0.5, 0.5)])
        a2, b = t.update([(0.5, 0.52), (0.5, 0.05)])
        assert a2 == a and b != a

    def test_a_tile_unseen_for_a_few_frames_keeps_its_id(self):
        t = RegionTracker(max_missed=3)
        (a,) = t.update([(0.5, 0.4)])
        for _ in range(3):
            assert t.update([]) == []
        assert t.update([(0.5, 0.43)]) == [a]

    def test_a_tile_unseen_too_long_is_dropped_and_reported_lost(self):
        t = RegionTracker(max_missed=2)
        (a,) = t.update([(0.5, 0.4)])
        t.update([]); t.update([])
        assert t.lost == []
        t.update([])
        assert t.lost == [a] and t.active_ids == []
        (b,) = t.update([(0.5, 0.4)])
        assert b != a

    def test_too_big_a_jump_is_a_different_tile_not_a_teleport(self):
        t = RegionTracker(max_match_distance=0.15)
        (a,) = t.update([(0.5, 0.3)])
        (b,) = t.update([(0.5, 0.85)])      # 0.55 away: the real clip's identity flip
        assert b != a

    def test_a_contested_track_goes_to_the_nearer_detection(self):
        t = RegionTracker(max_match_distance=0.3)
        (a,) = t.update([(0.5, 0.5)])
        near, far = t.update([(0.5, 0.7), (0.5, 0.52)])
        assert far == a and near != a


class TestLineCrossingTracker:
    def test_one_tile_fires_exactly_once(self):
        lt = LineCrossingTracker(LINE)
        fired = [o.crossed for y in (0.3, 0.4, 0.48, 0.55, 0.65, 0.8) for o in lt.update([at(y)])]
        assert fired.count(True) == 1 and lt.crossing_count == 1

    def test_the_identity_flip_that_broke_the_pipeline_no_longer_fires(self):
        # Tile A sits above the line at y=0.3; tile B below it at y=0.8. Areas
        # are near-equal so the old "largest" pick alternated A, B, A, B...
        # Each alternation looked like a tile jumping across the line.
        lt = LineCrossingTracker(LINE)
        for _ in range(6):
            obs = lt.update([at(0.3), at(0.8)])      # BOTH visible every frame now
            assert not any(o.crossed for o in obs)
        assert lt.crossing_count == 0

    def test_two_tiles_each_cross_once_even_while_sharing_the_frame(self):
        lt = LineCrossingTracker(LINE)
        frames = [
            (0.30, 0.00), (0.38, 0.08), (0.46, 0.16),   # A approaching, B just entering
            (0.54, 0.24), (0.62, 0.32), (0.70, 0.40),   # A crosses here
            (0.78, 0.48), (0.86, 0.56), (0.94, 0.64),   # B crosses here
        ]
        fired = []
        for ya, yb in frames:
            for o in lt.update([at(ya), at(yb)]):
                if o.crossed:
                    fired.append(o.track_id)
        assert lt.crossing_count == 2 and len(set(fired)) == 2

    def test_a_tile_hidden_briefly_mid_crossing_still_fires_once(self):
        lt = LineCrossingTracker(LINE, max_missed_frames=3)
        seq = [[at(0.40)], [at(0.46)], [], [], [at(0.58)], [at(0.64)]]
        assert sum(o.crossed for frame in seq for o in lt.update(frame)) == 1

    def test_observations_come_back_in_input_order_with_ids(self):
        lt = LineCrossingTracker(LINE)
        r1, r2 = at(0.2), at(0.8)
        obs = lt.update([r1, r2])
        assert [o.region for o in obs] == [r1, r2]
        assert obs[0].track_id != obs[1].track_id

    def test_bad_line_config_fails_at_construction(self):
        with pytest.raises(ValueError):
            LineCrossingTracker(dict(position=0.5, orientation="diagonal"))
