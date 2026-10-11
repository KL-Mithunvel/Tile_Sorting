"""Grade the sharpest frame of a tile's pass over the line, not just the crossing frame (2026-10-11)."""
from dataclasses import dataclass

import cv2
import numpy as np
import pytest

from camera.frame_picker import BestFramePicker
from camera.region_tracker import Observation
from camera.segmentation import segment_tile
from camera.sharpness import edge_sharpness


def tile_frame(size=(400, 300)):
    f = np.full((size[1], size[0], 3), (60, 130, 40), np.uint8)             # green belt
    cv2.rectangle(f, (150, 100), (260, 210), (30, 90, 180), -1)             # orange tile
    cv2.circle(f, (170, 120), 6, (20, 70, 150), -1)                         # a bit of surface detail
    return f


def region_of(frame):
    return segment_tile(frame, (0, 100, 100), (25, 255, 255), 500, 3)


def motion_blur(img, length, axis):
    if length <= 1:
        return img.copy()
    k = np.zeros((length, length), np.float32)
    if axis == "vertical":
        k[:, length // 2] = 1
    else:
        k[length // 2, :] = 1
    return cv2.filter2D(img, -1, k / k.sum())


class TestEdgeSharpness:
    def score(self, blur_len, blur_axis, belt_axis):
        f = tile_frame()
        region = region_of(f)                                   # the region is found on the SHARP frame, as the mask does not move
        return edge_sharpness(motion_blur(f, blur_len, blur_axis), region, belt_axis)

    def test_blur_along_the_belt_lowers_the_score_steadily(self):
        scores = [self.score(n, "vertical", "vertical") for n in (1, 5, 9, 15, 25)]
        assert all(a >= b for a, b in zip(scores, scores[1:]))      # never rises as the blur grows
        assert scores[1] < scores[0] and scores[2] < scores[1]       # and clearly falls over realistic (few-pixel) blur
        assert scores[-1] < 0.3 * scores[0]

    def test_a_horizontal_belt_responds_to_horizontal_blur(self):
        scores = [self.score(n, "horizontal", "horizontal") for n in (1, 7, 15, 25)]
        assert all(a >= b for a, b in zip(scores, scores[1:])) and scores[-1] < 0.3 * scores[0]

    def test_blur_across_the_belt_barely_matters(self):
        """Motion along a vertical belt cannot smear edges that run along it, and the metric ignores them."""
        sharp = self.score(1, "horizontal", "vertical")
        blurred_across = self.score(25, "horizontal", "vertical")
        assert blurred_across > 0.55 * sharp                    # the along-belt edges are untouched by sideways blur
        along = self.score(25, "vertical", "vertical")
        assert along < blurred_across

    def test_unknown_direction_uses_every_edge(self):
        assert self.score(1, "vertical", None) > self.score(25, "vertical", None)

    def test_a_featureless_or_tiny_region_scores_zero_not_an_error(self):
        f = tile_frame()
        region = region_of(f)
        region.mask = np.zeros_like(region.mask)
        assert edge_sharpness(f, region, "vertical") == 0.0

    def test_a_region_at_the_frame_edge_is_measured_without_error(self):
        f = np.full((200, 200, 3), (60, 130, 40), np.uint8)
        cv2.rectangle(f, (0, 0), (90, 90), (30, 90, 180), -1)
        region = segment_tile(f, (0, 100, 100), (25, 255, 255), 500, 3)
        assert edge_sharpness(f, region, "vertical") > 0


@dataclass
class FakeRegion:
    center_normalized: tuple


def obs(tid, y, crossed=False, x=0.5):
    return Observation(track_id=tid, region=FakeRegion((x, y)), crossed=crossed, direction="forward" if crossed else None)


POSITION = 0.5


def feed(picker, sequence, scores, orientation="horizontal"):
    """sequence: list of lists of Observations, one list per frame. scores: region -> float by its y."""
    out = []
    for i, frame_obs in enumerate(sequence):
        out += picker.update(frame_obs, orientation, POSITION, lambda r: scores[round(r.center_normalized[1], 3)],
                             frame=f"frame{i}", frame_index=i)
    return out


class TestPicker:
    def test_grades_the_sharpest_frame_in_the_band_not_the_crossing_frame(self):
        ys = [0.30, 0.42, 0.47, 0.52, 0.55, 0.58, 0.70]            # crossing is detected at 0.52
        scores = {0.42: 5, 0.47: 9, 0.52: 4, 0.55: 12, 0.58: 7}    # the sharpest is at 0.55 (after crossing)
        seq = [[obs(1, y, crossed=(y == 0.52))] for y in ys]
        picks = feed(BestFramePicker(band=0.1), seq, scores)
        assert len(picks) == 1
        p = picks[0]
        assert p.region.center_normalized[1] == 0.55 and p.score == 12 and p.candidates == 5
        assert p.frame == "frame4" and p.frame_index == 4          # the frame the region came from, not the current one

    def test_the_best_frame_can_be_one_before_the_crossing(self):
        ys = [0.30, 0.44, 0.48, 0.53, 0.60, 0.75]
        scores = {0.44: 20, 0.48: 6, 0.53: 5, 0.60: 3}
        picks = feed(BestFramePicker(band=0.1), [[obs(1, y, crossed=(y == 0.53))] for y in ys], scores)
        assert picks[0].region.center_normalized[1] == 0.44

    def test_emits_exactly_once_when_the_tile_leaves_the_band(self):
        ys = [0.3, 0.45, 0.5, 0.55, 0.7, 0.8, 0.9]
        scores = {0.45: 1, 0.5: 2, 0.55: 3}
        seq = [[obs(1, y, crossed=(y == 0.55))] for y in ys]
        picks = feed(BestFramePicker(band=0.1), seq, scores)
        assert len(picks) == 1

    def test_no_pick_for_a_tile_that_never_crosses(self):
        ys = [0.3, 0.45, 0.5, 0.48, 0.4, 0.3]
        picks = feed(BestFramePicker(band=0.1), [[obs(1, y)] for y in ys], {0.45: 1, 0.5: 2, 0.48: 3, 0.4: 1})
        assert picks == []

    def test_a_tile_that_stops_on_the_line_is_still_graded(self):
        seq = [[obs(1, 0.5, crossed=(i == 2))] for i in range(10)]
        picker = BestFramePicker(band=0.1, max_frames=4)
        picks = feed(picker, seq, {0.5: 1})
        assert len(picks) == 1 and picks[0].candidates == 4

    def test_a_tile_lost_while_still_in_the_band_is_graded_with_the_best_frame_seen(self):
        seq = [[obs(1, 0.47)], [obs(1, 0.5, crossed=True)], [], [], [], [], []]
        picks = feed(BestFramePicker(band=0.1, max_absent=2), seq, {0.47: 8, 0.5: 3})
        assert len(picks) == 1 and picks[0].score == 8

    def test_a_fast_tile_whose_crossing_lands_outside_the_band_is_still_graded(self):
        # 0.4 -> 0.7 in one frame with a band of 0.05: the crossing frame is out of the band
        seq = [[obs(1, 0.4)], [obs(1, 0.7, crossed=True)]]
        picks = feed(BestFramePicker(band=0.05), seq, {0.4: 1, 0.7: 9})
        assert len(picks) == 1 and picks[0].region.center_normalized[1] == 0.7

    def test_two_tiles_are_picked_independently(self):
        seq = [
            [obs(1, 0.45, x=0.3), obs(2, 0.10, x=0.7)],
            [obs(1, 0.52, crossed=True, x=0.3), obs(2, 0.2, x=0.7)],
            [obs(1, 0.70, x=0.3), obs(2, 0.46, x=0.7)],
            [obs(1, 0.85, x=0.3), obs(2, 0.53, crossed=True, x=0.7)],
            [obs(2, 0.75, x=0.7)],
        ]
        scores = {0.45: 3, 0.52: 7, 0.46: 2, 0.53: 6}
        picks = feed(BestFramePicker(band=0.1), seq, scores)
        assert sorted(p.track_id for p in picks) == [1, 2]
        assert {p.track_id: p.score for p in picks} == {1: 7, 2: 6}

    def test_band_zero_is_the_old_behaviour_grade_the_crossing_frame(self):
        called = []
        picker = BestFramePicker(band=0)
        out = picker.update([obs(1, 0.52, crossed=True), obs(2, 0.2)], "horizontal", POSITION, lambda r: called.append(r) or 1.0, frame="F", frame_index=7)
        assert [(p.track_id, p.score, p.candidates, p.frame, p.frame_index) for p in out] == [(1, None, 1, "F", 7)]
        assert called == []                                    # nothing was scored

    def test_unresolved_orientation_picks_nothing_yet(self):
        picker = BestFramePicker(band=0.1)
        assert picker.update([obs(1, 0.5, crossed=True)], None, POSITION, lambda r: 1.0)[0].score is None  # pass-through

    def test_a_vertical_line_is_crossed_along_x(self):
        seq = [[obs(1, 0.5, x=x, crossed=(x == 0.52))] for x in (0.4, 0.46, 0.52, 0.56, 0.75)]
        picks = []
        picker = BestFramePicker(band=0.1)
        for o in seq:
            picks += picker.update(o, "vertical", POSITION, lambda r: 10 - abs(r.center_normalized[0] - 0.56) * 10)
        assert len(picks) == 1 and picks[0].region.center_normalized[0] == 0.56

    def test_states_are_forgotten_once_a_tile_is_gone(self):
        picker = BestFramePicker(band=0.1, max_absent=1)
        feed(picker, [[obs(1, 0.45)], [obs(1, 0.52, crossed=True)], [obs(1, 0.8)]], {0.45: 1, 0.52: 2})
        for _ in range(4):
            picker.update([], "horizontal", POSITION, lambda r: 0.0)
        assert picker._tracks == {}

    def test_bad_arguments(self):
        with pytest.raises(ValueError):
            BestFramePicker(band=-0.1)
        with pytest.raises(ValueError):
            BestFramePicker(max_frames=0)


# ---- end to end: the sharpest frame is the one that is graded ---------------

from camera.capture import load_config  # noqa: E402
from camera.process_video import process_video_file  # noqa: E402
from camera.worker import CameraWorker, SharedState  # noqa: E402

TILE_BGR = (30, 90, 180)


def pass_config(band=0.12):
    cfg = load_config()
    px = cv2.cvtColor(np.uint8([[TILE_BGR]]), cv2.COLOR_BGR2HSV)[0][0]
    cfg["segmentation"]["hsv_lower"] = [max(0, int(px[0]) - 10), 100, 100]
    cfg["segmentation"]["hsv_upper"] = [min(179, int(px[0]) + 10), 255, 255]
    cfg["segmentation"]["min_tile_area_px"] = 1000
    cfg["segmentation"]["belt_roi"] = {"mode": "off"}
    cfg["capture_snapshots"]["enabled"] = False
    cfg["grading_model"]["enabled"] = False
    cfg["trigger_line"].update(orientation="horizontal", position=0.5, hysteresis=0.0, sharpest_band=band, max_step=None)
    return cfg


def frames_for_one_pass(sharp_index, blur=15):
    """A tile moving down a 300-px-high frame across the line at y=150; every frame is
    motion-blurred along the belt except `sharp_index`."""
    out = []
    for i, cy in enumerate(range(60, 250, 12)):
        f = np.full((300, 200, 3), (90, 140, 40), np.uint8)
        cv2.rectangle(f, (70, cy - 30), (130, cy + 30), TILE_BGR, -1)
        out.append(f if i == sharp_index else motion_blur(f, blur, "vertical"))
    return out


def test_the_worker_grades_the_sharp_frame_not_the_crossing_frame():
    frames = frames_for_one_pass(sharp_index=9)             # tile centre is at y=168 in that frame; the line is at y=150
    state = SharedState()
    w = CameraWorker(None, pass_config(), state)
    for f in frames:
        w._safe_step(f)
    s = state.status()
    assert s["errors"]["count"] == 0 and s["tile_count"] == 1
    rec = s["recent_tiles"][0]
    assert abs(rec.center[1] - 168) <= 2                    # the sharp frame, not the crossing frame (~y 156)
    assert rec.sharpness is not None and rec.sharpness_candidates >= 4
    assert s["recent_tiles"][0].track_id is not None


def test_with_picking_off_the_crossing_frame_is_graded_as_before():
    frames = frames_for_one_pass(sharp_index=9)
    state = SharedState()
    w = CameraWorker(None, pass_config(band=0), state)
    for f in frames:
        w._safe_step(f)
    rec = state.status()["recent_tiles"][0]
    assert 150 < rec.center[1] < 165                        # just past the line: the crossing frame
    assert rec.sharpness is None


def test_picking_never_changes_how_many_tiles_are_counted():
    frames = frames_for_one_pass(sharp_index=5) + frames_for_one_pass(sharp_index=7)
    counts = []
    for band in (0, 0.06, 0.12, 0.3):
        state = SharedState()
        w = CameraWorker(None, pass_config(band=band), state)
        for f in frames:
            w._safe_step(f)
        counts.append(state.status()["tile_count"])
    assert counts == [2, 2, 2, 2]


def test_offline_processing_hands_on_tile_the_frame_the_region_came_from(tmp_path):
    path = tmp_path / "clip.avi"
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 20, (200, 300))
    for f in frames_for_one_pass(sharp_index=9, blur=9):
        out.write(f)
    out.release()
    seen = []
    records = process_video_file(str(path), pass_config(), save_snapshots=False,
                                 on_tile=lambda rec, region, frame, idx: seen.append((rec, region, frame, idx)))
    assert len(records) == len(seen) == 1
    rec, region, frame, idx = seen[0]
    x, y, w, h = region.bbox
    assert np.array_equal(frame[y:y + h, x:x + w], rec.tile_bgr)      # region and frame belong together, not the current frame
    assert rec.sharpness is not None and idx >= 5


def test_belt_direction_falls_back_to_perpendicular_to_the_line():
    from camera.frame_picker import belt_axis_from_line
    assert belt_axis_from_line("horizontal") == "vertical"
    assert belt_axis_from_line("vertical") == "horizontal"
    assert belt_axis_from_line(None) is None
