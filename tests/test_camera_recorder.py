"""Recording raw frames to data/recordings/<date_time>/ (2026-10-11)."""
import json
from datetime import datetime

import cv2
import numpy as np
import pytest

from camera.capture import VideoFileCapture
from camera.recorder import Recorder, estimate_fps
from camera.sources import list_recordings

START = datetime(2026, 10, 11, 14, 30, 5)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def frame(i, size=(64, 48)):
    return np.full((size[1], size[0], 3), (i * 20) % 256, np.uint8)


def make(tmp_path, clock=None, **kw):
    clock = clock or FakeClock()
    return Recorder(tmp_path, "camera 0", now_fn=lambda: START, clock=clock, **kw), clock


def record(rec, clock, n, dt):
    for i in range(n):
        rec.write(frame(i))
        clock.advance(dt)


class TestEstimateFps:
    def test_measures_the_real_rate(self):
        assert estimate_fps([0.0, 0.1, 0.2, 0.3], 15) == (pytest.approx(10.0), True)

    def test_a_camera_that_is_not_the_rate_it_was_asked_for(self):
        ts = [i / 27.0 for i in range(10)]            # asked for 15, delivers 27
        fps, measured = estimate_fps(ts, 15)
        assert fps == pytest.approx(27.0) and measured

    @pytest.mark.parametrize("ts", [[], [1.0], [2.0, 2.0, 2.0]])
    def test_too_little_to_measure_uses_the_fallback_and_says_so(self, ts):
        assert estimate_fps(ts, 15) == (15.0, False)

    def test_clamped_to_a_plausible_range(self):
        assert estimate_fps([0.0, 100.0], 15)[0] == 1.0          # one stalled gap
        assert estimate_fps([0.0, 0.0001, 0.0002], 15)[0] == 120.0


class TestRecorder:
    def test_folder_is_named_by_start_date_and_time(self, tmp_path):
        rec, _ = make(tmp_path)
        assert rec.start().name == "2026-10-11_14-30-05"

    def test_writes_a_replayable_video_at_the_measured_rate(self, tmp_path):
        rec, clock = make(tmp_path, warmup_frames=5)
        folder = rec.start()
        record(rec, clock, n=20, dt=0.1)               # a 10 fps camera
        meta = rec.stop()

        assert meta["status"] == "complete" and meta["frames"] == 20
        assert meta["fps"] == pytest.approx(10.0, abs=0.01) and meta["fps_measured"] is True
        assert (meta["width"], meta["height"]) == (64, 48)

        video = folder / meta["video"]
        cap = VideoFileCapture(str(video)); cap.start()
        got = 0
        with pytest.raises(StopIteration):
            while True:
                cap.read_frame(); got += 1
        cap.stop()
        assert got == 20                                # every frame, including the warm-up ones

    def test_meta_json_is_saved_beside_the_video(self, tmp_path):
        rec, clock = make(tmp_path, warmup_frames=3)
        folder = rec.start()
        record(rec, clock, n=6, dt=0.05)
        rec.stop()
        saved = json.loads((folder / "meta.json").read_text())
        assert saved["source"] == "camera 0" and saved["started_at"] == "2026-10-11T14:30:05"
        assert saved["status"] == "complete" and saved["codec"] in ("mp4v", "MJPG")

    def test_stopping_during_warmup_still_keeps_what_was_captured(self, tmp_path):
        rec, clock = make(tmp_path, warmup_frames=10)
        folder = rec.start()
        record(rec, clock, n=4, dt=0.1)
        meta = rec.stop()
        assert meta["frames"] == 4 and meta["fps_measured"] is True
        assert any(p.name.startswith("video.") for p in folder.iterdir())

    def test_a_single_frame_recording_uses_the_fallback_rate(self, tmp_path):
        rec, clock = make(tmp_path, warmup_frames=5, fallback_fps=12)
        rec.start()
        rec.write(frame(0))
        meta = rec.stop()
        assert meta["frames"] == 1 and meta["fps"] == 12 and meta["fps_measured"] is False

    def test_recording_nothing_leaves_no_folder(self, tmp_path):
        rec, _ = make(tmp_path)
        folder = rec.start()
        assert rec.stop() == {"status": "empty", "frames": 0}
        assert not folder.exists()

    def test_a_resolution_change_mid_recording_is_resized_not_fatal(self, tmp_path):
        rec, clock = make(tmp_path, warmup_frames=3)
        rec.start()
        record(rec, clock, n=4, dt=0.1)
        rec.write(frame(9, size=(32, 24)))
        meta = rec.stop()
        assert meta["frames"] == 5 and meta["frames_resized"] == 1

    def test_elapsed_follows_the_clock_for_the_rec_timer(self, tmp_path):
        rec, clock = make(tmp_path)
        assert rec.elapsed_s == 0.0
        rec.start()
        clock.advance(12.5)
        assert rec.elapsed_s == pytest.approx(12.5)

    def test_misuse_is_an_error_not_silence(self, tmp_path):
        rec, clock = make(tmp_path)
        with pytest.raises(RuntimeError, match="not recording"):
            rec.write(frame(0))
        with pytest.raises(RuntimeError, match="not recording"):
            rec.stop()
        rec.start()
        with pytest.raises(RuntimeError, match="already started"):
            rec.start()
        rec.write(frame(0)); rec.stop()
        with pytest.raises(RuntimeError, match="not recording"):
            rec.write(frame(0))                         # finished recorders stay finished

    def test_rejects_a_warmup_too_short_to_measure_a_rate(self, tmp_path):
        with pytest.raises(ValueError, match="warmup_frames"):
            Recorder(tmp_path, "x", warmup_frames=1)

    def test_two_recordings_in_one_second_do_not_collide(self, tmp_path):
        names = []
        for _ in range(2):
            rec, clock = make(tmp_path, warmup_frames=2)
            names.append(rec.start().name)
            record(rec, clock, n=3, dt=0.1)
            rec.stop()
        assert names == ["2026-10-11_14-30-05", "2026-10-11_14-30-05_2"]


def test_a_finished_recording_appears_in_the_listing_ready_to_replay(tmp_path):
    rec, clock = make(tmp_path, warmup_frames=3)
    rec.start()
    record(rec, clock, n=10, dt=0.1)
    rec.stop()
    (row,) = list_recordings(tmp_path)
    assert row["complete"] is True and row["frames"] == 10 and row["source"] == "camera 0"
    assert row["label"].startswith("2026-10-11 14:30:05 · 1.0 s")


def test_an_unfinished_recording_is_listed_but_flagged_incomplete(tmp_path):
    rec, clock = make(tmp_path, warmup_frames=2)
    rec.start()
    record(rec, clock, n=4, dt=0.1)                     # app "killed": stop() never called
    rec._writer.release()
    (row,) = list_recordings(tmp_path)
    assert row["complete"] is False
