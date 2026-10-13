"""The master's plant model (what the 3D page draws) and the synthetic demo line
that lets it be run with no hardware."""

import json
import time

from master.app import create_app
from master.bus import InMemoryBus
from master.collector import MasterCollector
from master.demo import DemoLine
from master.plant import (LINE_TRANSIT_S, SORTED_VISIBLE_S, STAGE_U_CAP, build_plant, tile_position, tile_stage)


def status(state="waiting", **health):
    base = {"camera": {"level": "ok", "detail": "3 tiles"}, "mic": {"level": "ok", "detail": "mic"},
            "nano": {"level": "ok", "detail": "belt 60 %"}, "unoq": {"level": "ok", "detail": "acked"}}
    base.update(health)
    return {"state": state, "state_detail": state, "health": base, "awaiting_count": 0,
            "settings": {"hold_all": False, "vote_enabled": False}}


def snap(station=None, age=1.0, recent=(), nano=None):
    return {"station": station, "station_age_s": age if station else None, "nano": nano,
            "recent": list(recent), "tally": {}, "alarms": []}


def test_stage_follows_which_results_exist():
    assert tile_stage({"camera_grade": "Grade A"}) == "camera"
    assert tile_stage({"camera_grade": "Grade A", "acoustic_grade": "Grade A"}) == "acoustic"
    assert tile_stage({"camera_grade": "Grade A", "acoustic_status": "invalid"}) == "acoustic"
    assert tile_stage({"camera_grade": "Grade A", "final_grade": "Grade A"}) == "sorted"


def test_a_tile_is_held_back_until_the_next_station_has_reported():
    # old enough to be past every station on the free-running line...
    age = LINE_TRANSIT_S * 2
    assert tile_position("camera", age) == STAGE_U_CAP["camera"] < tile_position("acoustic", age)
    assert tile_position("acoustic", age) == STAGE_U_CAP["acoustic"] < tile_position("sorted", age) == 1.0
    assert tile_position("camera", 0) == 0.0
    assert tile_position("camera", -5) == 0.0   # a clock skew must not put a tile before the belt


def test_silent_station_shows_everything_offline_not_stale_green():
    p = build_plant(snap(station=status(), age=30.0), now=100.0)
    assert p["line"]["state"] == "silent"
    assert {s["level"] for s in p["stations"]} == {"offline"}
    p = build_plant(snap(station=None), now=100.0)
    assert p["line"]["state"] == "silent" and {s["level"] for s in p["stations"]} == {"offline"}


def test_station_levels_come_from_the_pis_health_and_a_missing_key_is_offline():
    st = status(camera={"level": "fail", "detail": "silent for 9 s"})
    del st["health"]["unoq"]
    p = build_plant(snap(station=st), now=100.0)
    by = {s["id"]: s for s in p["stations"]}
    assert by["camera"]["level"] == "fail" and "silent" in by["camera"]["detail"]
    assert by["acoustic"]["level"] == "ok"
    assert by["gantry"]["level"] == "offline"
    assert p["line"]["state"] == "waiting"


def test_sorted_tiles_leave_the_scene_and_tiles_report_destination():
    rows = [
        {"tile_id": "A", "first_ts": 99.0, "camera_grade": "Grade A"},
        {"tile_id": "B", "first_ts": 100.0 - SORTED_VISIBLE_S - 1, "camera_grade": "Grade A", "final_grade": "Grade A"},
        {"tile_id": "C", "first_ts": 90.0, "camera_grade": "Reject", "final_grade": "Reject", "destination": "bin_reject"},
        {"tile_id": "D", "first_ts": None},
    ]
    p = build_plant(snap(station=status(), recent=rows), now=100.0)
    ids = [t["tile_id"] for t in p["tiles"]]
    assert ids == ["A", "C"]
    c = p["tiles"][1]
    assert c["stage"] == "sorted" and c["destination"] == "bin_reject" and c["grade"] == "Reject"


def test_demo_line_drives_the_collector_and_obeys_commands():
    bus, col = InMemoryBus(), MasterCollector()
    col.attach(bus)
    line = DemoLine(bus, interval_s=100.0, transit_s=0.0, fault_every_s=1e9, seed=1)
    t0 = 1000.0
    with line._lock:
        line._step(t0)          # a tile arrives
        line._step(t0 + 0.1)    # ...and is finished (transit 0), no second one yet
    assert col.station_status["state"] in ("waiting", "awaiting_tap")
    (row,) = col.recent()[:1]
    assert row["final_grade"] in ("Grade A", "Grade B", "Reject") and row["ack_status"] == "ok"

    # commands the master sends change what the "line" does
    bus.publish("station/belt", {"percent": 0})
    bus.publish("station/cmd", {"cmd": "hold_all", "enabled": True})
    assert line.belt_pct == 0 and line.hold_all is True
    bus.publish("station/belt", {"percent": 250})        # out of range: ignored
    assert line.belt_pct == 0
    with line._lock:
        line._step(t0 + 500)    # an interval has passed but the belt is stopped: no new tile
    assert len(col.recent(50)) == 1


def test_demo_camera_fault_raises_an_alarm_and_a_failing_station():
    bus, col = InMemoryBus(), MasterCollector()
    col.attach(bus)
    line = DemoLine(bus, interval_s=1e9, fault_every_s=0.0, fault_for_s=5.0, seed=1)
    with line._lock:
        line._step(time.time())
        line._beat(time.time(), camera_faulty=True)
    assert [a["kind"] for a in col.alarms] == ["camera_silent"]
    p = build_plant(col.snapshot(), time.time())
    assert {s["id"]: s["level"] for s in p["stations"]}["camera"] == "fail"
    assert p["line"]["state"] == "fault"


def test_plant_endpoint_and_pages_render():
    bus, col = InMemoryBus(), MasterCollector()
    col.attach(bus)
    app = create_app(col, bus, controls_enabled=True, demo=True, clock=lambda: 100.0)
    c = app.test_client()
    j = c.get("/api/plant").get_json()
    assert j["demo"] is True and j["controls"] is True and j["line"]["state"] == "silent"
    assert c.get("/").status_code == 200 and c.get("/classic").status_code == 200
    assert c.get("/static/vendor/three/three.module.min.js").status_code == 200
    assert c.get("/static/vendor/three/three.core.min.js").status_code == 200
    json.dumps(j)   # plain data only


def test_gantry_station_uses_its_own_status_when_it_is_fresh():
    from master.plant import stations_from_snapshot

    base = {"station": None, "station_age_s": None}  # the Pi is silent; the gantry is not
    snap = dict(base, gantry={"state": "online", "level": "ok", "completed": 7, "queue_depth": 2}, gantry_age_s=1.0)
    g = next(s for s in stations_from_snapshot(snap) if s["id"] == "gantry")
    assert g["level"] == "ok" and "7 placed" in g["detail"]
    snap["gantry"] = {"state": "online", "level": "fail", "alarm": "LOST_STEPS_X"}
    g = next(s for s in stations_from_snapshot(snap) if s["id"] == "gantry")
    assert g["level"] == "fail" and "LOST_STEPS_X" in g["detail"]
    snap["gantry"] = {"state": "online", "level": "warn", "paused": True}
    assert next(s for s in stations_from_snapshot(snap) if s["id"] == "gantry")["detail"] == "paused"
    snap["gantry"] = {"state": "offline"}
    assert next(s for s in stations_from_snapshot(snap) if s["id"] == "gantry")["level"] == "offline"
    snap["gantry"], snap["gantry_age_s"] = {"state": "online", "level": "ok"}, 60.0  # stale: not trusted
    assert next(s for s in stations_from_snapshot(snap) if s["id"] == "gantry")["level"] == "offline"


# ---- whole-line start / stop --------------------------------------------------

def _client(controls=True):
    bus, col = InMemoryBus(), MasterCollector()
    col.attach(bus)
    return create_app(col, bus, controls_enabled=controls).test_client(), bus


def test_line_command_is_validated_guarded_and_sent_as_one_station_cmd():
    c, bus = _client()
    ok = c.post("/api/command/line", json={"state": "stop"})
    assert ok.status_code == 200
    assert bus.messages("station/cmd")[-1] == {"cmd": "line", "state": "stop"}
    c.post("/api/command/line", json={"state": "start", "belt_percent": 40})
    assert bus.messages("station/cmd")[-1] == {"cmd": "line", "state": "start", "belt_percent": 40}
    n = len(bus.messages("station/cmd"))
    for bad in ({}, {"state": "pause"}, {"state": "start", "belt_percent": 0},
                {"state": "start", "belt_percent": 101}, {"state": "start", "belt_percent": True},
                {"state": "stop", "belt_percent": 50}):
        assert c.post("/api/command/line", json=bad).status_code == 400
    assert len(bus.messages("station/cmd")) == n                       # nothing sent for any of them
    assert c.post("/api/command/line", data="x").status_code == 415
    off, _ = _client(controls=False)
    assert off.post("/api/command/line", json={"state": "stop"}).status_code == 403


def test_demo_line_follows_the_same_stop_start_rules_as_the_station():
    bus, col = InMemoryBus(), MasterCollector()
    col.attach(bus)
    line = DemoLine(bus, interval_s=100.0, fault_every_s=1e9, seed=1)
    line.hold_all, line.belt_pct = True, 45.0
    bus.publish("station/cmd", {"cmd": "line", "state": "stop"})
    assert line.line_stopped and line.belt_pct == 0 and line.hold_all and not line.strike_enabled
    bus.publish("station/cmd", {"cmd": "line", "state": "start"})
    assert not line.line_stopped and line.belt_pct == 45.0 and line.strike_enabled
    assert line.hold_all is True                      # it was on before the stop, so it is on after
    line.belt_pct = 0.0
    bus.publish("station/cmd", {"cmd": "line", "state": "stop"})   # nothing known to restore ...
    line._saved["belt"] = None
    bus.publish("station/cmd", {"cmd": "line", "state": "start"})
    assert line.line_stopped                          # ... so it refuses rather than guessing a speed


def test_plant_and_troubleshoot_show_a_stopped_line_as_deliberate_not_a_fault():
    from master.troubleshoot import diagnose
    st = status(state="line_stopped")
    st["state_detail"] = "line stopped (soft stop from the master - not an e-stop)"
    st["settings"] = {"hold_all": True, "vote_enabled": False, "line": "stopped"}
    p = build_plant(snap(station=st), now=100.0)
    assert p["line"]["stopped"] is True and p["line"]["state"] == "line_stopped"
    pi = {s["id"]: s for s in p["stations"]}["pi"]
    assert pi["level"] == "warn"
    (f,) = diagnose(p["stations"], [], [], 100.0)
    assert "stopped from the master" in f["title"] and "not a fault" in f["meaning"]


# ---- gantry feed gate -----------------------------------------------------------

def test_feed_command_is_validated_guarded_and_one_station_cmd():
    c, bus = _client()
    assert c.post("/api/command/feed", json={"state": "pause"}).status_code == 200
    assert bus.messages("station/cmd")[-1] == {"cmd": "feed", "state": "pause"}
    c.post("/api/command/feed", json={"state": "resume"})
    assert bus.messages("station/cmd")[-1] == {"cmd": "feed", "state": "resume"}
    n = len(bus.messages("station/cmd"))
    for bad in ({}, {"state": "stop"}, {"state": True}):
        assert c.post("/api/command/feed", json=bad).status_code == 400
    assert len(bus.messages("station/cmd")) == n and c.post("/api/command/feed", data="x").status_code == 415
    off, _ = _client(controls=False)
    assert off.post("/api/command/feed", json={"state": "pause"}).status_code == 403


def test_demo_line_holds_finals_while_the_feed_is_paused_and_delivers_them_on_resume():
    bus, col = InMemoryBus(), MasterCollector()
    col.attach(bus)
    line = DemoLine(bus, interval_s=0.0, transit_s=0.0, fault_every_s=1e9, seed=3)
    bus.publish("station/cmd", {"cmd": "feed", "state": "pause"})
    with line._lock:
        line._step(1000.0)
        line._step(1000.1)
    assert bus.messages("tile/+/final") == []                 # decided, but nothing handed to the gantry
    assert len(line._held) >= 1 and line.tally                 # ...while the line's own count moved on
    held = len(line._held)
    bus.publish("station/cmd", {"cmd": "feed", "state": "resume"})
    assert len(bus.messages("tile/+/final")) == held and line._held == []
    assert len(bus.messages("tile/+/ack")) == held


def test_demo_line_stop_pauses_the_feed_and_start_resumes_it():
    bus, col = InMemoryBus(), MasterCollector()
    col.attach(bus)
    line = DemoLine(bus, fault_every_s=1e9)
    line.belt_pct = 50.0
    bus.publish("station/cmd", {"cmd": "line", "state": "stop"})
    assert line.feed_paused is True
    bus.publish("station/cmd", {"cmd": "line", "state": "start"})
    assert line.feed_paused is False


def test_plant_shows_a_paused_feed_on_the_line_and_on_the_gantry_even_with_its_own_status():
    st = status()
    st["settings"] = {"hold_all": False, "vote_enabled": False, "line": "running", "feed": "paused"}
    st["outbox_depth"] = 3
    sn = snap(station=st)
    sn["gantry"] = {"state": "online", "level": "ok", "completed": 12, "queue_depth": 0, "paused": False,
                    "homed": True}
    sn["gantry_age_s"] = 1.0
    p = build_plant(sn, now=100.0)
    assert p["line"]["feed_paused"] is True and p["line"]["held_back"] == 3
    g = {s["id"]: s for s in p["stations"]}["gantry"]
    assert g["level"] == "warn" and "feed paused, 3 held back" in g["detail"]
    sn["gantry"]["level"] = "fail"
    sn["gantry"]["alarm"] = "LOST_STEPS"
    g = {s["id"]: s for s in build_plant(sn, now=100.0)["stations"]}["gantry"]
    assert g["level"] == "fail"                                # a real fault is never softened to a warning


def test_troubleshoot_explains_a_paused_feed_and_a_full_queue_differently():
    from master.troubleshoot import diagnose
    paused = [{"id": "gantry", "label": "g", "level": "warn", "detail": "feed paused by the master, 2 held back"}]
    (f,) = diagnose(paused, [], [], 100.0)
    assert "feed is paused" in f["title"] and "dropped" not in f["title"] and f["checks"]
    full = [{"id": "gantry", "label": "g", "level": "fail",
             "detail": "feed paused and the queue is full (5): tiles are being dropped"}]
    (f,) = diagnose(full, [], [], 100.0)
    assert "tiles are being dropped" in f["title"] and f["level"] == "fail"


# ---- the master PC and the operator in the 3D scene ---------------------------

def test_plant_endpoint_lists_the_master_pc_and_the_operator_with_their_own_status():
    bus, col = InMemoryBus(), MasterCollector()
    col.attach(bus)
    on = create_app(col, bus, controls_enabled=True, demo=True, demo_label="demo", clock=lambda: 100.0).test_client()
    st = {s["id"]: s for s in on.get("/api/plant").get_json()["stations"]}
    assert {"pi", "camera", "acoustic", "conveyor", "gantry", "master", "user"} <= set(st)
    assert st["master"]["level"] == "ok" and "connected to the broker" in st["master"]["detail"]
    assert "controls ON" in st["master"]["detail"] and st["user"]["level"] == "ok"
    off = create_app(col, bus, controls_enabled=False, clock=lambda: 100.0).test_client()
    st = {s["id"]: s for s in off.get("/api/plant").get_json()["stations"]}
    assert st["user"]["level"] == "offline" and "view only" in st["user"]["detail"]
    assert "read-only" in st["master"]["detail"]
    bus.link_up = False
    st = {s["id"]: s for s in off.get("/api/plant").get_json()["stations"]}
    assert st["master"]["level"] == "fail" and "NOT connected" in st["master"]["detail"]


def test_the_master_and_operator_are_not_part_of_the_lines_health_picture():
    """They are drawn, but Troubleshoot must never report them as line stations."""
    from master.plant import stations_from_snapshot
    ids = {s["id"] for s in stations_from_snapshot(snap(station=status()))}
    assert "master" not in ids and "user" not in ids


# ---- Hold tiles: a person's job (tile/<id>/needs_person) ------------------------

def test_collector_takes_a_needs_person_message_as_the_tiles_final_and_flags_it():
    c = MasterCollector()
    c.handle_message("tile/H1/camera/result", json.dumps({"grade": "Grade A"}).encode(), now=10.0)
    c.handle_message("tile/H1/needs_person", json.dumps({
        "tile_id": "H1", "final_grade": "Hold", "destination": "manual", "needs_person": True,
        "reasons": ["hold-all is on"], "ts": 11.0}).encode(), now=11.0)
    (row,) = c.recent()
    assert row["final_grade"] == "Hold" and row["needs_person"] is True and row["destination"] == "manual"
    assert c.tally() == {"Hold": 1}
    assert c.analysis_rows()[0]["final_ts"] == 11.0


def test_plant_keeps_hold_tiles_at_the_pickup_only_while_the_pi_says_they_need_a_person():
    st = status()
    st["needs_person"] = 1
    rows = [{"tile_id": "H2", "first_ts": 98.0, "camera_grade": "Grade A", "final_grade": "Hold", "needs_person": True},
            {"tile_id": "H1", "first_ts": 90.0, "camera_grade": "Grade A", "final_grade": "Hold", "needs_person": True},
            {"tile_id": "A1", "first_ts": 80.0, "camera_grade": "Grade A", "final_grade": "Grade A", "destination": "bin_1"}]
    p = build_plant(snap(station=st, recent=rows), now=100.0)
    by = {t["tile_id"]: t for t in p["tiles"]}
    assert by["H2"]["stage"] == "needs_person" and "H1" not in by       # the newest one only: the Pi counts 1
    assert p["line"]["needs_person"] == 1
    st["needs_person"] = 0                                               # a person confirmed (feed resumed)
    p = build_plant(snap(station=st, recent=rows), now=100.0)
    assert not [t for t in p["tiles"] if t["stage"] == "needs_person"]


def test_demo_line_keeps_hold_off_the_gantry_pauses_the_feed_and_resume_confirms():
    bus, col = InMemoryBus(), MasterCollector()
    col.attach(bus)
    line = DemoLine(bus, interval_s=0.0, transit_s=0.0, fault_every_s=1e9, seed=3)
    bus.publish("station/cmd", {"cmd": "hold_all", "enabled": True})
    with line._lock:
        line._step(1000.0)
        line._step(1000.1)
    assert bus.messages("tile/+/final") == []                          # never offered to the gantry
    assert bus.messages("tile/+/needs_person") and line.feed_paused is True and line._needs_person
    bus.publish("station/cmd", {"cmd": "feed", "state": "resume"})
    assert line._needs_person == [] and line.feed_paused is False


def test_troubleshoot_explains_a_feed_paused_for_a_person():
    from master.troubleshoot import diagnose
    s = [{"id": "gantry", "label": "g", "level": "warn",
          "detail": "2 hold tile(s) need a person - take them off the belt, then Resume feed (0 held back)"}]
    (f,) = diagnose(s, [], [], 100.0)
    assert "waiting for a person" in f["title"] and any("Resume feed" in c for c in f["checks"])
