"""Live labelling (2026-10-12): the session files, the worker hook, the dashboard API."""
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

from camera.capture import PacedVideoCapture, load_config
from camera.dashboard import create_app
from camera.label_session import LabelSession
from camera.station import StationController
from camera.worker import CameraWorker, SharedState

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "development"))

TILE_BGR = (30, 90, 180)


def record(seq=1, grade="Grade A", **extra):
    base = {
        "seq": seq, "grade": grade, "rule_grade": grade, "crack_severity": "none", "crack_detected": False,
        "crack_length_px": 0.0, "corner_broken": False, "corner_fill_ratio": 0.95,
        "corner_missing_extent_fraction": 0.03, "corner_missing_area_px": 0.0, "sharpness": 12.0,
        "model_grade": None, "model_confidence": None,
    }
    base.update(extra)
    return base


def add(session, seq=1, **extra):
    tile = np.full((40, 50, 3), 120, np.uint8)
    frame = np.full((200, 300, 3), 60, np.uint8)
    return session.add_tile(record(seq, **extra), tile, frame, (100, 50, 50, 40))


class TestSession:
    def test_tile_is_saved_losslessly_with_its_readings(self, tmp_path):
        s = LabelSession(tmp_path)
        tid = add(s, 1, crack_severity="minor", crack_length_px=77.0)
        for sub in ("tiles", "context", "frames"):
            f = s.folder / sub / f"{tid}.png"
            assert f.is_file() and f.read_bytes().startswith(b"\x89PNG")
        assert cv2.imread(str(s.folder / "tiles" / f"{tid}.png")).shape == (40, 50, 3)
        assert cv2.imread(str(s.folder / "frames" / f"{tid}.png")).shape == (200, 300, 3)
        item = json.loads((s.folder / "human_labels.json").read_text())["items"][0]
        assert item["crack_severity"] == "minor" and item["crack_length_px"] == 77.0 and item["label"] is None

    def test_full_frame_can_be_switched_off(self, tmp_path):
        s = LabelSession(tmp_path, save_full_frame=False)
        add(s, 1)
        assert not (s.folder / "frames").exists()

    def test_batch_verdict_applies_to_later_tiles_only(self, tmp_path):
        s = LabelSession(tmp_path, batch_label="fine", batch_tier="3A")
        add(s, 1)
        s.set_batch(label="chipped", tier=None)
        add(s, 2)
        rows = {i["seq"]: i for i in json.loads((s.folder / "human_labels.json").read_text())["items"]}
        assert (rows[1]["label"], rows[1]["tier"]) == ("fine", "3A")
        assert (rows[2]["label"], rows[2]["tier"]) == ("chipped", None)

    def test_verdict_defaults_to_the_last_tile_and_none_clears(self, tmp_path):
        s = LabelSession(tmp_path)
        add(s, 1)
        add(s, 2)
        s.set_verdict(label="cracked")
        s.set_verdict(1, label="fine", note=" back face ")
        assert s.summary()["labelled"] == 2
        assert s.set_verdict(1, label=None)["label"] is None
        assert s.summary()["labelled"] == 1
        # leaving a field out leaves it alone
        s.set_verdict(2, tier="3B")
        assert s.set_verdict(2, note="x")["tier"] == "3B"

    def test_bad_input_is_refused_and_changes_nothing(self, tmp_path):
        s = LabelSession(tmp_path)
        with pytest.raises(ValueError):
            s.set_verdict(label="fine")          # no tile yet
        add(s, 1)
        with pytest.raises(ValueError):
            s.set_verdict(label="broken")
        with pytest.raises(ValueError):
            s.set_verdict(tier="2B")
        with pytest.raises(ValueError):
            s.set_verdict(9, label="fine")       # not in this session
        with pytest.raises(ValueError):
            LabelSession(tmp_path, batch_label="nope")
        assert s.summary()["labelled"] == 0

    def test_two_sessions_in_the_same_second_get_different_folders(self, tmp_path):
        from datetime import datetime
        now = datetime(2026, 10, 12, 9, 0, 0)
        assert LabelSession(tmp_path, now).folder != LabelSession(tmp_path, now).folder

    def test_folder_opens_in_the_annotator_and_feeds_tune_from_labels(self, tmp_path):
        from annotation_tools import LabelStore, load_tiles
        from review_tools import parse_export

        s = LabelSession(tmp_path, batch_label="fine")
        add(s, 1)
        add(s, 2)
        s.set_verdict(2, label="chipped", tier="4")
        tiles, kind = load_tiles(s.folder)
        assert kind == "dataset" and [t.seq for t in tiles] == [1, 2]
        store = LabelStore(s.folder, tiles)
        assert (store.get(tiles[1].id).label, store.get(tiles[1].id).tier) == ("chipped", "4")
        items = parse_export(json.loads((s.folder / "human_labels.json").read_text()))
        assert sorted(i.label for i in items) == ["chipped", "fine"]


# ---------------------------------------------------------------- worker + API

def config(tmp_path):
    cfg = load_config()
    px = cv2.cvtColor(np.uint8([[TILE_BGR]]), cv2.COLOR_BGR2HSV)[0][0]
    cfg["segmentation"]["hsv_lower"] = [max(0, int(px[0]) - 10), 100, 100]
    cfg["segmentation"]["hsv_upper"] = [min(179, int(px[0]) + 10), 255, 255]
    cfg["segmentation"]["belt_roi"] = {"mode": "off"}
    cfg["segmentation"]["min_tile_area_px"] = 1000
    cfg["capture_snapshots"]["enabled"] = False
    cfg["grading_model"]["enabled"] = False
    cfg["trigger_line"].update(position=0.5, orientation="horizontal", direction="both", hysteresis=0.0, max_step=None)
    cfg["labelling"] = {"output_dir": str(tmp_path / "labelled"), "save_full_frame": True}
    cfg["recording"]["output_dir"] = str(tmp_path / "recordings")
    return cfg


def frame_with_tile(cy, size=60):
    f = np.full((300, 200, 3), (40, 40, 40), np.uint8)
    cv2.rectangle(f, (70, cy - size // 2), (70 + size, cy + size // 2), TILE_BGR, -1)
    return f


def pass_a_tile(worker):
    for cy in (60, 90, 120, 135, 165, 190):
        worker._safe_step(frame_with_tile(cy))


class FakeCapture:
    def read_frame(self):
        raise StopIteration


def test_worker_saves_each_crossing_tile_into_the_open_session(tmp_path):
    state = SharedState()
    w = CameraWorker(FakeCapture(), config(tmp_path), state)
    w.start_labelling(batch_label="fine")
    pass_a_tile(w)
    s = state.status()["labelling"]
    assert s["count"] == 1 and s["labelled"] == 1 and s["last"]["label"] == "fine"
    w.label_tile(label="cracked", tier="4")
    assert state.status()["labelling"]["last"]["label"] == "cracked"
    folder = Path(s["path"])
    assert len(list((folder / "tiles").glob("*.png"))) == 1 and len(list((folder / "frames").glob("*.png"))) == 1
    assert w.stop_labelling()["count"] == 1
    assert state.status()["labelling"] is None
    pass_a_tile(w)  # a tile after the session closed is not saved anywhere
    assert len(list((folder / "tiles").glob("*.png"))) == 1


def test_no_session_means_no_files_and_clear_errors(tmp_path):
    w = CameraWorker(FakeCapture(), config(tmp_path), SharedState())
    pass_a_tile(w)
    assert not (tmp_path / "labelled").exists()
    with pytest.raises(RuntimeError):
        w.label_tile(label="fine")
    with pytest.raises(RuntimeError):
        w.stop_labelling()
    w.start_labelling()
    with pytest.raises(RuntimeError):
        w.start_labelling()


def test_a_failing_disk_costs_the_label_not_the_inspection(tmp_path, monkeypatch):
    state = SharedState()
    w = CameraWorker(FakeCapture(), config(tmp_path), state)
    w.start_labelling()

    def full_disk(*a, **k):
        raise OSError("No space left on device")

    monkeypatch.setattr(LabelSession, "add_tile", full_disk)
    pass_a_tile(w)
    s = state.status()
    assert s["tile_count"] == 1                       # still graded and counted
    assert s["errors"]["count"] == 1 and "No space" in s["errors"]["last"]


@pytest.fixture
def api(tmp_path):
    cfg = config(tmp_path)
    state = SharedState()
    ctl = StationController(cfg, state, lister=lambda skip_usb=(): {"usb": [], "csi": []},
                            file_factory=lambda p, loop, realtime: PacedVideoCapture(p, loop=loop, realtime=False))
    client = create_app(state, controller=ctl).test_client()
    client.ctl, client.state = ctl, state
    yield client
    ctl.shutdown()


def post(c, url, **body):
    return c.post(url, json=body)


class TestApi:
    def test_status_has_no_labelling_until_a_session_opens(self, api):
        assert api.get("/api/status").get_json()["labelling"] is None

    def test_start_batch_label_stop(self, api):
        r = post(api, "/api/label/start", label="fine")
        assert r.status_code == 200 and r.get_json()["labelling"]["batch_label"] == "fine"
        assert api.get("/api/status").get_json()["labelling"]["folder"]
        assert post(api, "/api/label/batch", label="cracked", tier="3B").get_json()["labelling"]["batch_tier"] == "3B"
        assert post(api, "/api/label/start").status_code == 409            # already open
        assert post(api, "/api/label/tile", label="fine").status_code == 400  # no tile yet
        assert post(api, "/api/label/stop").get_json()["labelling"]["count"] == 0
        assert api.get("/api/status").get_json()["labelling"] is None
        assert post(api, "/api/label/stop").status_code == 409

    def test_bad_values_are_400_and_viewer_only_is_503(self, api):
        assert post(api, "/api/label/start", label="broken").status_code == 400
        post(api, "/api/label/start")
        assert post(api, "/api/label/batch", tier="9Z").status_code == 400
        assert post(api, "/api/label/tile").status_code == 400            # nothing to set
        assert post(api, "/api/label/tile", seq="x", label="fine").status_code == 400
        viewer = create_app(SharedState()).test_client()
        assert post(viewer, "/api/label/start").status_code == 503
