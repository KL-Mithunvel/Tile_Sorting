"""CameraWorker must survive a bad frame (step 3, 2026-10-09).

Before the guard, one exception anywhere in the per-frame pipeline killed the
capture thread silently. These drive the worker synchronously with a fake
capture, so no thread, camera or timing is involved.
"""
import cv2
import numpy as np
import pytest

import camera.worker as worker_mod
from camera.capture import load_config
from camera.worker import CameraWorker, SharedState

TILE_BGR = (30, 90, 180)


def make_config():
    cfg = load_config()
    px = cv2.cvtColor(np.uint8([[TILE_BGR]]), cv2.COLOR_BGR2HSV)[0][0]
    cfg["segmentation"]["hsv_lower"] = [max(0, int(px[0]) - 10), 100, 100]
    cfg["segmentation"]["hsv_upper"] = [min(179, int(px[0]) + 10), 255, 255]
    cfg["segmentation"]["belt_roi"] = {"mode": "off"}
    cfg["segmentation"]["min_tile_area_px"] = 1000
    cfg["capture_snapshots"]["enabled"] = False
    cfg["grading_model"]["enabled"] = False
    cfg["processing_trigger"] = "line_crossing"
    cfg["trigger_line"].update(position=0.5, orientation="horizontal", direction="both", hysteresis=0.0, max_step=None)
    return cfg


def frame_with_tile(cy, size=60):
    f = np.full((300, 200, 3), (40, 40, 40), np.uint8)
    cv2.rectangle(f, (70, cy - size // 2), (70 + size, cy + size // 2), TILE_BGR, -1)
    return f


def pass_a_tile(worker):
    """Drive one tile top->bottom across the line at y=150."""
    for cy in (60, 90, 120, 135, 165, 190):
        worker._safe_step(frame_with_tile(cy))


class FakeCapture:
    def __init__(self, frames):
        self.frames = list(frames)

    def read_frame(self):
        if not self.frames:
            raise StopIteration
        return self.frames.pop(0)


def test_sanity_a_tile_crossing_is_graded_without_any_failure():
    state = SharedState()
    w = CameraWorker(FakeCapture([]), make_config(), state)
    pass_a_tile(w)
    s = state.status()
    assert s["tile_count"] == 1 and s["errors"]["count"] == 0


def test_an_exception_in_the_pipeline_does_not_escape_and_is_recorded(monkeypatch):
    state = SharedState()
    w = CameraWorker(FakeCapture([]), make_config(), state)

    def boom(*a, **k):
        raise ValueError("degenerate crop")

    monkeypatch.setattr(worker_mod, "process_tile", boom)
    pass_a_tile(w)                                  # must not raise

    s = state.status()
    assert s["errors"]["count"] == 1
    assert "ValueError" in s["errors"]["last"] and "degenerate crop" in s["errors"]["last"]
    assert s["station_state"] in ("waiting", "in_view")   # never stuck on "processing"


def test_the_next_tile_is_still_inspected_after_a_failed_one(monkeypatch):
    state = SharedState()
    w = CameraWorker(FakeCapture([]), make_config(), state)
    real = worker_mod.process_tile
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first tile blows up")
        return real(*a, **k)

    monkeypatch.setattr(worker_mod, "process_tile", flaky)
    pass_a_tile(w)
    w._safe_step(np.full((300, 200, 3), (40, 40, 40), np.uint8))   # tile leaves
    pass_a_tile(w)

    s = state.status()
    assert s["errors"]["count"] == 1
    assert len(s["recent_tiles"]) == 1             # the second tile made it through


def test_failure_in_segmentation_is_also_survived(monkeypatch):
    state = SharedState()
    w = CameraWorker(FakeCapture([]), make_config(), state)
    monkeypatch.setattr(worker_mod, "locate_tiles", lambda *a, **k: (_ for _ in ()).throw(IndexError("bad frame")))
    for _ in range(3):
        w._safe_step(frame_with_tile(100))
    assert state.status()["errors"]["count"] == 3


def test_loop_ends_cleanly_when_the_video_runs_out():
    state = SharedState()
    w = CameraWorker(FakeCapture([frame_with_tile(100), frame_with_tile(120)]), make_config(), state)
    w._running = True
    w._loop()                                       # returns instead of raising StopIteration
    assert w._running is False
    assert state.get_frame() is not None            # the frames before the end were processed


def test_loop_retries_after_a_transient_capture_error():
    class Flaky(FakeCapture):
        def __init__(self, frames):
            super().__init__(frames)
            self.failed = False

        def read_frame(self):
            if not self.failed:
                self.failed = True
                raise RuntimeError("camera hiccup")
            return super().read_frame()

    state = SharedState()
    w = CameraWorker(Flaky([frame_with_tile(100)]), make_config(), state)
    w._running = True
    w._loop()
    assert state.get_frame() is not None


def test_api_status_reports_errors():
    from camera.dashboard import create_app

    state = SharedState()
    state.record_error("ValueError: x")
    body = create_app(state).test_client().get("/api/status").get_json()
    assert body["errors"] == {"count": 1, "last": "ValueError: x"}


# ---- two tiles in frame (step 4) ---------------------------------------

def frame_with_two_tiles(cy_a, cy_b, size_a=60, size_b=60):
    """Two tiles of (near-)identical area -- the real clip's failure case."""
    f = np.full((600, 200, 3), (40, 40, 40), np.uint8)
    for cy, size, x in ((cy_a, size_a, 20), (cy_b, size_b, 110)):
        cv2.rectangle(f, (x, cy - size // 2), (x + size, cy + size // 2), TILE_BGR, -1)
    return f


def test_two_tiles_of_equal_area_each_cross_once_and_are_graded_separately():
    cfg = make_config()
    state = SharedState()
    w = CameraWorker(FakeCapture([]), cfg, state)
    # Tile A travels 120 -> 480 and tile B 20 -> 380 (both crossing y=300), with
    # B's size jittering by a pixel so "which is larger" flips -- the old failure.
    for i, ya in enumerate(range(120, 481, 30)):
        yb = ya - 100
        size_b = 60 + (i % 2)                    # 60/61: the "largest" alternates
        w._safe_step(frame_with_two_tiles(ya, max(yb, 30), size_b=size_b))
    s = state.status()
    assert s["errors"]["count"] == 0
    assert s["tile_count"] == 2                  # not 12 identity-flip "crossings"
    assert len(s["recent_tiles"]) == 2
