"""master.session_pull against the REAL Pi Flask app (no network): verified copy,
resume, healing, refusing a file that arrives wrong, skipping a live recording."""

import hashlib
import io
import json
import time

import pytest

from acoustic.dashboard import create_app
from master.session_pull import pull_sessions
from tests.test_session_bag import station


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Client:
    """An opener that talks to the Flask test client."""

    def __init__(self, flask_client, tamper=None, truncate=None):
        self.c, self.tamper, self.truncate = flask_client, tamper, truncate
        self.requests = []

    def __call__(self, url, headers):
        path = url.split("://", 1)[1].split("/", 1)[1]
        self.requests.append((path, dict(headers)))
        r = self.c.get("/" + path, headers=headers)
        data = r.data
        if self.tamper and self.tamper in path and r.status_code in (200, 206):
            data = b"X" + data[1:]
        if self.truncate and self.truncate in path and r.status_code == 200:
            data = data[: len(data) // 2]
        return r.status_code, dict(r.headers), io.BytesIO(data)


@pytest.fixture
def pi(tmp_path):
    svc, bus, ctl = station(tmp_path / "pi")
    c = create_app(svc, controls_enabled=True).test_client()
    c.post("/api/session/start", json={"run_type": "tile", "labels": {"defect": "cracked"}})
    time.sleep(0.2)
    bus.publish("station/tap", {"seq": 1, "tile_present": True, "tof_mm": 50, "nano_ms": 1, "source": "manual", "ts": time.time()})
    sid = ctl.recorder.id
    c.post("/api/session/stop", json={})
    yield c, sid, tmp_path / "pi" / "data" / "sessions" / sid
    svc.shutdown()


def test_pull_copies_every_file_verified_and_writes_the_manifest_last(pi, tmp_path):
    c, sid, src = pi
    dest = tmp_path / "pc"
    report = pull_sessions("http://pi:5001", dest, opener=Client(c), log=lambda s: None)
    assert report[sid]["status"] == "complete" and report[sid]["problems"] == []
    manifest = json.loads((src / "manifest.json").read_text())
    for f in manifest["files"]:
        assert sha(dest / sid / f["path"]) == f["sha256"]
    assert (dest / sid / "manifest.json").is_file()


def test_pulling_again_downloads_nothing(pi, tmp_path):
    c, sid, _ = pi
    dest = tmp_path / "pc"
    pull_sessions("http://pi:5001", dest, opener=Client(c), log=lambda s: None)
    again = Client(c)
    pull_sessions("http://pi:5001", dest, opener=again, log=lambda s: None)
    assert not [p for p, _ in again.requests if "/file/" in p]


def test_a_damaged_local_copy_is_healed(pi, tmp_path):
    c, sid, _ = pi
    dest = tmp_path / "pc"
    pull_sessions("http://pi:5001", dest, opener=Client(c), log=lambda s: None)
    (dest / sid / "events.jsonl").write_text("rubbish")
    report = pull_sessions("http://pi:5001", dest, opener=Client(c), log=lambda s: None)
    assert report[sid]["status"] == "complete"
    assert "rubbish" not in (dest / sid / "events.jsonl").read_text()


def test_a_file_that_arrives_wrong_is_discarded_and_the_session_has_no_manifest(pi, tmp_path):
    c, sid, _ = pi
    dest = tmp_path / "pc"
    report = pull_sessions("http://pi:5001", dest, opener=Client(c, tamper="events.jsonl"), log=lambda s: None)
    assert report[sid]["status"] == "incomplete"
    assert any("arrived wrong" in p for p in report[sid]["problems"])
    assert not (dest / sid / "events.jsonl").exists() and not (dest / sid / "manifest.json").exists()


def test_a_cut_off_download_resumes_from_where_it_stopped(pi, tmp_path):
    c, sid, src = pi
    dest = tmp_path / "pc"
    first = pull_sessions("http://pi:5001", dest, opener=Client(c, truncate="events.jsonl"), log=lambda s: None)
    assert first[sid]["status"] == "incomplete"                       # arrived short: discarded, not kept
    # Now a genuine partial: leave half of the file as .part and let the next run finish it.
    data = (src / "events.jsonl").read_bytes()
    part = dest / sid / "events.jsonl.part"
    part.parent.mkdir(parents=True, exist_ok=True)
    part.write_bytes(data[: len(data) // 2])
    opener = Client(c)
    report = pull_sessions("http://pi:5001", dest, opener=opener, log=lambda s: None)
    assert report[sid]["status"] == "complete"
    ranged = [h for p, h in opener.requests if p.endswith("/file/events.jsonl")]
    assert ranged and ranged[0].get("Range") == f"bytes={len(data) // 2}-"
    assert (dest / sid / "events.jsonl").read_bytes() == data


def test_a_session_still_recording_is_skipped(tmp_path):
    svc, bus, ctl = station(tmp_path / "pi")
    try:
        c = create_app(svc, controls_enabled=True).test_client()
        c.post("/api/session/start", json={})
        (tmp_path / "pi" / "data" / "sessions" / ctl.recorder.id).exists()
        report = pull_sessions("http://pi:5001", tmp_path / "pc", opener=Client(c), log=lambda s: None)
        assert list(report.values())[0]["status"].startswith("skipped")
        assert not (tmp_path / "pc").exists() or not list((tmp_path / "pc").iterdir())
        c.post("/api/session/stop", json={})
    finally:
        svc.shutdown()


def test_a_manifest_naming_a_path_outside_the_session_is_refused(pi, tmp_path):
    c, sid, _ = pi

    def evil(url, headers):
        status, h, body = Client(c)(url, headers)
        if url.endswith("/manifest"):
            m = json.loads(body.read())
            m["files"].append({"path": "../../escape.txt", "size": 1, "sha256": "0" * 64})
            return status, h, io.BytesIO(json.dumps(m).encode())
        return status, h, body

    report = pull_sessions("http://pi:5001", tmp_path / "pc", opener=evil, log=lambda s: None)
    assert any("unsafe" in p for p in report[sid]["problems"]) and not (tmp_path / "escape.txt").exists()
