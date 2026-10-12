"""Master troubleshooting: the health-change history, the diagnosis rules and
the per-station timeline."""

import json

from master.collector import MasterCollector
from master.troubleshoot import diagnose, since_ts, timeline

NOW = 1000.0


def st(sid, level, detail=""):
    return {"id": sid, "label": sid, "level": level, "detail": detail}


def healthy():
    return [st("pi", "ok", "waiting"), st("camera", "ok", "3 tiles"), st("acoustic", "ok", "listening"),
            st("conveyor", "ok", "belt 60 %"), st("gantry", "ok", "last ack 1 s ago")]


def station_msg(**levels):
    health = {"camera": {"level": "ok", "detail": "1 tiles"}, "mic": {"level": "ok", "detail": "listening"},
              "nano": {"level": "ok", "detail": "belt 60 %"}, "unoq": {"level": "ok", "detail": "ok"}}
    for k, v in levels.items():
        health[k] = {"level": v[0], "detail": v[1]}
    return json.dumps({"state": "waiting", "state_detail": "waiting", "health": health,
                       "awaiting_count": 0, "settings": {}}).encode()


# ---- history ----------------------------------------------------------------

def test_only_level_changes_are_recorded_not_detail_churn():
    c = MasterCollector()
    c.handle_message("station/status", station_msg(), now=10.0)
    c.tick(now=10.5)
    c.handle_message("station/status", station_msg(camera=("ok", "2 tiles")), now=11.0)
    c.tick(now=11.5)                                    # detail text changed, level did not
    first = c.health_events()
    assert {e["station"] for e in first} == {"pi", "camera", "acoustic", "conveyor", "gantry"}
    n = len(first)
    c.handle_message("station/status", station_msg(camera=("fail", "silent for 9 s")), now=12.0)
    c.tick(now=12.5)
    new = c.health_events()[n:]
    assert [(e["station"], e["level"], e["detail"]) for e in new] == [("camera", "fail", "silent for 9 s")]


def test_a_silent_station_is_logged_by_tick_even_though_no_message_arrives():
    c = MasterCollector()
    c.handle_message("station/status", station_msg(), now=10.0)
    c.tick(now=10.5)
    c.tick(now=60.0)                                    # 50 s of nothing from the Pi
    pi = [e for e in c.health_events() if e["station"] == "pi"]
    assert [e["level"] for e in pi] == ["ok", "offline"] and pi[-1]["ts"] == 60.0


def test_master_broker_link_is_its_own_station():
    c = MasterCollector()
    c.tick(now=5.0, broker_connected=False)
    c.tick(now=9.0, broker_connected=True)
    m = [(e["level"]) for e in c.health_events() if e["station"] == "master"]
    assert m == ["fail", "ok"]


def test_since_includes_the_level_in_force_when_the_window_opened():
    c = MasterCollector()
    c.handle_message("station/status", station_msg(), now=10.0)
    c.tick(now=10.0)
    c.handle_message("station/status", station_msg(camera=("fail", "x")), now=500.0)
    c.tick(now=500.0)
    ev = c.health_events(since_ts=400.0)
    cam = [(e["ts"], e["level"]) for e in ev if e["station"] == "camera"]
    assert cam == [(10.0, "ok"), (500.0, "fail")]       # the earlier ok is carried in as the opening level


# ---- diagnosis --------------------------------------------------------------

def test_healthy_line_has_no_findings():
    assert diagnose(healthy(), [], [], NOW) == []


def test_each_failing_station_gets_a_matching_title_and_checks():
    s = healthy()
    s[1] = st("camera", "fail", "silent for 14 s")
    s[3] = st("conveyor", "fail", "no serial bridge heartbeat")
    f = diagnose(s, [], [], NOW)
    assert [x["station"] for x in f] == ["camera", "conveyor"]
    assert "stopped sending heartbeats" in f[0]["title"] and f[0]["checks"]
    assert "never reported" in f[1]["title"]


def test_silent_pi_collapses_the_rest_into_one_finding():
    s = [st(i, "offline", "no status from the Pi station") for i in ("pi", "camera", "acoustic", "conveyor", "gantry")]
    f = diagnose(s, [], [], NOW)
    assert len(f) == 1 and f[0]["station"] == "pi" and f[0]["level"] == "offline"


def test_master_link_down_reports_only_that():
    s = [st(i, "offline") for i in ("pi", "camera")]
    f = diagnose(s, [], [{"ts": NOW, "kind": "x", "level": "alarm"}], NOW, master_connected=False)
    assert [x["station"] for x in f] == ["master"]


def test_unknown_detail_still_produces_a_finding():
    f = diagnose([st("camera", "warn", "something new")], [], [], NOW)
    assert len(f) == 1 and f[0]["detail"] == "something new" and f[0]["checks"]


def test_recent_alarms_are_grouped_by_kind_and_old_ones_ignored():
    alarms = [{"ts": NOW - 5, "kind": "missed_camera", "message": "m1", "level": "alarm"},
              {"ts": NOW - 20, "kind": "missed_camera", "message": "m2", "level": "alarm"},
              {"ts": NOW - 5000, "kind": "orphan_tap", "message": "old", "level": "alarm"},
              {"ts": NOW - 5, "kind": "event", "message": "info", "level": "info"}]
    f = diagnose(healthy(), [], alarms, NOW)
    assert len(f) == 1 and f[0]["kind"] == "missed_camera" and "(2 in 10 min)" in f[0]["title"]
    assert f[0]["since"] == NOW - 20 and f[0]["detail"] == "m1"


def test_findings_are_ordered_most_severe_first():
    s = healthy()
    s[4] = st("gantry", "warn", "waiting for ack of T1")
    s[1] = st("camera", "fail", "silent for 9 s")
    assert [x["level"] for x in diagnose(s, [], [], NOW)] == ["fail", "warn"]


def test_since_is_none_when_history_never_saw_that_level():
    ev = [{"ts": 5.0, "station": "camera", "level": "ok"}, {"ts": 9.0, "station": "camera", "level": "fail"}]
    assert since_ts(ev, "camera", "fail") == 9.0
    assert since_ts(ev, "camera", "warn") is None and since_ts(ev, "mic", "ok") is None


# ---- timeline ---------------------------------------------------------------

def test_timeline_segments_and_uptime():
    ev = [{"ts": 0.0, "station": "camera", "level": "ok"},       # before the window: opening level
          {"ts": 700.0, "station": "camera", "level": "fail"},
          {"ts": 900.0, "station": "camera", "level": "ok"}]
    t = timeline(ev, {"camera": "ok"}, now=1000.0, window_s=500.0)["stations"]["camera"]
    assert [(s["from"], s["to"], s["level"]) for s in t["segments"]] == [
        (500.0, 700.0, "ok"), (700.0, 900.0, "fail"), (900.0, 1000.0, "ok")]
    assert t["uptime"] == 0.6 and t["changes"] == 2


def test_time_before_first_record_is_unknown_not_counted_as_up():
    ev = [{"ts": 900.0, "station": "pi", "level": "ok"}]
    t = timeline(ev, {"pi": "ok"}, now=1000.0, window_s=500.0)["stations"]["pi"]
    assert t["segments"][0]["level"] == "unknown" and t["segments"][-1]["level"] == "ok"
    assert t["known_s"] == 100.0 and t["uptime"] == 1.0
    assert timeline([], {"pi": "ok"}, now=1000.0, window_s=500.0)["stations"]["pi"]["uptime"] is None


# ---- endpoint ---------------------------------------------------------------

def test_troubleshoot_endpoint_reports_a_silent_pi_then_a_camera_fault():
    from master.app import create_app
    from master.bus import InMemoryBus

    clock = [100.0]
    bus, col = InMemoryBus(), MasterCollector(clock=lambda: clock[0])
    col.attach(bus)
    app = create_app(col, bus, clock=lambda: clock[0])
    c = app.test_client()
    d = c.get("/api/troubleshoot?window=900").get_json()
    assert [f["station"] for f in d["findings"]] == ["pi"]            # nothing heard yet: one finding, not five

    bus.publish("station/status", json.loads(station_msg(camera=("fail", "silent for 9 s"))))
    d = c.get("/api/troubleshoot?window=900").get_json()
    assert [f["station"] for f in d["findings"]] == ["camera"]
    assert d["findings"][0]["checks"] and d["timeline"]["stations"]["camera"]["segments"]
    assert d["labels"]["camera"] == "Camera station"

    bus.link_up = False
    d = c.get("/api/troubleshoot").get_json()
    assert [f["station"] for f in d["findings"]] == ["master"]
    assert c.get("/api/troubleshoot?window=5").status_code == 400
    assert c.get("/troubleshoot").status_code == 200


def test_pi_fault_is_not_repeated_when_a_station_explains_it():
    s = healthy()
    s[0] = st("pi", "fail", "camera: silent for 9 s")
    s[1] = st("camera", "fail", "silent for 9 s")
    assert [f["station"] for f in diagnose(s, [], [], NOW)] == ["camera"]
    # but a Pi fault with no failing station beneath it is still reported
    s[1] = st("camera", "ok", "3 tiles")
    assert [f["station"] for f in diagnose(s, [], [], NOW)] == ["pi"]
