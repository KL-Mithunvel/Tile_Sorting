"""Threaded newest-frame capture, frame-rate meters and the dashboard's perf numbers (2026-10-11)."""
import threading
import time

import cv2
import numpy as np
import pytest

from camera.capture import LatestFrameCapture, load_config
from camera.dashboard import create_app
from camera.ratemeter import RateMeter
from camera.worker import CameraWorker, SharedState


class TestRateMeter:
    def test_no_rate_until_two_events(self):
        m = RateMeter()
        assert m.rate() is None
        m.tick(1.0)
        assert m.rate() is None

    def test_rate_is_events_per_second(self):
        m = RateMeter()
        for i in range(11):
            m.tick(i * 0.1)                    # 10 fps
        assert m.rate() == pytest.approx(10.0)

    def test_only_the_recent_window_counts(self):
        m = RateMeter(window=5)
        for i in range(10):
            m.tick(i * 1.0)                    # 1 fps for a long time...
        for i in range(5):
            m.tick(9.0 + i * 0.1)              # ...then 10 fps
        assert m.rate() == pytest.approx(10.0, rel=0.05)

    def test_simultaneous_events_have_no_rate_and_reset_clears(self):
        m = RateMeter()
        m.tick(1.0); m.tick(1.0)
        assert m.rate() is None
        m.tick(2.0); m.reset()
        assert m.rate() is None

    def test_window_must_be_able_to_measure(self):
        with pytest.raises(ValueError):
            RateMeter(window=1)


class Source:
    """A fake live camera producing numbered frames at ~`fps`."""

    def __init__(self, fps=200.0, fail_after=None):
        self.interval = 1.0 / fps
        self.n = 0
        self.fail_after = fail_after
        self.started = self.stopped = False
        self.settings = {"width": 64, "height": 48}

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def read_frame(self):
        time.sleep(self.interval)
        if self.fail_after is not None and self.n >= self.fail_after:
            raise RuntimeError("camera gone")
        self.n += 1
        return np.full((48, 64, 3), self.n % 250, np.uint8)


class TestLatestFrameCapture:
    def test_passes_start_and_stop_through_and_shows_the_drivers_settings(self):
        src = Source()
        cap = LatestFrameCapture(src)
        cap.start()
        assert src.started and cap.settings == {"width": 64, "height": 48}
        cap.read_frame()
        cap.stop()
        assert src.stopped

    def test_a_slow_consumer_gets_the_newest_frame_not_the_next_one_in_a_queue(self):
        cap = LatestFrameCapture(Source(fps=300))
        cap.start()
        first = int(cap.read_frame()[0, 0, 0])
        time.sleep(0.25)                                   # the consumer is busy; ~75 frames go by
        second = int(cap.read_frame()[0, 0, 0])
        cap.stop()
        assert second - first > 20                         # it jumped to (near) the latest, it did not take frame first+1

    def test_skipped_frames_are_counted(self):
        cap = LatestFrameCapture(Source(fps=300))
        cap.start()
        cap.read_frame()
        time.sleep(0.2)
        cap.read_frame()
        cap.stop()
        assert cap.frames_captured > 30 and cap.frames_skipped > 20

    def test_a_fast_consumer_skips_nothing(self):
        cap = LatestFrameCapture(Source(fps=100))
        cap.start()
        for _ in range(15):
            cap.read_frame()                               # blocks for each new frame
        skipped_pct = cap.frames_skipped / cap.frames_captured
        cap.stop()
        assert skipped_pct < 0.2

    def test_the_same_frame_is_never_handed_out_twice(self):
        cap = LatestFrameCapture(Source(fps=150))
        cap.start()
        seen = [int(cap.read_frame()[0, 0, 0]) for _ in range(12)]
        cap.stop()
        assert seen == sorted(set(seen))

    def test_capture_fps_tracks_the_cameras_rate_not_the_consumers(self):
        cap = LatestFrameCapture(Source(fps=100))
        cap.start()
        time.sleep(0.5)
        cap.read_frame()
        fps = cap.capture_fps
        cap.stop()
        assert 50 < fps < 130

    def test_a_camera_error_reaches_the_consumer(self):
        cap = LatestFrameCapture(Source(fps=300, fail_after=3))
        cap.start()
        with pytest.raises(RuntimeError, match="camera gone"):
            for _ in range(50):
                cap.read_frame()
        cap.stop()

    def test_a_silent_camera_does_not_hang_the_consumer(self):
        class Silent(Source):
            def read_frame(self):
                time.sleep(5)

        cap = LatestFrameCapture(Silent())
        cap._WAIT_S = 0.15
        cap.start()
        t0 = time.monotonic()
        with pytest.raises(RuntimeError, match="no new frame"):
            cap.read_frame()
        assert time.monotonic() - t0 < 1.0
        cap._running = False                                # do not wait for the 5 s read to finish
        cap._thread = None

    def test_stop_ends_the_capture_thread(self):
        cap = LatestFrameCapture(Source(fps=300))
        cap.start()
        cap.read_frame()
        thread = cap._thread
        cap.stop()
        assert not thread.is_alive()

    def test_end_of_a_wrapped_source_is_passed_on(self):
        class Ends(Source):
            def read_frame(self):
                raise StopIteration

        cap = LatestFrameCapture(Ends())
        cap.start()
        with pytest.raises(StopIteration):
            cap.read_frame()
        cap.stop()


def worker_config():
    cfg = load_config()
    px = cv2.cvtColor(np.uint8([[(30, 90, 180)]]), cv2.COLOR_BGR2HSV)[0][0]
    cfg["segmentation"]["hsv_lower"] = [max(0, int(px[0]) - 10), 100, 100]
    cfg["segmentation"]["hsv_upper"] = [min(179, int(px[0]) + 10), 255, 255]
    cfg["segmentation"]["belt_roi"] = {"mode": "off"}
    cfg["trigger_line"]["orientation"] = "horizontal"
    cfg["capture_snapshots"]["enabled"] = False
    cfg["grading_model"]["enabled"] = False
    return cfg


class TestPerfInStatus:
    def test_worker_publishes_capture_and_processing_rates_and_skips(self):
        state = SharedState()
        cap = LatestFrameCapture(Source(fps=300))
        w = CameraWorker(cap, worker_config(), state)
        w._PUBLISH_EVERY_S = 0.0
        w.start()
        time.sleep(0.6)
        w.stop()
        perf = state.status()["perf"]
        assert perf["capture_fps"] > 50 and perf["process_fps"] > 5
        assert perf["skipped_pct"] is not None and 0 <= perf["skipped_pct"] <= 100
        assert perf["camera"] == {"width": 64, "height": 48}

    def test_a_file_source_has_no_capture_perf(self):
        state = SharedState()

        class FileLike(Source):
            frame_count, position, fps, paused, loop = 10, 0, 30.0, False, False

        w = CameraWorker(FileLike(), worker_config(), state)
        w._publish(force=True)
        assert state.status()["perf"] is None and state.status()["source"]["playback"] is not None

    def test_api_status_carries_perf(self):
        state = SharedState()
        state.set_perf({"capture_fps": 29.8, "process_fps": 17.5, "skipped_pct": 41.0, "camera": {"width": 1920}})
        body = create_app(state).test_client().get("/api/status").get_json()
        assert body["perf"]["process_fps"] == 17.5


def test_the_browser_feed_is_downscaled_but_detection_sees_the_full_frame():
    cfg = worker_config()
    cfg["dashboard"]["stream_width"] = 320
    state = SharedState()
    w = CameraWorker(None, cfg, state)
    big = np.full((1080, 1920, 3), (40, 40, 40), np.uint8)
    cv2.rectangle(big, (900, 400), (1100, 600), (30, 90, 180), -1)
    w._safe_step(big)
    jpg = cv2.imdecode(np.frombuffer(state.get_frame(), np.uint8), cv2.IMREAD_COLOR)
    assert jpg.shape[:2] == (180, 320)                      # what the browser gets
    assert state.status()["errors"]["count"] == 0           # and the 1080p frame was processed without trouble


def test_stream_width_null_sends_full_size():
    cfg = worker_config()
    cfg["dashboard"]["stream_width"] = None
    state = SharedState()
    CameraWorker(None, cfg, state)._safe_step(np.full((300, 400, 3), 40, np.uint8))
    assert cv2.imdecode(np.frombuffer(state.get_frame(), np.uint8), cv2.IMREAD_COLOR).shape[:2] == (300, 400)
