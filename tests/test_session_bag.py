"""The data-collection "bag" (fusion/session.py, acoustic/session_control.py,
camera/session_follower.py): continuous audio + video + bus events + labelled striker
events, in one folder, sealed with hashes.

No hardware: a synthetic microphone, an in-memory bus, fake clocks. What this does NOT
show: that a real microphone's start-up delay or the camera's frame times line up with
the wall clock to better than tens of ms, or that a Pi's disk keeps up with 1080p + audio.
"""

import hashlib
import json
import time
import wave
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import pytest

from acoustic.dashboard import create_app
from acoustic.demo import SyntheticCapture, synthetic_strike
from acoustic.session_control import SessionController
from acoustic.station import StationService
from camera.recorder import Recorder
from camera.session_follower import SessionFollower
from fusion.session import (
    AudioChunkWriter, SessionRecorder, extract_clips, list_sessions, mark_interrupted, read_audio_index,
    seal_manifest, session_file, verify_session,
)
from fusion.store import TileStore
from fusion.transport import InMemoryBus

SR = 8000


class Clock:
    def __init__(self, t=1_760_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def recorder(tmp_path, clock=None, **kw):
    kw.setdefault("free_bytes", lambda p: 50 * 1024 ** 3)
    kw.setdefault("git", lambda p: "abc1234")
    return SessionRecorder(tmp_path / "sessions", repo_root=tmp_path, chunk_s=1.0, clock=clock or Clock(), **kw)


def feed(rec, clock, seconds, burst_at=None, sr=SR):
    """Push `seconds` of near-silence in 100 ms blocks (a 1 kHz burst starting `burst_at` s in),
    advancing the fake clock as the real microphone would."""
    n = int(0.1 * sr)
    for i in range(int(seconds * 10)):
        t = i * 0.1
        block = np.zeros(n, dtype=np.float32)
        if burst_at is not None and burst_at <= t < burst_at + 0.1:
            block = 0.8 * np.sin(2 * np.pi * 1000 * np.arange(n) / sr).astype(np.float32)
        clock.t += 0.1
        rec.add_audio(block, wall_end=clock.t)
    # the writer thread drains the queue asynchronously
    deadline = time.time() + 5
    while not rec._audio_q.empty() and time.time() < deadline:
        time.sleep(0.01)


def peak_time(path):
    with wave.open(str(path), "rb") as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32)
        return int(np.argmax(np.abs(x))) / w.getframerate()


# ---- the folder ---------------------------------------------------------------------------


def test_audio_is_continuous_across_chunks_and_has_wall_clock_starts(tmp_path):
    w = AudioChunkWriter(tmp_path, SR, chunk_s=1.0)
    (tmp_path / "chunks.jsonl").unlink(missing_ok=True)
    w.write(np.zeros(int(2.5 * SR)), wall_start=100.0)
    w.close()
    assert [c["samples"] for c in w.chunks] == [SR, SR, SR // 2]
    assert w.total_samples == int(2.5 * SR)
    assert [c["start_wall"] for c in w.chunks] == pytest.approx([100.0, 101.0, 102.0])


def test_a_session_records_labelled_strikes_and_cuts_clips_around_them(tmp_path):
    clock = Clock()
    rec = recorder(tmp_path, clock)
    rec.start(run_type="tile", note="batch 1", labels={"defect": "cracked", "tile_ref": "17"}, sample_rate=SR)
    feed(rec, clock, 1.0)
    t_strike = clock.t + 0.5                      # the burst below begins 0.5 s from now
    feed(rec, clock, 1.0, burst_at=0.5)
    ev = rec.note_striker("strike", "manual", nano_ms=5000, seq=1, t=t_strike)
    feed(rec, clock, 1.0)
    manifest = rec.stop()

    assert manifest["status"] == "complete" and manifest["code_commit"] == "abc1234"
    d = next((tmp_path / "sessions").iterdir())
    rows = list(json.loads((d / "strikes.json").read_text()))
    assert rows[0]["labels"]["defect"] == "cracked" and rows[0]["labels"]["tile_ref"] == "17"
    clips = [f for f in (d / "clips").glob("S0001_strike.wav")]
    assert len(clips) == 1
    # The burst sits where the event was stamped: 0.3 s into a clip that starts 0.3 s before it.
    assert peak_time(clips[0]) == pytest.approx(0.3, abs=0.12)
    csv_text = (d / "clips" / "clips.csv").read_text()
    assert "cracked" in csv_text and "manual" in csv_text
    assert ev["id"] == "S0001"


def test_retract_and_strike_of_one_cycle_are_linked_and_timed(tmp_path):
    clock = Clock()
    rec = recorder(tmp_path, clock)
    rec.start(sample_rate=SR)
    feed(rec, clock, 0.5)
    r = rec.note_striker("retract", "manual", nano_ms=1000, seq=1)
    s = rec.note_striker("strike", "manual", nano_ms=2400, seq=1)
    assert s["cycle"] == r["id"] and rec.events()[0]["cycle"] == s["id"]
    assert s["retract_to_strike_ms"] == 1400
    rec.stop()


def test_manifest_hashes_every_file_and_catches_a_changed_one(tmp_path):
    clock = Clock()
    rec = recorder(tmp_path, clock)
    rec.start(sample_rate=SR)
    feed(rec, clock, 1.2)
    rec.log_event("station/tap", {"seq": 1})
    manifest = rec.stop()
    d = next((tmp_path / "sessions").iterdir())
    names = {f["path"] for f in manifest["files"]}
    assert "events.jsonl" in names and "strikes.json" in names and any(n.startswith("audio/audio_") for n in names)
    for f in manifest["files"]:
        assert hashlib.sha256((d / f["path"]).read_bytes()).hexdigest() == f["sha256"]
    assert verify_session(d) == []
    (d / "events.jsonl").write_text("tampered\n")
    assert verify_session(d) == ["changed events.jsonl"]


def test_events_are_one_json_line_each_with_a_timestamp(tmp_path):
    clock = Clock()
    rec = recorder(tmp_path, clock)
    rec.start(sample_rate=SR)
    rec.log_event("tile/T1/camera/result", b'{"grade": "Grade A"}')
    rec.log_event("station/status", "not json")
    rec.stop()
    d = next((tmp_path / "sessions").iterdir())
    lines = [json.loads(x) for x in (d / "events.jsonl").read_text().splitlines()]
    assert lines[0]["topic"] == "session/start"
    assert {"t", "topic", "payload"} <= set(lines[1]) and lines[1]["payload"] == {"grade": "Grade A"}
    assert lines[2]["payload"] == "not json" and lines[-1]["topic"] == "session/stop"


def test_a_session_is_refused_on_a_nearly_full_disk(tmp_path):
    rec = recorder(tmp_path, free_bytes=lambda p: 10 * 1024 ** 2)
    with pytest.raises(RuntimeError, match="free"):
        rec.start(sample_rate=SR)
    assert not rec.active


def test_labels_are_checked_and_can_be_corrected_afterwards(tmp_path):
    clock = Clock()
    rec = recorder(tmp_path, clock)
    rec.start(sample_rate=SR)
    with pytest.raises(ValueError):
        rec.set_labels(defect="broken-ish")
    with pytest.raises(ValueError):
        rec.set_labels(colour="red")
    ev = rec.note_striker("strike", "manual", 1, 1)
    assert ev["labels"]["defect"] is None
    rec.relabel(ev["id"], defect="chipped", tier="3B")
    assert rec.events()[0]["labels"]["defect"] == "chipped"
    assert rec.set_labels()["defect"] is None                   # the running label did not change
    rec.stop()


def test_ambient_mark_gets_a_clip(tmp_path):
    clock = Clock()
    rec = recorder(tmp_path, clock)
    rec.start(sample_rate=SR)
    feed(rec, clock, 1.0)
    rec.mark("ambient")
    feed(rec, clock, 1.5)
    rec.stop()
    d = next((tmp_path / "sessions").iterdir())
    assert (d / "clips" / "A0001_ambient.wav").is_file()


def test_clips_can_be_recut_with_a_different_window(tmp_path):
    clock = Clock()
    rec = recorder(tmp_path, clock)
    rec.start(sample_rate=SR)
    feed(rec, clock, 1.0)
    rec.note_striker("strike", "manual", 1, 1)
    feed(rec, clock, 2.0)
    rec.stop()
    d = next((tmp_path / "sessions").iterdir())
    extract_clips(d, pre_s=0.1, post_s=0.5)
    with wave.open(str(d / "clips" / "S0001_strike.wav"), "rb") as w:
        assert w.getnframes() / w.getframerate() == pytest.approx(0.6, abs=0.01)


def test_a_session_that_never_stopped_is_sealed_as_interrupted(tmp_path):
    clock = Clock()
    rec = recorder(tmp_path, clock)
    rec.start(sample_rate=SR)
    feed(rec, clock, 2.5)
    rec.note_striker("strike", "manual", 1, 1)
    # power cut: no stop(). A new process finds the folder.
    fixed = mark_interrupted(tmp_path / "sessions")
    d = next((tmp_path / "sessions").iterdir())
    assert fixed == [d.name]
    m = json.loads((d / "manifest.json").read_text())
    assert m["status"] == "interrupted" and m["audio"]["chunks_found"] >= 1
    assert verify_session(d) == []
    assert list_sessions(tmp_path / "sessions")[0]["status"] == "interrupted"


def test_session_file_refuses_to_leave_the_session(tmp_path):
    root = tmp_path / "sessions"
    (root / "a").mkdir(parents=True)
    (root / "a" / "x.txt").write_text("x")
    (root / "secret.txt").write_text("s")
    assert session_file(root, "a", "x.txt") is not None
    assert session_file(root, "a", "../secret.txt") is None
    assert session_file(root, "..", "sessions/secret.txt") is None


# ---- the station: bus -> session -----------------------------------------------------------------

CONFIG = {
    "audio": {"sample_rate": 44100, "device": None},
    "trigger": {"mode": "hardware", "rms_threshold": 0.02, "pre_trigger_ms": 100},
    "hardware_trigger": {"pre_trigger_ms": 100},
    "station": {
        "ledger": {"max_wait_s": 10},
        "final": {"vote_enabled": False, "min_confidence": 0.6, "bins": {"Grade A": "bin_1", "Reject": "bin_4", "Hold": "manual"}},
        "unoq": {"require_ack": False},
    },
}


def station(tmp_path):
    import copy

    cfg = copy.deepcopy(CONFIG)
    bus, store = InMemoryBus(), TileStore(":memory:")
    rec = SessionRecorder(tmp_path / "data" / "sessions", repo_root=tmp_path, chunk_s=1.0,
                          free_bytes=lambda p: 50 * 1024 ** 3, git=lambda p: None)
    ctl = SessionController(bus, rec, data_root=tmp_path / "data", sample_rate=44100,
                            config_snapshot=lambda: {"acoustic": cfg}, camera_wait_s=3.0)
    svc = StationService(cfg, bus, store, capture_factory=SyntheticCapture, session=ctl)
    svc.coordinator.start()
    svc.session.attach()
    svc._open_capture()
    return svc, bus, ctl


def test_manual_strike_is_recorded_and_labelled_but_never_uses_a_tiles_tap_slot(tmp_path):
    svc, bus, ctl = station(tmp_path)
    try:
        bus.publish("tile/T1/camera/result", {"tile_id": "T1", "grade": "Grade A", "seq": 1, "snapshot_path": None})
        ctl.start(run_type="striker_only", labels={"condition": "no_tile"}, mic_ok=True)
        time.sleep(0.2)
        bus.publish("station/retract", {"seq": 1, "source": "manual", "nano_ms": 100, "ts": time.time()})
        bus.publish("station/tap", {"seq": 1, "tile_present": False, "tof_mm": 400, "nano_ms": 1600, "source": "manual", "ts": time.time()})
        assert svc.handle_clip(synthetic_strike(freq=2800.0)) is None          # not bound to T1
        assert svc.coordinator.snapshot()["awaiting_count"] == 1                # T1 still waiting for ITS tap
        assert svc.coordinator.snapshot()["counters"].get("manual_strikes") == 1
        assert not bus.messages("tile/+/final")
        events = ctl.recorder.events()
        assert [e["kind"] for e in events] == ["retract", "strike"]
        assert events[1]["labels"]["condition"] == "no_tile" and events[1]["cycle"] == events[0]["id"]
        manifest = ctl.stop()
        d = next((tmp_path / "data" / "sessions").iterdir())
        assert manifest["status"] == "complete" and manifest["audio"]["samples"] > 0     # the synthetic mic's blocks
        assert (d / "clips" / "S0001_strike.wav").is_file() and (d / "clips" / "R0001_retract.wav").is_file()
        topics = {json.loads(x)["topic"] for x in (d / "events.jsonl").read_text().splitlines()}
        assert {"station/tap", "station/retract", "session/start", "session/stop"} <= topics
        assert (d / "manifest.json").is_file() and verify_session(d) == []
    finally:
        svc.shutdown()


def test_tof_strike_is_paired_with_its_tile_and_the_picture_is_kept(tmp_path):
    svc, bus, ctl = station(tmp_path)
    try:
        pic = tmp_path / "data" / "camera_captures" / "tile_1.jpg"
        pic.parent.mkdir(parents=True)
        cv2.imwrite(str(pic), np.full((20, 20, 3), 90, np.uint8))
        ctl.start(mic_ok=True)
        bus.publish("tile/T1/camera/result", {"tile_id": "T1", "grade": "Grade A", "seq": 1, "snapshot_path": str(pic)})
        bus.publish("station/tap", {"seq": 1, "tile_present": True, "tof_mm": 50, "nano_ms": 1, "source": "tof", "ts": time.time()})
        assert svc.handle_clip(synthetic_strike()) == "T1"
        assert ctl.recorder.events()[0]["tile_id"] == "T1"
        ctl.stop()
        d = next((tmp_path / "data" / "sessions").iterdir())
        assert (d / "tiles" / "T1.jpg").is_file()
    finally:
        svc.shutdown()


def test_picture_outside_data_is_not_copied(tmp_path):
    svc, bus, ctl = station(tmp_path)
    try:
        outside = tmp_path / "elsewhere.jpg"
        cv2.imwrite(str(outside), np.zeros((8, 8, 3), np.uint8))
        ctl.start(mic_ok=True)
        bus.publish("tile/T9/camera/result", {"tile_id": "T9", "grade": "Grade A", "seq": 1, "snapshot_path": str(outside)})
        ctl.stop()
        d = next((tmp_path / "data" / "sessions").iterdir())
        assert not list((d / "tiles").iterdir())
    finally:
        svc.shutdown()


def test_no_session_without_a_microphone_unless_asked(tmp_path):
    svc, bus, ctl = station(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="microphone"):
            ctl.start(mic_ok=False)
        ctl.start(mic_ok=False, allow_no_audio=True)
        assert ctl.stop()["audio_available"] is False
    finally:
        svc.shutdown()


def test_start_and_stop_are_announced_for_the_camera(tmp_path):
    svc, bus, ctl = station(tmp_path)
    try:
        ctl.start(mic_ok=True)
        on = bus.messages("station/session/status")[-1]
        assert on["active"] is True and on["dir"].startswith("data/sessions/") and on["id"]
        ctl.stop()
        assert bus.messages("station/session/status")[-1]["active"] is False
        assert bus.retained["station/session/status"]
    finally:
        svc.shutdown()


def test_stop_waits_for_the_cameras_video_to_finish(tmp_path):
    svc, bus, ctl = station(tmp_path)
    try:
        ctl.start(mic_ok=True)
        sid = ctl.recorder.id
        d = tmp_path / "data" / "sessions" / sid
        (d / "video").mkdir(exist_ok=True)
        (d / "video" / "meta.json").write_text(json.dumps({"status": "recording"}))
        bus.publish("station/session/camera", {"state": "recording", "id": sid})
        import threading

        threading.Timer(0.2, lambda: (d / "video" / "meta.json").write_text(
            json.dumps({"status": "complete", "frames": 9}))).start()
        manifest = ctl.stop()
        assert manifest["video"]["status"] == "complete" and manifest["video"]["frames"] == 9
    finally:
        svc.shutdown()


# ---- the GUI's API and the sync/export -----------------------------------------------------------------


def test_api_session_controls_need_controls_and_json(tmp_path):
    svc, bus, ctl = station(tmp_path)
    try:
        ro = create_app(svc, controls_enabled=False).test_client()
        assert ro.get("/api/session").get_json()["controls"] is False
        assert ro.post("/api/session/start", json={}).status_code == 403
        assert ro.post("/api/striker/action", json={"action": "release"}).status_code == 403
        rw = create_app(svc, controls_enabled=True).test_client()
        assert rw.post("/api/session/start", data="x").status_code == 415
        assert rw.post("/api/striker/action", json={"action": "explode"}).status_code == 409
    finally:
        svc.shutdown()


def test_api_session_round_trip_and_striker_commands_reach_the_bus(tmp_path):
    svc, bus, ctl = station(tmp_path)
    try:
        c = create_app(svc, controls_enabled=True).test_client()
        r = c.post("/api/session/start", json={"run_type": "tile", "note": "first", "labels": {"defect": "fine"}})
        assert r.status_code == 200 and r.get_json()["session"]["active"] is True
        assert c.post("/api/session/start", json={}).status_code == 409                   # one at a time
        assert c.post("/api/session/labels", json={"defect": "chipped", "tile_ref": "4"}).get_json()["labels"]["defect"] == "chipped"
        assert c.post("/api/session/labels", json={"defect": "nope"}).status_code == 409
        c.post("/api/striker/mode", json={"manual": True})
        c.post("/api/striker/arm", json={"enabled": True})
        c.post("/api/striker/action", json={"action": "retract"})
        c.post("/api/striker/action", json={"action": "release"})
        cmds = bus.messages("station/striker/cmd")
        assert cmds == [{"action": "mode", "manual": True}, {"action": "retract"}, {"action": "release"}]
        assert bus.messages("station/strike")[-1] == {"enabled": True}
        j = c.get("/api/session").get_json()
        assert j["session"]["active"] and j["session"]["labels"]["tile_ref"] == "4"
        assert c.post("/api/session/mark", json={"kind": "ambient"}).status_code == 200
        stopped = c.post("/api/session/stop", json={}).get_json()
        assert stopped["manifest"]["status"] == "complete"
        assert c.post("/api/session/stop", json={}).status_code == 409
    finally:
        svc.shutdown()


def test_sync_api_lists_serves_and_hashes_sessions_but_not_one_still_recording(tmp_path):
    svc, bus, ctl = station(tmp_path)
    try:
        c = create_app(svc, controls_enabled=True).test_client()
        c.post("/api/session/start", json={})
        sid = ctl.recorder.id
        time.sleep(0.1)
        assert c.get(f"/api/sync/session/{sid}/file/events.jsonl").status_code == 409     # still recording
        c.post("/api/session/stop", json={})
        listed = c.get("/api/sync/sessions").get_json()["sessions"]
        assert listed[0]["id"] == sid and listed[0]["status"] == "complete"
        manifest = c.get(f"/api/sync/session/{sid}/manifest").get_json()
        f = next(x for x in manifest["files"] if x["path"] == "events.jsonl")
        r = c.get(f"/api/sync/session/{sid}/file/events.jsonl")
        assert r.status_code == 200 and r.headers["X-Blob-Sha256"] == f["sha256"]
        assert hashlib.sha256(r.data).hexdigest() == f["sha256"]
        assert c.get(f"/api/sync/session/{sid}/file/../../x").status_code == 404
        assert c.get("/api/sync/session/nope/manifest").status_code == 404
    finally:
        svc.shutdown()


# ---- the camera's half ------------------------------------------------------------------------------------


def frame(i):
    return np.full((48, 64, 3), (i * 20) % 256, np.uint8)


def test_recorder_into_a_fixed_folder_logs_the_wall_time_of_every_frame(tmp_path):
    t = [1000.0]
    wall = [1_760_000_000.0]
    rec = Recorder(tmp_path / "ignored", "camera 0", folder=tmp_path / "session" / "video", frame_log=True,
                   now_fn=lambda: datetime(2026, 10, 19, 12, 0, 0), clock=lambda: t[0], wall=lambda: wall[0])
    rec.start()
    for i in range(14):
        rec.write(frame(i))
        t[0] += 0.1
        wall[0] += 0.1
    meta = rec.stop()
    folder = tmp_path / "session" / "video"
    assert not (tmp_path / "ignored").exists()
    assert (folder / "video.mp4").is_file() or (folder / "video.avi").is_file()
    rows = (folder / "frames.csv").read_text().splitlines()
    assert rows[0] == "frame,t_wall,t_mono" and len(rows) == 15
    assert float(rows[-1].split(",")[1]) - float(rows[1].split(",")[1]) == pytest.approx(1.3)
    assert meta["frame_log"] == "frames.csv" and meta["frames"] == 14


class _Controller:
    def __init__(self):
        self.started, self.stopped = [], 0
        self.fail = None

    def start_recording(self, folder=None, frame_log=False):
        if self.fail:
            raise ValueError(self.fail)
        folder.mkdir(parents=True, exist_ok=True)
        self.started.append((folder, frame_log))
        return {"folder": folder.name}

    def stop_recording(self):
        self.stopped += 1
        return {"frames": 12, "fps": 30.0}


class _Pub:
    def __init__(self):
        self.subs, self.sent = {}, []

    def subscribe(self, topic, cb):
        self.subs[topic] = cb

    def publish_json(self, topic, payload, qos=1, retain=False):
        self.sent.append((topic, payload, retain))
        return True


def follower(tmp_path):
    ctl, pub = _Controller(), _Pub()
    f = SessionFollower(ctl, pub, tmp_path, config={"segmentation": {"x": 1}})
    f.attach()
    return f, ctl, pub


def announce(pub, **msg):
    pub.subs["station/session/status"]("station/session/status", json.dumps(msg).encode())


def test_camera_records_into_the_announced_session_and_answers(tmp_path):
    f, ctl, pub = follower(tmp_path)
    announce(pub, active=True, id="S1", dir="data/sessions/S1")
    assert ctl.started == [((tmp_path / "data" / "sessions" / "S1" / "video").resolve(), True)]
    assert (tmp_path / "data" / "sessions" / "S1" / "camera_config.yaml").is_file()
    assert pub.sent[-1][1]["state"] == "recording"
    announce(pub, active=True, id="S1", dir="data/sessions/S1")             # retained message again
    assert len(ctl.started) == 1
    announce(pub, active=False, id="S1")
    assert ctl.stopped == 1 and pub.sent[-1][1]["state"] == "stopped" and pub.sent[-1][1]["frames"] == 12


def test_camera_refuses_a_folder_outside_data_and_reports_an_unusable_source(tmp_path):
    f, ctl, pub = follower(tmp_path)
    announce(pub, active=True, id="X", dir="../../etc")
    assert not ctl.started and pub.sent[-1][1]["state"] == "error"
    ctl.fail = "recording needs a live camera as the source"
    announce(pub, active=True, id="Y", dir="data/sessions/Y")
    assert pub.sent[-1][1]["state"] == "error" and "live camera" in pub.sent[-1][1]["detail"]
    assert f.session_id is None


def test_a_camera_that_restarts_mid_session_starts_a_second_video_part(tmp_path):
    f, ctl, pub = follower(tmp_path)
    video = tmp_path / "data" / "sessions" / "S2" / "video"
    video.mkdir(parents=True)
    (video / "meta.json").write_text("{}")
    announce(pub, active=True, id="S2", dir="data/sessions/S2")
    assert ctl.started[0][0].name == "video_2"


def test_the_two_session_topics_agree_across_the_nodes():
    from acoustic.session_control import TOPIC_SESSION_CAMERA
    from camera.session_follower import TOPIC_SESSION_CAMERA as CAM, TOPIC_SESSION_STATUS as CAM_STATUS
    from fusion.transport import TOPIC_SESSION_STATUS

    assert TOPIC_SESSION_CAMERA == CAM and TOPIC_SESSION_STATUS == CAM_STATUS


def test_api_striker_hold_power_needs_controls_and_a_number(tmp_path):
    svc, bus, ctl = station(tmp_path)
    try:
        ro = create_app(svc, controls_enabled=False).test_client()
        assert ro.post("/api/striker/hold", json={"percent": 40}).status_code == 403
        rw = create_app(svc, controls_enabled=True).test_client()
        assert rw.post("/api/striker/hold", json={"percent": 40}).status_code == 200
        assert bus.messages("station/striker/cmd")[-1] == {"action": "hold", "percent": 40.0}
        assert rw.post("/api/striker/hold", json={"percent": 140}).status_code == 409
        assert rw.post("/api/striker/hold", json={"percent": "hot"}).status_code == 409
        assert rw.post("/api/striker/hold", json={}).status_code == 409
    finally:
        svc.shutdown()
