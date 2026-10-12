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
