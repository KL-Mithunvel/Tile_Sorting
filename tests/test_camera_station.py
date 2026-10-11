"""Switching the video source, replaying files, and recording, against fake
cameras (2026-10-11). Real worker threads, no hardware."""
import re
import time

import cv2
import numpy as np
import pytest

from camera.capture import PacedVideoCapture
from camera.station import StationController
from camera.capture import load_config
from camera.worker import CameraWorker, SharedState

TILE_BGR = (30, 90, 180)


def make_config():
    """Real config with the belt ROI off and a synthetic orange tile's hue."""
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


def wait_for(cond, timeout=5.0, step=0.01):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return False


class FeedCapture:
    """A fake camera. Yields `frames` once then ends (like a file), or, with
    `endless`, cycles them forever like a live camera."""

    def __init__(self, frames, endless=False, delay=0.002):
        self.frames, self.endless, self.delay = list(frames), endless, delay
        self.started = self.stopped = False
        self.i = 0

    def start(self):
        self.started, self.stopped, self.i = True, False, 0

    def stop(self):
        self.stopped = True

    def read_frame(self):
        time.sleep(self.delay)
        if self.i >= len(self.frames):
            if not self.endless:
                raise StopIteration
            self.i = 0
        f = self.frames[self.i]
        self.i += 1
        return f


class BrokenCapture(FeedCapture):
    def start(self):
        raise RuntimeError("could not open device index 9")


def tile_pass():
    """Frames of one tile crossing the line at y=150."""
    return [frame_with_tile(cy) for cy in (60, 90, 120, 135, 165, 190, 220)]


@pytest.fixture
def station(tmp_path):
    cfg = make_config()
    cfg["recording"]["output_dir"] = str(tmp_path / "recordings")
    cfg["recording"]["warmup_frames"] = 3
    state = SharedState()
    made = []

    def usb(c):
        cap = FeedCapture(tile_pass(), endless=True)
        made.append(("usb", c["camera"]["device_index"], cap))
        return cap

    ctl = StationController(
        cfg, state,
        lister=lambda skip_usb=(): {"usb": [{"index": 0, "width": 640, "height": 480, "fps": 30.0},
                                {"index": 2, "width": 1280, "height": 720, "fps": 30.0}], "csi": []},
        usb_factory=usb,
    )
    ctl.made = made
    yield ctl
    ctl.shutdown()


class TestOpeningCameras:
    def test_open_camera_uses_that_index_and_starts_the_feed(self, station):
        src = station.open_camera(2)
        assert src["label"] == "Camera 2" and src["kind"] == "camera"
        assert station.made[-1][:2] == ("usb", 2)
        assert wait_for(lambda: station._state.get_frame() is not None)
        assert station._state.status()["source"]["status"] == "running"

    def test_a_negative_index_is_refused(self, station):
        with pytest.raises(ValueError, match=">= 0"):
            station.open_camera(-1)

    def test_switching_camera_stops_the_old_one_first(self, station):
        station.open_camera(0)
        first = station.made[-1][2]
        station.open_camera(2)
        assert first.stopped is True
        assert station.made[-1][1] == 2

    def test_a_camera_that_wont_open_leaves_the_previous_source_running(self, station):
        station.open_camera(0)
        assert wait_for(lambda: station._state.get_frame() is not None)
        station._usb_factory = lambda c: BrokenCapture([])
        with pytest.raises(RuntimeError, match="could not open"):
            station.open_camera(9)
        # the old camera was put back and is delivering frames again
        assert station.worker.source["index"] == 0
        station._state.update_frame(b"stale")
        assert wait_for(lambda: station._state.get_frame() != b"stale")
        assert station._state.status()["source"]["status"] == "running"

    def test_sources_lists_scanned_cameras_and_keeps_the_one_in_use(self, station):
        station.scan_cameras(wait=True)
        station.open_camera(5)                      # not in the scan: busy cameras can't be probed
        usb = station.sources()["cameras"]["usb"]
        assert [c["index"] for c in usb] == [0, 2, 5]
        assert next(c for c in usb if c["index"] == 5)["in_use"] is True

    def test_scan_runs_in_the_background_and_reports_when_done(self, station):
        assert station.sources()["scanned"] is False
        station.scan_cameras(wait=True)
        s = station.sources()
        assert s["scanned"] is True and s["scanning"] is False

    def test_a_scan_never_probes_the_camera_that_is_streaming(self, station):
        """Regression, found on the real webcam: loading the dashboard started a
        scan that opened camera 0 a second time, which on Windows takes the
        device from the running capture and freezes the live feed."""
        asked = []
        station._lister = lambda skip_usb=(): asked.append(set(skip_usb)) or {"usb": [], "csi": []}
        station.scan_cameras(wait=True)
        assert asked[-1] == set()                      # nothing open yet: probe everything

        station.open_camera(3)
        station.scan_cameras(wait=True)
        assert asked[-1] == {3}                        # camera 3 is streaming: leave it alone

    def test_a_file_source_has_no_camera_to_protect(self, station, tmp_path):
        asked = []
        station._lister = lambda skip_usb=(): asked.append(set(skip_usb)) or {"usb": [], "csi": []}
        station.worker.switch_source(FeedCapture(tile_pass(), endless=True), {"kind": "file", "label": "x"})
        station.scan_cameras(wait=True)
        assert asked[-1] == set()

    def test_a_crashing_scan_does_not_break_the_page(self, station):
        station._lister = lambda skip_usb=(): (_ for _ in ()).throw(OSError("probe blew up"))
        station.scan_cameras(wait=True)
        assert station.sources()["cameras"] == {"usb": [], "csi": []}


class TestSwitchingResetsResults:
    def test_counts_from_one_source_do_not_carry_into_the_next(self, station):
        station.worker.switch_source(FeedCapture(tile_pass()), {"kind": "file", "label": "a"})
        assert wait_for(lambda: station._state.status()["source"]["status"] == "finished")
        assert station._state.status()["tile_count"] == 1

        station.worker.switch_source(FeedCapture([frame_with_tile(60)] * 3), {"kind": "file", "label": "b"})
        assert wait_for(lambda: station._state.status()["source"]["status"] == "finished")
        s = station._state.status()
        assert s["tile_count"] == 0 and s["tally"] == {} and s["recent_tiles"] == []

    def test_each_source_is_tracked_from_scratch(self, station):
        for label in ("a", "b"):
            station.worker.switch_source(FeedCapture(tile_pass()), {"kind": "file", "label": label})
            assert wait_for(lambda: station._state.status()["source"]["status"] == "finished")
            assert station._state.status()["tile_count"] == 1     # not 2 on the second run


def write_avi(path, frames, fps=20):
    h, w = frames[0].shape[:2]
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (w, h))
    for f in frames:
        out.write(f)
    out.release()


class TestOpeningFiles:
    def test_plays_a_recording_through_the_pipeline_then_reports_finished(self, station, tmp_path):
        rec = tmp_path / "recordings" / "2026-10-11_14-30-05"
        rec.mkdir(parents=True)
        write_avi(rec / "video.avi", tile_pass())
        station._file_factory = lambda p, loop, realtime: PacedVideoCapture(p, loop=loop, realtime=False)

        src = station.open_recording("2026-10-11_14-30-05")
        assert src["kind"] == "file" and src["recording"] is True
        assert src["label"] == "2026-10-11_14-30-05"
        assert wait_for(lambda: station._state.status()["source"]["status"] == "finished")
        s = station._state.status()
        assert s["tile_count"] == 1
        assert s["source"]["playback"]["frame_count"] == 7

    def test_files_outside_data_are_refused_before_anything_is_touched(self, station, tmp_path):
        station.open_camera(0)
        outside = tmp_path / "elsewhere.avi"
        write_avi(outside, tile_pass())
        with pytest.raises(ValueError, match="inside the data"):
            station.open_file(str(outside))
        assert station.worker.source["label"] == "Camera 0"      # untouched

    def test_unknown_recording_id(self, station):
        with pytest.raises(ValueError, match="no recording named"):
            station.open_recording("2020-01-01_00-00-00")

    def test_pause_holds_the_picture_and_resume_continues(self, station, tmp_path):
        rec = tmp_path / "recordings" / "r"
        rec.mkdir(parents=True)
        write_avi(rec / "video.avi", tile_pass() * 5, fps=60)
        station.open_file(str(rec / "video.avi"), loop=True)
        station.set_paused(True)
        assert wait_for(lambda: station._state.status()["source"]["playback"] is not None)
        time.sleep(0.2)
        a = station._state.status()["source"]["playback"]["position"]
        time.sleep(0.3)
        b = station._state.status()["source"]["playback"]["position"]
        assert a == b and station._state.status()["source"]["playback"]["paused"] is True
        station.set_paused(False)
        assert wait_for(lambda: station._state.status()["source"]["playback"]["position"] != b)

    def test_loop_can_be_toggled_while_playing(self, station, tmp_path):
        rec = tmp_path / "recordings" / "r"
        rec.mkdir(parents=True)
        write_avi(rec / "video.avi", tile_pass(), fps=100)
        station.open_file(str(rec / "video.avi"), loop=False)
        station.set_loop(True)
        time.sleep(0.5)                                          # several times the clip's length
        assert station._state.status()["source"]["status"] == "running"

    def test_playback_controls_need_a_file(self, station):
        station.open_camera(0)
        with pytest.raises(ValueError, match="only apply when a file is open"):
            station.set_paused(True)


class TestRecording:
    def test_records_the_live_feed_into_a_date_time_folder_and_it_replays(self, station, tmp_path):
        station.open_camera(0)
        assert wait_for(lambda: station._state.get_frame() is not None)
        info = station.start_recording()
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(_\d+)?", info["folder"])
        assert wait_for(lambda: station._state.status()["recording"] is not None)
        time.sleep(0.3)
        meta = station.stop_recording()

        assert meta["status"] == "complete" and meta["frames"] > 5
        assert meta["source"] == "Camera 0"
        folder = tmp_path / "recordings" / info["folder"]
        assert (folder / meta["video"]).is_file() and (folder / "meta.json").is_file()
        s = station._state.status()
        assert s["recording"] is None and s["last_recording"]["folder"] == info["folder"]

        # ...and the recording can be opened straight back
        station._file_factory = lambda p, loop, realtime: PacedVideoCapture(p, loop=loop, realtime=False)
        assert station.open_recording(info["folder"])["kind"] == "file"
        assert wait_for(lambda: station._state.status()["source"]["status"] == "finished")

    def test_status_shows_progress_while_recording(self, station):
        station.open_camera(0)
        station.start_recording()
        assert wait_for(lambda: (station._state.status()["recording"] or {}).get("frames", 0) > 3)
        r = station._state.status()["recording"]
        assert r["active"] is True and r["elapsed_s"] >= 0
        station.stop_recording()

    def test_cannot_record_a_file_source(self, station):
        station.worker.switch_source(FeedCapture(tile_pass(), endless=True), {"kind": "file", "label": "x"})
        with pytest.raises(ValueError, match="live camera"):
            station.start_recording()

    def test_cannot_record_with_no_source(self, station):
        with pytest.raises(ValueError, match="live camera"):
            station.start_recording()

    def test_double_start_and_stop_without_start_are_errors(self, station):
        station.open_camera(0)
        with pytest.raises(RuntimeError, match="not recording"):
            station.stop_recording()
        station.start_recording()
        with pytest.raises(RuntimeError, match="already recording"):
            station.start_recording()
        station.stop_recording()

    def test_switching_source_finishes_the_recording_first(self, station, tmp_path):
        station.open_camera(0)
        info = station.start_recording()
        time.sleep(0.2)
        station.open_camera(2)
        meta = station._state.status()["last_recording"]
        assert meta["status"] == "complete" and meta["folder"] == info["folder"]

    def test_shutdown_mid_recording_still_saves_it(self, station, tmp_path):
        station.open_camera(0)
        info = station.start_recording()
        time.sleep(0.2)
        station.shutdown()
        import json
        meta = json.loads((tmp_path / "recordings" / info["folder"] / "meta.json").read_text())
        assert meta["status"] == "complete"

    def test_the_raw_frame_is_recorded_not_the_annotated_one(self, station, tmp_path):
        station.open_camera(0)
        info = station.start_recording()
        time.sleep(0.25)
        meta = station.stop_recording()
        cap = cv2.VideoCapture(str(tmp_path / "recordings" / info["folder"] / meta["video"]))
        ok, f = cap.read()
        cap.release()
        # The overlay draws a thin grey belt outline / yellow line; the source frames are
        # a flat dark background with one orange square, so no yellow pixels may appear.
        yellow = np.all(np.abs(f.astype(int) - np.array([0, 200, 255])) < 40, axis=2).sum()
        assert ok and yellow == 0


class TestCameraTrouble:
    def test_an_unplugged_camera_is_reported_and_recovery_clears_it(self, monkeypatch):
        monkeypatch.setattr(CameraWorker, "_READ_FAILURE_SECONDS", 0.05)

        class Flaky:
            def __init__(self):
                self.broken = False
                self.n = 0

            def start(self): pass
            def stop(self): pass

            def read_frame(self):
                time.sleep(0.002)
                if self.broken:
                    raise RuntimeError("camera gone")
                return frame_with_tile(60)

        cam, state = Flaky(), SharedState()
        w = CameraWorker(cam, make_config(), state)
        w.start()
        assert wait_for(lambda: state.get_frame() is not None)
        cam.broken = True
        assert wait_for(lambda: state.status()["source"]["status"] == "error")
        assert "stopped delivering" in state.status()["source"]["detail"]
        cam.broken = False
        assert wait_for(lambda: state.status()["source"]["status"] == "running")
        w.stop()

    def test_the_worker_can_start_with_no_source_at_all(self):
        state = SharedState()
        w = CameraWorker(None, make_config(), state)
        w.start()
        assert state.status()["source"]["status"] == "none"
        w.stop()
