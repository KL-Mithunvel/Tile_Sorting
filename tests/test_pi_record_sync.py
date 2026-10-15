"""The Pi keeps every tile's record + evidence; the master pulls a faithful copy.

Everything runs against the REAL Pi sync blueprint through Flask's test client
(no network): store -> blobs -> /api/sync/* -> PiSyncer -> PiMirror -> master
routes -> integrity checks.
"""

import json
import wave
from io import BytesIO
from urllib.parse import urlsplit

import numpy as np
import pytest
from flask import Flask

from acoustic.sync_api import make_blueprint
from fusion.blobs import BlobStore, EvidenceRecorder
from fusion.store import TileStore
from master.app import create_app
from master.bus import InMemoryBus
from master.collector import MasterCollector
from master.integrity import analyse, check_sync, check_tiles, correlate
from master.mirror import PiMirror
from master.sync import PiSyncer

T0 = 1_700_000_000.0


# ---- the Pi side ----------------------------------------------------------------

def make_pi(tmp_path, token=None, budget=10 ** 9):
    clock = {"t": T0}
    store = TileStore(tmp_path / "pi" / "tiles.db")
    blobs = BlobStore(tmp_path / "pi" / "blobs", store, budget_bytes=budget, clock=lambda: clock["t"])
    app = Flask(__name__)
    app.register_blueprint(make_blueprint(store, blobs, token))
    return store, blobs, app, clock


def populate(store, blobs, n=3, start=1, t=T0):
    for i in range(start, start + n):
        tid = f"T2026-10-15_10-00-00-{i:05d}"
        store.upsert(tid, t + i, seq=i, state="awaiting_tap", camera_grade="Grade A", camera_json={"grade": "Grade A"})
        blobs.save_bytes(tid, "camera", b"\xff\xd8jpegbytes%d" % i, ".jpg")
        blobs.save_wav(tid, "tap", np.sin(np.linspace(0, 100, 4410)) * 0.5, 44100)
        store.upsert(tid, t + i + 1, state="done", acoustic_grade="Grade A", acoustic_json={"valid": True},
                     final_grade="Grade A", delivery="acked")


def http_for(app, fail_on=None):
    client = app.test_client()
    calls = []

    def http(url, headers, timeout):
        p = urlsplit(url)
        path = p.path + ("?" + p.query if p.query else "")
        calls.append(path)
        if fail_on and fail_on(path, len(calls)):
            raise ConnectionError("link dropped")
        r = client.get(path, headers=headers)
        return r.status_code, dict(r.headers), r.data

    http.calls = calls
    return http


def make_master(tmp_path, app, **kw):
    mirror = PiMirror(tmp_path / "m" / "mirror.db", tmp_path / "m" / "blobs")
    http = kw.pop("http", None) or http_for(app)
    return mirror, PiSyncer(mirror, "http://pi:5001", http=http, clock=lambda: T0 + 99, **kw)


# ---- store: history + identity ------------------------------------------------------

def test_every_upsert_and_event_gets_a_cursor_number(tmp_path):
    store = TileStore(tmp_path / "t.db")
    store.upsert("A", T0, state="awaiting_tap", camera_json={"x": 1})
    store.upsert("A", T0 + 1, final_grade="Reject")
    seq = store.add_event("alarm", T0 + 2, "A", {"kind": "bad_message"})
    evs = store.events_after(0)
    assert [e["seq"] for e in evs] == [1, 2, seq]
    assert evs[0]["data"]["camera_json"] == {"x": 1}          # structured, not a string
    assert store.events_after(2)[0]["kind"] == "alarm"
    assert store.head_seq() == seq


def test_store_id_survives_reopen_and_differs_between_databases(tmp_path):
    a = TileStore(tmp_path / "a.db").store_id
    assert TileStore(tmp_path / "a.db").store_id == a
    assert TileStore(tmp_path / "b.db").store_id != a


# ---- blobs ----------------------------------------------------------------------

def test_blob_is_hashed_registered_and_readable(tmp_path):
    store, blobs, _app, _c = make_pi(tmp_path)
    info = blobs.save_bytes("T1", "camera", b"hello", ".jpg")
    assert info["size"] == 5 and len(info["sha256"]) == 64
    assert blobs.path_for("T1", "camera").read_bytes() == b"hello"
    assert blobs.path_for("T1", "nope") is None


def test_wav_is_a_valid_16bit_mono_file(tmp_path):
    store, blobs, _a, _c = make_pi(tmp_path)
    blobs.save_wav("T1", "tap", np.zeros(441), 44100)
    with wave.open(str(blobs.path_for("T1", "tap")), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 44100, 441)


def test_oldest_files_are_pruned_first_and_rows_are_kept(tmp_path):
    store, blobs, _a, clock = make_pi(tmp_path, budget=250)
    for i in range(4):
        clock["t"] = T0 + i
        store.upsert(f"T{i}", clock["t"])
        blobs.save_bytes(f"T{i}", "camera", b"x" * 100, ".jpg")
    live = {b["tile_id"] for b in store.live_blobs_oldest_first()}
    assert live == {"T2", "T3"}                                   # 200 bytes <= 250
    assert blobs.path_for("T0", "camera") is None and store.get_blob("T0", "camera")["pruned"] == 1
    assert store.get("T0") is not None                           # the tile's record stays
    assert any(e["kind"] == "blob_pruned" for e in store.events_after(0))


def test_a_corrupt_registry_row_cannot_read_outside_the_blob_root(tmp_path):
    store, blobs, _a, _c = make_pi(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("no")
    store.add_blob("T1", "camera", "../../secret.txt", "0" * 64, 2, T0)
    assert blobs.path_for("T1", "camera") is None


def test_losing_a_picture_never_raises_into_the_station(tmp_path):
    store, blobs, _a, _c = make_pi(tmp_path)
    ev = EvidenceRecorder(blobs)
    ev.camera_result("T1", {"snapshot_path": str(tmp_path / "does_not_exist.jpg")})   # no exception
    ev.camera_result("T1", {})
    assert store.blobs_for("T1") == []


# ---- sync ---------------------------------------------------------------------

def test_first_sync_copies_tiles_events_and_verified_files(tmp_path):
    store, blobs, app, _c = make_pi(tmp_path)
    populate(store, blobs, n=3)
    mirror, syncer = make_master(tmp_path, app)
    r = syncer.sync_once()
    assert r["ok"], r
    assert r["tiles"] == 3 and r["blobs_fetched"] == 6 and r["behind"] == 0
    assert mirror.counts()["tiles"] == 3
    t = mirror.tile("T2026-10-15_10-00-00-00002")
    assert t["final_grade"] == "Grade A" and {b["name"] for b in t["blobs"]} == {"camera", "tap"}
    assert mirror.blob_path(t["tile_id"], "camera").read_bytes() == b"\xff\xd8jpegbytes2"
    assert syncer.reconcile()["in_sync"] is True


def test_second_sync_moves_nothing_and_new_tiles_arrive_incrementally(tmp_path):
    store, blobs, app, _c = make_pi(tmp_path)
    populate(store, blobs, n=2)
    mirror, syncer = make_master(tmp_path, app)
    syncer.sync_once()
    again = syncer.sync_once()
    assert (again["events"], again["tiles"], again["blobs_fetched"]) == (0, 0, 0)
    populate(store, blobs, n=1, start=3)
    third = syncer.sync_once()
    assert third["tiles"] == 1 and mirror.counts()["tiles"] == 3


def test_a_link_that_drops_mid_sync_loses_nothing(tmp_path):
    store, blobs, app, _c = make_pi(tmp_path)
    populate(store, blobs, n=3)
    # Dies on the call that fetches tile rows: events are in, cursor must NOT have moved.
    mirror, syncer = make_master(tmp_path, app, http=http_for(app, fail_on=lambda p, n: p.startswith("/api/sync/tiles")))
    r = syncer.sync_once()
    assert not r["ok"] and "could not reach" in r["error"]
    assert mirror.cursor() == 0
    mirror2, syncer2 = make_master(tmp_path, app)      # same files, healthy link
    mirror2.close()
    syncer2._mirror = mirror
    ok = syncer2.sync_once()
    assert ok["ok"] and mirror.counts()["tiles"] == 3 and mirror.cursor() == store.head_seq()


def test_a_file_that_fails_its_checksum_is_kept_out_and_reported(tmp_path):
    store, blobs, app, _c = make_pi(tmp_path)
    populate(store, blobs, n=1)
    tid = "T2026-10-15_10-00-00-00001"
    blobs.path_for(tid, "camera").write_bytes(b"tampered on disk")        # bytes no longer match the registry
    mirror, syncer = make_master(tmp_path, app)
    r = syncer.sync_once()
    assert r["blobs_failed"] == 1
    assert mirror.blob_path(tid, "camera") is None
    assert {b["name"]: b["status"] for b in mirror.tile(tid)["blobs"]}["camera"] == "corrupt"
    assert any(f["kind"] == "sync_incomplete" for f in check_sync(r, syncer.reconcile()))


def test_a_file_pruned_on_the_pi_is_reported_gone_not_missing_forever(tmp_path):
    store, blobs, app, clock = make_pi(tmp_path)
    populate(store, blobs, n=1)
    store.mark_blob_pruned("T2026-10-15_10-00-00-00001", "camera", T0)
    mirror, syncer = make_master(tmp_path, app)
    syncer.sync_once()
    st = {b["name"]: b["status"] for b in mirror.tile("T2026-10-15_10-00-00-00001")["blobs"]}
    assert st["camera"] == "pruned" and st["tap"] == "ok"
    assert syncer.reconcile()["in_sync"] is True       # a pruned file is not "missing"


def test_replaced_pi_database_resets_the_cursor_but_keeps_history(tmp_path):
    store, blobs, app, _c = make_pi(tmp_path)
    populate(store, blobs, n=2)
    mirror, syncer = make_master(tmp_path, app)
    syncer.sync_once()
    assert mirror.cursor() > 0
    store2, blobs2, app2, _ = make_pi(tmp_path / "fresh")          # a new database, new store_id
    populate(store2, blobs2, n=1, start=10)
    syncer._http = http_for(app2)
    r = syncer.sync_once()
    assert r["ok"] and r["store_replaced"] is True
    assert mirror.counts()["tiles"] == 3                              # old 2 kept + new 1
    assert any(f["kind"] == "pi_store_replaced" for f in check_sync(r))


def test_token_is_required_when_set(tmp_path):
    store, blobs, app, _c = make_pi(tmp_path, token="s3cret")
    populate(store, blobs, n=1)
    mirror = PiMirror(tmp_path / "m.db", tmp_path / "mb")
    bad = PiSyncer(mirror, "http://pi", token="wrong", http=http_for(app))
    assert "refused" in bad.sync_once()["error"]
    good = PiSyncer(mirror, "http://pi", token="s3cret", http=http_for(app))
    assert good.sync_once()["ok"]


def test_sync_endpoints_reject_nonsense_input(tmp_path):
    store, blobs, app, _c = make_pi(tmp_path)
    c = app.test_client()
    assert c.get("/api/sync/events?after=abc").status_code == 400
    assert c.get("/api/sync/events?after=-1").status_code == 400
    assert c.get("/api/sync/blob/T1/camera").status_code == 404
    assert c.get("/api/sync/info").get_json()["tiles"] == 0


# ---- integrity checks ----------------------------------------------------------

def row(i, **kw):
    d = {"tile_id": f"T2026-10-15_10-00-00-{i:05d}", "created_ts": T0 + i, "updated_ts": T0 + i,
         "state": "done", "camera_grade": "Grade A", "camera": {}, "acoustic_grade": "Grade A",
         "acoustic": {"valid": True}, "acoustic_counted": False, "final_grade": "Grade A",
         "delivery": "acked", "placement": "placed"}
    d.update(kw)
    return d


def kinds(fs):
    return {f["kind"] for f in fs}


def test_a_clean_record_has_no_findings():
    assert check_tiles([row(i) for i in range(1, 12)], T0 + 100) == []


def test_unacknowledged_final_grade_is_flagged_only_after_it_has_waited():
    t = row(1, delivery="sent", updated_ts=T0)
    assert check_tiles([t], T0 + 10) == []
    assert "stuck_delivery" in kinds(check_tiles([t], T0 + 300))


def test_final_grade_better_than_camera_is_a_rule_violation():
    f = check_tiles([row(1, camera_grade="Reject", final_grade="Grade A")], T0 + 1)
    assert f[0]["kind"] == "grade_rule_violation" and f[0]["severity"] == "alarm"
    # an upgrade by sound that does not count is not a violation of the SOUND rule, only camera matters:
    assert check_tiles([row(1, camera_grade="Grade A", final_grade="Reject")], T0 + 1) == []


def test_missing_tile_numbers_are_found_per_run():
    tiles = [row(1), row(2), row(5)]
    f = [x for x in check_tiles(tiles, T0 + 1) if x["kind"] == "sequence_gap"][0]
    assert f["evidence"]["missing_total"] == 2


def test_most_taps_unusable_is_flagged_with_the_reasons():
    tiles = [row(i, acoustic={"valid": False, "reason_invalid": "no strike found"}) for i in range(1, 13)]
    f = [x for x in check_tiles(tiles, T0 + 1) if x["kind"] == "unusable_taps"][0]
    assert f["evidence"]["bad"] == 12 and "no strike found" in f["evidence"]["reasons"]


def test_failed_gantry_placement_and_held_tiles_are_reported():
    ks = kinds(check_tiles([row(1, placement="failed", placement_reason="vacuum miss"),
                            row(2, delivery="needs_person", final_grade="Hold")], T0 + 1))
    assert {"placement_problem", "needs_person"} <= ks


def test_an_outage_followed_by_held_tiles_is_correlated_but_a_quiet_one_is_not():
    tiles = [row(i) for i in range(1, 8)] + [row(i, final_grade="Hold", delivery="needs_person") for i in range(8, 15)]
    tiles = [{**t, "created_ts": T0 + i * 20} for i, t in enumerate(tiles)]
    ev = [{"seq": 1, "ts": T0 + 7 * 20, "kind": "health", "tile_id": None,
           "data": {"name": "camera", "level": "fail", "detail": "no heartbeat"}}]
    f = correlate(tiles, ev, T0 + 1000)
    assert [x["kind"] for x in f] == ["correlated_outage"] and f[0]["evidence"]["rate_after"] > 0.9
    assert correlate([row(i) for i in range(1, 15)], ev, T0 + 1000) == []


def test_repeating_alarm_is_reported_from_history():
    ev = [{"seq": i, "ts": T0 + i, "kind": "alarm", "tile_id": None, "data": {"kind": "bad_message"}}
          for i in range(1, 7)]
    assert "alarm_repeat" in kinds(correlate([], ev, T0 + 60))


def test_sync_failure_is_a_finding_and_never_leaks_the_address_into_the_summary():
    f = check_sync({"ok": False, "error": "could not reach the Pi at http://192.168.1.11:5001"})
    assert f[0]["kind"] == "sync_failed" and "192.168" not in f[0]["summary"]


def test_findings_are_ordered_alarm_first():
    out = analyse([row(1, camera_grade="Reject", final_grade="Grade A"), row(2, delivery="needs_person")],
                  [], T0 + 5)
    assert out["findings"][0]["severity"] == "alarm"


# ---- the master's pages ----------------------------------------------------------

def master_client(tmp_path, llm=None, populate_n=3):
    store, blobs, app, _c = make_pi(tmp_path)
    populate(store, blobs, n=populate_n)
    mirror, syncer = make_master(tmp_path, app)
    syncer.sync_once()
    c = create_app(MasterCollector(), InMemoryBus(), clock=lambda: T0 + 60, llm=llm,
                   mirror=mirror, syncer=syncer).test_client()
    return c, store, mirror


def test_tiles_list_detail_and_evidence_file_are_served(tmp_path):
    c, _s, _m = master_client(tmp_path)
    lst = c.get("/api/pi/tiles").get_json()
    assert lst["total"] == 3 and lst["tiles"][0]["tile_id"].endswith("00003")        # newest first
    tid = lst["tiles"][0]["tile_id"]
    d = c.get(f"/api/pi/tile/{tid}").get_json()
    assert d["tile"]["final_grade"] == "Grade A" and [e["kind"] for e in d["events"]][0] == "tile"
    img = c.get(f"/api/pi/blob/{tid}/camera")
    assert img.status_code == 200 and img.data.startswith(b"\xff\xd8")
    assert c.get("/api/pi/blob/nope/camera").status_code == 404
    assert c.get("/api/pi/tile/nope").status_code == 404
    assert c.get("/tiles").status_code == 200


def test_sync_can_be_triggered_from_the_page_and_needs_json(tmp_path):
    c, store, _m = master_client(tmp_path)
    assert c.post("/api/pi/sync").status_code == 415
    populate(store, BlobStore(tmp_path / "pi" / "blobs", store), n=1, start=9)
    r = c.post("/api/pi/sync", json={}).get_json()
    assert r["ok"] and r["tiles"] == 1
    assert c.get("/api/pi/sync?reconcile=1").get_json()["reconcile"]["in_sync"] is True


def test_integrity_endpoint_reports_a_planted_error(tmp_path):
    c, store, _m = master_client(tmp_path)
    store.upsert("T2026-10-15_10-00-00-00001", T0 + 5, camera_grade="Reject", final_grade="Grade A")
    c.post("/api/pi/sync", json={})
    out = c.get("/api/pi/integrity?window=3600").get_json()
    assert any(f["kind"] == "grade_rule_violation" for f in out["findings"])


def test_without_a_mirror_the_routes_say_so_instead_of_failing():
    c = create_app(MasterCollector(), InMemoryBus(), clock=lambda: T0).test_client()
    assert c.get("/api/pi/tiles").status_code == 409
    assert c.get("/api/pi/sync").status_code == 409
    assert c.get("/api/insights").get_json()["record_checks"] == []


def test_the_language_model_gets_numbers_but_never_tile_ids(tmp_path):
    from master.llm import summarize_for_model

    tiles = [row(1, camera_grade="Reject", final_grade="Grade A")]
    findings = analyse(tiles, [], T0 + 1)["findings"]
    sent = json.dumps(summarize_for_model({"anomalies": []}, [], findings))
    assert "grade_rule_violation" not in sent and "Final grade is better" in sent
    assert "T2026-10-15" not in sent
