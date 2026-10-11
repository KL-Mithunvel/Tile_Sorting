"""The trigger line must be PERPENDICULAR to the belt, whichever way the belt runs
(2026-10-11).

The shipped default was "vertical" -- parallel to the belt on both the portrait
phone clip and the landscape webcam recordings -- so nothing ever crossed it.
"""
import cv2
import numpy as np
import pytest

from camera.belt_roi import BeltRoi, find_belt_roi
from camera.capture import load_config
from camera.process_video import process_video_file
from camera.region_tracker import LineCrossingTracker, validate_line_config
from camera.worker import CameraWorker, SharedState

BELT = (90, 140, 20)    # teal-green BGR
TILE = (30, 90, 180)    # orange


def hsv_range(bgr, margin=14):
    px = cv2.cvtColor(np.uint8([[bgr]]), cv2.COLOR_BGR2HSV)[0][0]
    return ([max(0, int(px[0]) - margin), 60, 40], [min(179, int(px[0]) + margin), 255, 255])


def scene(belt, tile_pos=None, size=(400, 300), tilt_px=0):
    """A frame with a belt running 'vertical' (top-to-bottom) or 'horizontal', and optionally
    an orange tile on it at tile_pos (distance along the belt)."""
    w, h = size
    f = np.full((h, w, 3), (40, 40, 40), np.uint8)
    if belt == "vertical":
        pts = np.array([[w // 2 - 50, 0], [w // 2 + 50, 0], [w // 2 + 50 + tilt_px, h], [w // 2 - 50 + tilt_px, h]])
    else:
        pts = np.array([[0, h // 2 - 50], [0, h // 2 + 50], [w, h // 2 + 50 + tilt_px], [w, h // 2 - 50 + tilt_px]])
    cv2.fillPoly(f, [pts], BELT)
    if tile_pos is not None:
        if belt == "vertical":
            cv2.rectangle(f, (w // 2 - 30, tile_pos - 30), (w // 2 + 30, tile_pos + 30), TILE, -1)
        else:
            cv2.rectangle(f, (tile_pos - 30, h // 2 - 30), (tile_pos + 30, h // 2 + 30), TILE, -1)
    return f


COLOR_CFG = {"mode": "color", "hsv_lower": hsv_range(BELT)[0], "hsv_upper": hsv_range(BELT)[1],
             "margin_px": 8, "min_area_px": 500}


class TestBeltDirection:
    @pytest.mark.parametrize("belt", ["vertical", "horizontal"])
    def test_reads_the_direction_whatever_the_frame_shape(self, belt):
        for size in ((400, 300), (300, 400), (320, 320)):       # landscape, portrait, square
            roi = find_belt_roi(scene(belt, size=size), COLOR_CFG)
            assert roi.axis == belt, size
            assert roi.tilt_deg < 3

    def test_a_tilted_belt_still_reads_as_its_nearest_axis_and_reports_the_tilt(self):
        roi = find_belt_roi(scene("vertical", tilt_px=60), COLOR_CFG)   # leans ~11 degrees
        assert roi.axis == "vertical" and 5 < roi.tilt_deg < 20

    def test_a_compact_blob_has_no_direction(self):
        f = np.full((300, 300, 3), (40, 40, 40), np.uint8)
        cv2.rectangle(f, (100, 100), (200, 200), BELT, -1)
        assert find_belt_roi(f, COLOR_CFG).axis is None

    def test_a_tile_lying_across_the_belt_does_not_change_the_direction(self):
        f = scene("vertical")
        cv2.rectangle(f, (140, 100), (260, 200), TILE, -1)              # wider than the belt, splits it
        assert find_belt_roi(f, COLOR_CFG).axis == "vertical"

    @pytest.mark.parametrize("rect,axis", [([0.4, 0.0, 0.6, 1.0], "vertical"), ([0.0, 0.4, 1.0, 0.6], "horizontal"),
                                           ([0.25, 0.25, 0.75, 0.75], None)])
    def test_fixed_mode_takes_the_direction_from_the_rectangle(self, rect, axis):
        # A square frame, so a square normalised rect is a square in pixels too: the
        # direction comes from the rectangle's PIXEL shape, not its 0-1 numbers.
        frame = scene("vertical", size=(300, 300))
        assert find_belt_roi(frame, {"mode": "fixed", "rect": rect}).axis == axis

    def test_fixed_rect_direction_is_judged_in_pixels_not_normalised_units(self):
        # 0.5 x 0.5 normalised is 200x150 px on a 4:3 frame: wider than tall.
        frame = scene("vertical", size=(400, 300))
        assert find_belt_roi(frame, {"mode": "fixed", "rect": [0.25, 0.25, 0.75, 0.75]}).axis == "horizontal"

    def test_the_roi_is_still_an_ordinary_four_tuple(self):
        roi = find_belt_roi(scene("vertical"), {"mode": "fixed", "rect": [0.25, 0.0, 0.75, 1.0]})
        assert isinstance(roi, BeltRoi) and roi == (100, 0, 300, 300)
        x0, y0, x1, y1 = roi
        assert (x1 - x0, y1 - y0) == (200, 300)


def centre(v):
    class R:
        center_normalized = v
    return R()


LINE = dict(position=0.5, direction="both", hysteresis=0.0)


class TestAutoOrientation:
    def test_belt_running_down_the_frame_gets_a_horizontal_line(self):
        t = LineCrossingTracker({**LINE, "orientation": "auto"}, auto_frames=3)
        assert t.orientation is None and not t.resolved
        for _ in range(3):
            t.update([], belt_axis="vertical")
        assert t.orientation == "horizontal"

    def test_belt_running_across_the_frame_gets_a_vertical_line(self):
        t = LineCrossingTracker({**LINE, "orientation": "auto"}, auto_frames=3)
        for _ in range(3):
            t.update([], belt_axis="horizontal")
        assert t.orientation == "vertical"

    def test_nothing_fires_while_the_direction_is_still_being_learned(self):
        t = LineCrossingTracker({**LINE, "orientation": "auto"}, auto_frames=6)
        fired = [o.crossed for y in (0.2, 0.4, 0.6, 0.8) for o in t.update([centre((0.5, y))], belt_axis="vertical")]
        assert not any(fired) and not t.resolved

    def test_frames_with_no_readable_belt_do_not_count_as_votes(self):
        t = LineCrossingTracker({**LINE, "orientation": "auto"}, auto_frames=3)
        for axis in (None, "vertical", None, None, "vertical"):
            t.update([], belt_axis=axis)
        assert not t.resolved                      # only 2 real votes
        t.update([], belt_axis="vertical")
        assert t.resolved

    def test_the_majority_wins_and_a_tie_keeps_waiting(self):
        t = LineCrossingTracker({**LINE, "orientation": "auto"}, auto_frames=4)
        for axis in ("vertical", "horizontal", "vertical", "horizontal"):
            t.update([], belt_axis=axis)
        assert not t.resolved
        t.update([], belt_axis="vertical")
        assert t.orientation == "horizontal"

    def test_once_resolved_it_never_changes(self):
        t = LineCrossingTracker({**LINE, "orientation": "auto"}, auto_frames=2)
        for _ in range(2):
            t.update([], belt_axis="vertical")
        for _ in range(20):
            t.update([], belt_axis="horizontal")
        assert t.orientation == "horizontal"

    def test_a_tile_crosses_the_resolved_line_exactly_once(self):
        t = LineCrossingTracker({**LINE, "orientation": "auto"}, auto_frames=2)
        for _ in range(2):
            t.update([], belt_axis="vertical")
        fired = [o.crossed for y in (0.2, 0.35, 0.45, 0.55, 0.7, 0.85) for o in t.update([centre((0.5, y))], belt_axis="vertical")]
        assert fired.count(True) == 1 and t.crossing_count == 1

    def test_sideways_motion_does_not_cross_a_line_perpendicular_to_a_vertical_belt(self):
        # The old failure mode: a line parallel to the belt. Moving ALONG x never crosses a y-line.
        t = LineCrossingTracker({**LINE, "orientation": "auto"}, auto_frames=2)
        for _ in range(2):
            t.update([], belt_axis="vertical")
        fired = [o.crossed for x in (0.2, 0.4, 0.6, 0.8) for o in t.update([centre((x, 0.3))], belt_axis="vertical")]
        assert not any(fired)

    @pytest.mark.parametrize("orientation", ["vertical", "horizontal"])
    def test_an_explicit_orientation_is_used_as_given_and_ignores_the_belt(self, orientation):
        t = LineCrossingTracker({**LINE, "orientation": orientation})
        assert t.resolved and t.orientation == orientation
        t.update([], belt_axis="horizontal" if orientation == "horizontal" else "vertical")
        assert t.orientation == orientation

    def test_bad_arguments_fail_at_construction(self):
        with pytest.raises(ValueError, match="auto_frames"):
            LineCrossingTracker({**LINE, "orientation": "auto"}, auto_frames=0)
        with pytest.raises(ValueError, match="orientation"):
            LineCrossingTracker({**LINE, "orientation": "diagonal"})


class TestConfigGuard:
    def test_auto_without_a_belt_roi_is_refused_up_front(self):
        with pytest.raises(ValueError, match="belt ROI is off"):
            validate_line_config({"orientation": "auto"}, {"belt_roi": {"mode": "off"}})
        with pytest.raises(ValueError, match="belt ROI is off"):
            validate_line_config({}, {})                      # the default is auto

    @pytest.mark.parametrize("trig,seg", [
        ({"orientation": "auto"}, {"belt_roi": {"mode": "color"}}),
        ({"orientation": "auto"}, {"belt_roi": {"mode": "fixed"}}),
        ({"orientation": "horizontal"}, {"belt_roi": {"mode": "off"}}),
    ])
    def test_valid_combinations_pass(self, trig, seg):
        validate_line_config(trig, seg)

    def test_the_shipped_config_is_valid_and_defaults_to_auto_at_the_middle(self):
        cfg = load_config()
        validate_line_config(cfg["trigger_line"], cfg["segmentation"])
        assert cfg["trigger_line"]["orientation"] == "auto" and cfg["trigger_line"]["position"] == 0.5


def worker_config(belt_mode="color"):
    cfg = load_config()
    px = cv2.cvtColor(np.uint8([[TILE]]), cv2.COLOR_BGR2HSV)[0][0]
    cfg["segmentation"]["hsv_lower"] = [max(0, int(px[0]) - 10), 100, 100]
    cfg["segmentation"]["hsv_upper"] = [min(179, int(px[0]) + 10), 255, 255]
    cfg["segmentation"]["min_tile_area_px"] = 1000
    cfg["segmentation"]["belt_roi"] = dict(COLOR_CFG) if belt_mode == "color" else {"mode": "off"}
    cfg["capture_snapshots"]["enabled"] = False
    cfg["grading_model"]["enabled"] = False
    cfg["trigger_line"].update(orientation="auto", position=0.5, hysteresis=0.0, auto_frames=3, direction="both", max_step=None)
    return cfg


class FeedCamera:
    def __init__(self, frames):
        self.frames = list(frames)

    def start(self): pass
    def stop(self): pass

    def read_frame(self):
        raise StopIteration if not self.frames else None


@pytest.mark.parametrize("belt,size", [("vertical", (400, 300)), ("vertical", (300, 400)),
                                       ("horizontal", (400, 300)), ("horizontal", (300, 400))])
def test_worker_counts_a_tile_in_every_camera_orientation_with_no_orientation_setting(belt, size):
    """The same config, four camera set-ups: the line follows the belt."""
    length = size[1] if belt == "vertical" else size[0]
    state = SharedState()
    w = CameraWorker(None, worker_config(), state)
    for pos in np.linspace(40, length - 40, 14).astype(int):
        w._safe_step(scene(belt, tile_pos=int(pos), size=size))
    s = state.status()
    assert s["errors"]["count"] == 0
    assert s["tile_count"] == 1
    assert s["info"]["line_orientation"] == ("horizontal" if belt == "vertical" else "vertical")


def test_worker_says_so_when_the_belt_direction_cannot_be_read():
    state = SharedState()
    w = CameraWorker(None, worker_config(), state)
    blank = np.full((300, 400, 3), (40, 40, 40), np.uint8)      # no belt anywhere
    for _ in range(CameraWorker._AUTO_ORIENTATION_PATIENCE_FRAMES + 1):
        w._safe_step(blank)
    s = state.status()
    assert s["errors"]["count"] == 1 and "belt's direction" in s["errors"]["last"]
    assert s["info"]["line_orientation"] is None


def test_worker_refuses_auto_with_the_belt_roi_off():
    with pytest.raises(ValueError, match="belt ROI is off"):
        CameraWorker(None, worker_config(belt_mode="off"), SharedState())


def test_a_new_source_relearns_the_belt_direction():
    state = SharedState()
    w = CameraWorker(None, worker_config(), state)
    for pos in np.linspace(40, 260, 14).astype(int):
        w._safe_step(scene("vertical", tile_pos=int(pos)))
    assert state.status()["info"]["line_orientation"] == "horizontal"
    w._reset_run_state()                                       # what switch_source does
    assert state.status()["info"]["line_orientation"] is None
    for pos in np.linspace(40, 360, 14).astype(int):
        w._safe_step(scene("horizontal", tile_pos=int(pos)))
    assert state.status()["info"]["line_orientation"] == "vertical"


@pytest.mark.parametrize("belt,size", [("vertical", (400, 300)), ("horizontal", (400, 300))])
def test_offline_processing_agrees_with_the_live_worker(tmp_path, belt, size):
    length = size[1] if belt == "vertical" else size[0]
    path = tmp_path / "clip.avi"
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 20, size)
    for pos in np.linspace(40, length - 40, 20).astype(int):
        out.write(scene(belt, tile_pos=int(pos), size=size))
    out.release()

    info = {}
    records = process_video_file(str(path), worker_config(), save_snapshots=False, info=info)
    assert len(records) == 1
    assert info["line_orientation"] == ("horizontal" if belt == "vertical" else "vertical")
    assert info["crossings"] == 1 and info["frames"] == 20 and info["belt_frames"] == 20


class TestCrossingDirection:
    def test_observation_reports_which_way_the_tile_crossed(self):
        t = LineCrossingTracker({**LINE, "orientation": "horizontal"})
        down = [o for y in (0.3, 0.4, 0.48, 0.56, 0.66) for o in t.update([centre((0.5, y))]) if o.crossed]
        assert [o.direction for o in down] == ["forward"]
        up = [o for y in (0.56, 0.48, 0.4, 0.3) for o in t.update([centre((0.5, y))]) if o.crossed]
        assert [o.direction for o in up] == ["reverse"]

    def test_direction_is_none_when_nothing_crossed(self):
        t = LineCrossingTracker({**LINE, "orientation": "horizontal"})
        assert all(o.direction is None for y in (0.1, 0.2) for o in t.update([centre((0.5, y))]))


def test_on_tile_callback_receives_each_graded_tile_with_its_frame(tmp_path):
    """The review-sheet builder uses this hook to cut its own pictures."""
    path = tmp_path / "clip.avi"
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 20, (400, 300))
    for pos in np.linspace(40, 260, 20).astype(int):
        out.write(scene("vertical", tile_pos=int(pos)))
    out.release()

    seen = []
    records = process_video_file(
        str(path), worker_config(), save_snapshots=False,
        on_tile=lambda rec, region, frame, idx: seen.append((rec.seq, region.bbox, frame.shape, idx)),
    )
    assert len(records) == len(seen) == 1
    seq, bbox, shape, idx = seen[0]
    assert seq == 1 and shape == (300, 400, 3) and 0 <= idx < 20
