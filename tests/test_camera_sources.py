"""Recording folders, the replayable-file listing, safe path opening, and the
real-time-paced file player (video-source feature, 2026-10-11)."""
import json
from datetime import datetime

import cv2
import numpy as np
import pytest

import camera.capture as capture_mod
import camera.sources as sources
from camera.capture import PacedVideoCapture
from camera.sources import (
    describe_recording,
    list_recordings,
    new_recording_dir,
    recording_name,
    resolve_media_path,
)


def write_avi(path, n_frames=6, fps=10, size=(64, 48)):
    """A tiny real video (MJPG/avi is available in every OpenCV build)."""
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, size)
    for i in range(n_frames):
        w.write(np.full((size[1], size[0], 3), (i * 25) % 256, np.uint8))
    w.release()
    return path


class TestNaming:
    def test_name_is_date_and_time_sortable_and_filesystem_safe(self):
        assert recording_name(datetime(2026, 10, 11, 14, 30, 5)) == "2026-10-11_14-30-05"
        names = [recording_name(datetime(2026, 10, 11, h, 0, 0)) for h in (9, 10, 14)]
        assert names == sorted(names)
        assert ":" not in names[0]

    def test_two_recordings_in_the_same_second_do_not_overwrite(self, tmp_path):
        now = datetime(2026, 10, 11, 14, 30, 5)
        a, b, c = (new_recording_dir(tmp_path, now) for _ in range(3))
        assert [p.name for p in (a, b, c)] == ["2026-10-11_14-30-05", "2026-10-11_14-30-05_2", "2026-10-11_14-30-05_3"]
        assert all(p.is_dir() for p in (a, b, c))

    def test_creates_the_root_if_missing(self, tmp_path):
        d = new_recording_dir(tmp_path / "nope" / "recordings", datetime(2026, 1, 2, 3, 4, 5))
        assert d.is_dir()


class TestListing:
    def make(self, root, name, meta=None, video="video.avi"):
        folder = root / name
        folder.mkdir(parents=True)
        write_avi(folder / video)
        if meta is not None:
            (folder / "meta.json").write_text(json.dumps(meta))
        return folder

    def test_lists_newest_first_with_a_readable_label(self, tmp_path):
        self.make(tmp_path, "2026-10-11_09-00-00", {"status": "complete", "duration_s": 3.0, "width": 640, "height": 480})
        self.make(tmp_path, "2026-10-11_14-30-05", {"status": "complete", "duration_s": 12.34, "width": 640, "height": 480, "frames": 185})
        rows = list_recordings(tmp_path)
        assert [r["id"] for r in rows] == ["2026-10-11_14-30-05", "2026-10-11_09-00-00"]
        assert rows[0]["label"] == "2026-10-11 14:30:05 · 12.3 s · 640×480"
        assert rows[0]["frames"] == 185 and rows[0]["complete"] is True

    def test_a_recording_with_no_or_corrupt_meta_is_still_listed(self, tmp_path):
        self.make(tmp_path, "2026-10-11_09-00-00", meta=None)
        f = self.make(tmp_path, "2026-10-11_10-00-00", meta=None)
        (f / "meta.json").write_text("{not json")
        rows = list_recordings(tmp_path)
        assert len(rows) == 2 and not any(r["complete"] for r in rows)

    def test_folders_without_a_video_and_a_missing_root_are_skipped(self, tmp_path):
        (tmp_path / "2026-10-11_09-00-00").mkdir()
        assert list_recordings(tmp_path) == []
        assert list_recordings(tmp_path / "does-not-exist") == []

    def test_describe_returns_none_without_a_video(self, tmp_path):
        assert describe_recording(tmp_path) is None


class TestResolveMediaPath:
    @pytest.fixture
    def roots(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir()
        return data, [data]

    def test_accepts_a_video_inside_the_allowed_root(self, roots):
        data, rs = roots
        v = write_avi(data / "clip.avi")
        assert resolve_media_path(str(v), rs) == v.resolve()

    def test_strips_quotes_pasted_from_explorer(self, roots):
        data, rs = roots
        v = write_avi(data / "clip.avi")
        assert resolve_media_path(f'"{v}"', rs) == v.resolve()

    @pytest.mark.parametrize("bad", ["", "   ", None])
    def test_rejects_an_empty_path(self, roots, bad):
        with pytest.raises(ValueError, match="no file path"):
            resolve_media_path(bad, roots[1])

    def test_rejects_a_missing_file(self, roots):
        with pytest.raises(ValueError, match="not found"):
            resolve_media_path(str(roots[0] / "nope.avi"), roots[1])

    def test_rejects_a_non_video_extension(self, roots):
        data, rs = roots
        (data / "notes.txt").write_text("x")
        with pytest.raises(ValueError, match="not a video"):
            resolve_media_path(str(data / "notes.txt"), rs)

    def test_rejects_a_video_outside_data(self, roots, tmp_path):
        outside = write_avi(tmp_path / "outside.avi")
        with pytest.raises(ValueError, match="inside the data"):
            resolve_media_path(str(outside), roots[1])

    def test_rejects_dot_dot_escapes(self, roots, tmp_path):
        data, rs = roots
        write_avi(tmp_path / "secret.avi")
        with pytest.raises(ValueError, match="inside the data"):
            resolve_media_path(str(data / ".." / "secret.avi"), rs)

    def test_rejects_a_directory(self, roots):
        data, rs = roots
        (data / "folder.avi").mkdir()
        with pytest.raises(ValueError, match="not a file"):
            resolve_media_path(str(data / "folder.avi"), rs)


class TestPacedVideoCapture:
    @pytest.fixture
    def clock(self, monkeypatch):
        """A fake clock: sleeping advances it instantly, so pacing is testable
        without waiting."""
        t = {"now": 100.0, "slept": 0.0}
        monkeypatch.setattr(capture_mod, "_now", lambda: t["now"])

        def fake_sleep(s):
            t["now"] += s
            t["slept"] += s

        monkeypatch.setattr(capture_mod, "_sleep", fake_sleep)
        return t

    def test_plays_every_frame_then_stops(self, tmp_path, clock):
        cap = PacedVideoCapture(str(write_avi(tmp_path / "v.avi", n_frames=5)))
        cap.start()
        frames = []
        with pytest.raises(StopIteration):
            while True:
                frames.append(cap.read_frame())
        assert len(frames) == 5 and cap.frame_count == 5
        cap.stop()

    def test_paces_at_the_files_own_frame_rate(self, tmp_path, clock):
        cap = PacedVideoCapture(str(write_avi(tmp_path / "v.avi", n_frames=10, fps=10)))
        cap.start()
        for _ in range(10):
            cap.read_frame()
        assert clock["slept"] == pytest.approx(1.0, abs=0.05)   # 10 frames at 10 fps = 1 s
        cap.stop()

    def test_realtime_off_does_not_sleep(self, tmp_path, clock):
        cap = PacedVideoCapture(str(write_avi(tmp_path / "v.avi", n_frames=5)), realtime=False)
        cap.start()
        for _ in range(5):
            cap.read_frame()
        assert clock["slept"] == 0.0
        cap.stop()

    def test_loop_restarts_instead_of_ending(self, tmp_path, clock):
        cap = PacedVideoCapture(str(write_avi(tmp_path / "v.avi", n_frames=3)), loop=True)
        cap.start()
        positions = []
        for _ in range(8):
            cap.read_frame()
            positions.append(cap.position)
        assert positions == [0, 1, 2, 0, 1, 2, 0, 1]
        cap.stop()

    def test_pause_holds_the_current_frame_and_keeps_ticking(self, tmp_path, clock):
        cap = PacedVideoCapture(str(write_avi(tmp_path / "v.avi", n_frames=5)))
        cap.start()
        cap.read_frame()
        held = cap.read_frame()
        cap.paused = True
        again = [cap.read_frame() for _ in range(3)]
        assert all(np.array_equal(held, f) for f in again) and cap.position == 1
        cap.paused = False
        cap.read_frame()
        assert cap.position == 2           # resumes where it stopped, no skipped frames
        cap.stop()

    def test_a_bad_frame_rate_in_the_container_falls_back_to_a_sane_one(self, tmp_path, clock, monkeypatch):
        cap = PacedVideoCapture(str(write_avi(tmp_path / "v.avi")))
        real_get = cv2.VideoCapture.get
        monkeypatch.setattr(cv2.VideoCapture, "get", lambda self, p: 0.0 if p == cv2.CAP_PROP_FPS else real_get(self, p))
        cap.start()
        assert cap.fps == 30.0
        cap.stop()

    def test_resynchronises_after_a_long_stall_instead_of_fast_forwarding(self, tmp_path, clock):
        cap = PacedVideoCapture(str(write_avi(tmp_path / "v.avi", n_frames=6, fps=10)))
        cap.start()
        cap.read_frame()
        clock["now"] += 30.0                 # the process was frozen for 30 s
        before = clock["slept"]
        for _ in range(4):
            cap.read_frame()
        assert clock["slept"] - before == pytest.approx(0.4, abs=0.15)   # still ~one interval per frame
        cap.stop()

    def test_unopenable_file_raises(self, tmp_path):
        with pytest.raises(RuntimeError, match="could not open"):
            PacedVideoCapture(str(tmp_path / "missing.avi")).start()

    def test_read_before_start_raises(self, tmp_path):
        with pytest.raises(RuntimeError, match="start"):
            PacedVideoCapture("x.avi").read_frame()


def test_module_constants_are_consistent():
    assert ".mp4" in sources.VIDEO_SUFFIXES and ".avi" in sources.VIDEO_SUFFIXES
    assert sources.RECORDING_VIDEO_NAMES[0] == "video.mp4"
