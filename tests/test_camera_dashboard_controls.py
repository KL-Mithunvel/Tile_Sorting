"""The dashboard's source / recording API and the new CLI flags (2026-10-11)."""
import time

import cv2
import numpy as np
import pytest

from camera.capture import PacedVideoCapture, load_config
from camera.dashboard import create_app
from camera.live_dashboard import main, parse_args
from camera.station import StationController
from camera.worker import SharedState

TILE_BGR = (30, 90, 180)


def frame(cy=60):
    f = np.full((300, 200, 3), (40, 40, 40), np.uint8)
    cv2.rectangle(f, (70, cy - 30), (130, cy + 30), TILE_BGR, -1)
    return f


class Cam:
    def __init__(self, fail=False):
        self.fail = fail

    def start(self):
        if self.fail:
            raise RuntimeError("could not open device index 7")

    def stop(self):
        pass

    def read_frame(self):
        time.sleep(0.002)
        return frame()


@pytest.fixture
def api(tmp_path):
    cfg = load_config()
    cfg["segmentation"]["belt_roi"] = {"mode": "off"}
    cfg["capture_snapshots"]["enabled"] = False
    cfg["grading_model"]["enabled"] = False
    cfg["recording"]["output_dir"] = str(tmp_path / "recordings")
    cfg["recording"]["warmup_frames"] = 3
    state = SharedState()
    ctl = StationController(
        cfg, state,
        lister=lambda skip_usb=(): {"usb": [{"index": 0, "width": 640, "height": 480, "fps": 30.0}], "csi": []},
        usb_factory=lambda c: Cam(fail=c["camera"]["device_index"] == 7),
        file_factory=lambda p, loop, realtime: PacedVideoCapture(p, loop=loop, realtime=False),
    )
    client = create_app(state, controller=ctl).test_client()
    client.ctl, client.state, client.tmp = ctl, state, tmp_path
    yield client
    ctl.shutdown()


def post(client, url, **body):
    return client.post(url, json=body)


class TestStatus:
    def test_status_carries_source_recording_and_whether_controls_exist(self, api):
        body = api.get("/api/status").get_json()
        assert body["controls"] is True
        assert body["source"]["status"] == "none" and body["recording"] is None

    def test_viewer_only_dashboard_reports_no_controls(self):
        body = create_app(SharedState()).test_client().get("/api/status").get_json()
        assert body["controls"] is False


class TestGuards:
    def test_no_controller_means_503(self):
        c = create_app(SharedState()).test_client()
        assert post(c, "/api/record/start").status_code == 503

    def test_disabled_controls_are_refused_with_403_and_nothing_is_listed(self, tmp_path):
        cfg = load_config()
        cfg["recording"]["output_dir"] = str(tmp_path)
        state = SharedState()
        ctl = StationController(cfg, state)
        c = create_app(state, controller=ctl, controls_enabled=False).test_client()
        for url in ("/api/source/camera", "/api/source/file", "/api/record/start", "/api/cameras/scan"):
            assert post(c, url, index=0).status_code == 403
        listing = c.get("/api/sources").get_json()
        assert listing["controls"] is False and "files" not in listing and "recordings" not in listing
        assert c.get("/api/status").get_json()["controls"] is False

    def test_a_non_json_post_is_refused_so_a_foreign_web_page_cannot_drive_it(self, api):
        r = api.post("/api/record/start", data="x=1", content_type="text/plain")
        assert r.status_code == 415
        r = api.post("/api/source/camera", data="index=0", content_type="application/x-www-form-urlencoded")
        assert r.status_code == 415


class TestCameraRoute:
    def test_selecting_a_camera(self, api):
        r = post(api, "/api/source/camera", index=2)
        assert r.status_code == 200 and r.get_json()["source"]["label"] == "Camera 2"
        assert api.get("/api/status").get_json()["source"]["label"] == "Camera 2"

    @pytest.mark.parametrize("bad", ["abc", None, -1])
    def test_bad_indexes_are_400s_not_crashes(self, api, bad):
        r = post(api, "/api/source/camera", index=bad)
        assert r.status_code == 400 and r.get_json()["ok"] is False

    def test_a_camera_that_will_not_open_is_a_409_with_the_reason(self, api):
        r = post(api, "/api/source/camera", index=7)
        assert r.status_code == 409 and "could not open" in r.get_json()["error"]


class TestFileRoute:
    def test_a_path_outside_data_is_a_400(self, api):
        outside = api.tmp / "x.avi"
        outside.write_bytes(b"")
        r = post(api, "/api/source/file", path=str(outside))
        assert r.status_code == 400 and "data" in r.get_json()["error"]

    @pytest.mark.parametrize("path", ["", "../../etc/passwd", "C:/Windows/win.ini", "data/nope.mp4"])
    def test_junk_paths_are_400s(self, api, path):
        assert post(api, "/api/source/file", path=path).status_code == 400

    def test_unknown_recording_is_a_400(self, api):
        assert post(api, "/api/source/file", recording="2020-01-01_00-00-00").status_code == 400


class TestRecordingRoutes:
    def test_record_then_list_then_replay(self, api):
        post(api, "/api/source/camera", index=0)
        r = post(api, "/api/record/start")
        assert r.status_code == 200 and r.get_json()["folder"]
        time.sleep(0.3)
        r = post(api, "/api/record/stop")
        meta = r.get_json()["recording"]
        assert r.status_code == 200 and meta["status"] == "complete" and meta["frames"] > 3

        listing = api.get("/api/sources").get_json()
        assert len(listing["recordings"]) == 1
        rec_id = listing["recordings"][0]["id"]
        assert api.get("/api/status").get_json()["last_recording"]["folder"] == rec_id

        r = post(api, "/api/source/file", recording=rec_id)
        assert r.status_code == 200 and r.get_json()["source"]["kind"] == "file"

    def test_recording_with_no_camera_is_a_400(self, api):
        assert post(api, "/api/record/start").status_code == 400

    def test_stop_without_start_and_double_start_are_409s(self, api):
        post(api, "/api/source/camera", index=0)
        assert post(api, "/api/record/stop").status_code == 409
        assert post(api, "/api/record/start").status_code == 200
        assert post(api, "/api/record/start").status_code == 409
        post(api, "/api/record/stop")


class TestPlaybackRoute:
    def test_needs_a_file_source(self, api):
        post(api, "/api/source/camera", index=0)
        assert post(api, "/api/source/playback", paused=True).status_code == 400

    def test_needs_something_to_change(self, api):
        assert post(api, "/api/source/playback").status_code == 400


class TestSourcesListing:
    def test_first_listing_starts_a_background_scan_and_cameras_appear(self, api):
        first = api.get("/api/sources").get_json()
        assert first["controls"] is True
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not api.get("/api/sources").get_json()["scanned"]:
            time.sleep(0.02)
        done = api.get("/api/sources").get_json()
        assert done["scanned"] and [c["index"] for c in done["cameras"]["usb"]] == [0]

    def test_scan_endpoint(self, api):
        assert post(api, "/api/cameras/scan").status_code == 200


class TestCli:
    def test_new_flags_parse(self):
        a = parse_args(["--device", "1"])
        assert a.device == 1 and a.video is None and a.loop is False
        a = parse_args(["--video", "data/x.mp4", "--loop"])
        assert a.video == "data/x.mp4" and a.loop is True

    def test_device_and_video_together_is_a_usage_error(self, capsys):
        with pytest.raises(SystemExit) as e:
            main(["--device", "0", "--video", "data/x.mp4"])
        assert e.value.code == 2 and "pick one" in capsys.readouterr().err

    def test_loop_without_video_is_a_usage_error(self, capsys):
        with pytest.raises(SystemExit) as e:
            main(["--loop"])
        assert e.value.code == 2 and "--video" in capsys.readouterr().err

    def test_headless_with_an_unopenable_video_exits_1(self, capsys):
        with pytest.raises(SystemExit) as e:
            main(["--no-dashboard", "--video", "data/definitely-not-here.mp4"])
        assert e.value.code == 1 and "Failed to start" in capsys.readouterr().err
