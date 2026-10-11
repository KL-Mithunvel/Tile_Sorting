"""StationService + the GUI's HTTP API, with a synthetic microphone and an
in-memory bus: no audio hardware, no broker."""

import time

import numpy as np
import pytest

from acoustic.dashboard import create_app
from acoustic.demo import SyntheticCapture, synthetic_strike
from acoustic.station import StationService
from fusion.store import TileStore
from fusion.transport import InMemoryBus

CONFIG = {
    "audio": {"sample_rate": 44100, "device": None},
    "trigger": {"mode": "hardware", "rms_threshold": 0.02, "pre_trigger_ms": 100},
    "hardware_trigger": {"pre_trigger_ms": 100},
    "station": {
        "ledger": {"max_wait_s": 10},
        "final": {"vote_enabled": True, "min_confidence": 0.6, "bins": {"Grade A": "bin_1", "Reject": "bin_4", "Hold": "manual"}},
        "unoq": {"require_ack": False},
        "reference": {"peak_hz_min": 2500, "peak_hz_max": 3100, "rule_confidence": 0.8},
    },
}


def make(config=None, factory=SyntheticCapture):
    import copy

    cfg = copy.deepcopy(config or CONFIG)
    bus, store = InMemoryBus(), TileStore(":memory:")
    svc = StationService(cfg, bus, store, capture_factory=factory)
    svc.coordinator.start()
    svc._open_capture()
    bus.publish("station/camera/status", {"source_kind": "camera", "source_status": "running"}, qos=0)
    return svc, bus


def camera(bus, tid, grade="Grade A"):
    bus.publish(f"tile/{tid}/camera/result", {"tile_id": tid, "grade": grade, "seq": 1, "snapshot_path": None})


def test_good_strike_binds_to_waiting_tile_and_sound_vote_counts():
    svc, bus = make()
    camera(bus, "T1", "Grade A")
    assert svc.handle_clip(synthetic_strike(freq=1500.0)) == "T1"  # outside the reference band
    final = bus.messages("tile/+/final")[0]
    assert final["final_grade"] == "Reject" and final["acoustic_counted"] is True
    assert svc.last_tap()["tile_id"] == "T1"


def test_in_band_strike_leaves_a_clean_tile_clean():
    svc, bus = make()
    camera(bus, "T1", "Grade A")
    svc.handle_clip(synthetic_strike(freq=2800.0))
    assert bus.messages("tile/+/final")[0]["final_grade"] == "Grade A"


def test_unusable_recording_with_hardware_trigger_still_uses_the_tap_slot():
    svc, bus = make()
    camera(bus, "T1", "Grade B")
    assert svc.handle_clip(np.zeros(22050, dtype=np.float32)) == "T1"
    f = bus.messages("tile/+/final")[0]
    assert f["final_grade"] == "Grade B" and f["acoustic_counted"] is False


def test_in_rms_mode_a_noise_burst_does_not_steal_a_tile_slot():
    cfg = {**CONFIG, "trigger": {"mode": "rms", "rms_threshold": 0.02, "pre_trigger_ms": 100}}
    svc, bus = make(cfg)
    camera(bus, "T1", "Grade A")
    rng = np.random.default_rng(0)
    assert svc.handle_clip(rng.normal(0, 0.05, 22050).astype(np.float32)) is None
    assert svc.coordinator.snapshot()["awaiting_count"] == 1  # T1 still waiting for its real tap
    assert svc.coordinator.snapshot()["counters"]["ignored_taps"] == 1
    assert svc.handle_clip(synthetic_strike()) == "T1"


def test_simulate_tap_refused_in_rms_mode():
    cfg = {**CONFIG, "trigger": {"mode": "rms", "rms_threshold": 0.02, "pre_trigger_ms": 100}}
    svc, _ = make(cfg)
    with pytest.raises(RuntimeError):
        svc.simulate_tap()


def test_missing_microphone_is_reported_not_fatal():
    def boom(cfg):
        raise OSError("no such device")

    svc, bus = make(factory=boom)
    snap = svc.coordinator.snapshot()
    assert snap["state"] == "fault" and snap["health"]["mic"]["level"] == "fail"
    assert "no such device" in snap["health"]["mic"]["detail"]


def test_threads_run_the_full_loop_and_shut_down():
    svc, bus = make()
    svc.start()
    try:
        camera(bus, "T1", "Grade A")
        svc.simulate_tap()
        deadline = time.time() + 5
        while time.time() < deadline and not bus.messages("tile/+/final"):
            time.sleep(0.05)
        assert bus.messages("tile/+/final")
    finally:
        svc.shutdown()


# ---- the HTTP API ---------------------------------------------------------


@pytest.fixture
def client_factory():
    def build(controls):
        svc, bus = make()
        return svc, bus, create_app(svc, controls_enabled=controls).test_client()

    return build


def test_pages_and_status_render(client_factory):
    svc, bus, c = client_factory(False)
    assert b"Tile station" in c.get("/").data
    assert b"service" in c.get("/service").data.lower()
    j = c.get("/api/station").get_json()
    assert j["controls"] is False and "health" in j and "settings" in j


def test_controls_refused_when_disabled_but_alarm_ack_allowed(client_factory):
    svc, bus, c = client_factory(False)
    r = c.post("/api/control/vote", json={"enabled": True})
    assert r.status_code == 403
    assert c.post("/api/control/hold_all", json={"enabled": True}).status_code == 403
    assert c.post("/api/audio/device", json={"device": 1}).status_code == 403
    assert c.post("/api/control/clear_alarms", json={}).status_code == 200


def test_controls_require_json_body(client_factory):
    svc, bus, c = client_factory(True)
    r = c.post("/api/control/vote", data="enabled=1", content_type="application/x-www-form-urlencoded")
    assert r.status_code == 415


def test_vote_and_hold_all_toggle_when_enabled(client_factory):
    svc, bus, c = client_factory(True)
    assert c.post("/api/control/vote", json={"enabled": False}).get_json()["vote_enabled"] is False
    assert svc.coordinator.vote_enabled is False
    c.post("/api/control/hold_all", json={"enabled": True})
    assert c.get("/api/station").get_json()["settings"]["hold_all"] is True


def test_simulate_tap_endpoint(client_factory):
    svc, bus, c = client_factory(True)
    assert c.post("/api/control/simulate_tap", json={}).status_code == 200


def test_tile_image_only_served_from_under_data(client_factory, tmp_path, monkeypatch):
    svc, bus, _ = client_factory(False)
    root = tmp_path
    (root / "data" / "camera_captures").mkdir(parents=True)
    good = root / "data" / "camera_captures" / "t.jpg"
    good.write_bytes(b"\xff\xd8\xff")
    outside = root / "secret.jpg"
    outside.write_bytes(b"nope")
    bus.publish("tile/OK/camera/result", {"tile_id": "OK", "grade": "Grade A", "snapshot_path": str(good)})
    bus.publish("tile/BAD/camera/result", {"tile_id": "BAD", "grade": "Grade A", "snapshot_path": str(outside)})
    bus.publish("tile/ETC/camera/result", {"tile_id": "ETC", "grade": "Grade A", "snapshot_path": "/etc/passwd"})
    c = create_app(svc, controls_enabled=False, repo_root=root).test_client()
    assert c.get("/tile_image/OK").status_code == 200
    assert c.get("/tile_image/BAD").status_code == 404
    assert c.get("/tile_image/ETC").status_code == 404
    assert c.get("/tile_image/UNKNOWN").status_code == 404
