"""Master observer: collects the station's messages into one row per tile,
persists them, exports CSV, and sends validated commands only when enabled.
Also drives it from the real coordinator to prove they agree on the contract."""

import json

import pytest

from fusion.coordinator import OK, StationCoordinator
from fusion.store import TileStore
from fusion.transport import InMemoryBus as FusionBus
from master.app import create_app
from master.bus import InMemoryBus
from master.collector import MasterCollector


def feed(c, topic, payload, now=100.0):
    c.handle_message(topic, json.dumps(payload).encode(), now=now)


def test_messages_for_one_tile_merge_into_one_row():
    c = MasterCollector()
    feed(c, "tile/T1/camera/result", {"tile_id": "T1", "grade": "Grade B"})
    feed(c, "tile/T1/acoustic/result", {"acoustic_grade": "Grade A", "status": "done", "peak_frequency_hz": 2810})
    feed(c, "tile/T1/final", {"final_grade": "Grade B", "destination": "bin_2", "acoustic_counted": False,
                              "timed_out": False, "reasons": ["camera: Grade B", "sound: not counted"]})
    feed(c, "tile/T1/ack", {"status": "ok"})
    (row,) = c.recent()
    assert row["camera_grade"] == "Grade B" and row["acoustic_grade"] == "Grade A"
    assert row["final_grade"] == "Grade B" and row["destination"] == "bin_2" and row["ack_status"] == "ok"
    assert c.tally() == {"Grade B": 1}


def test_unreadable_messages_are_counted_not_fatal():
    c = MasterCollector()
    c.handle_message("tile/T1/final", b"{nope", now=1.0)
    c.handle_message("tile/T1/final", b"[1,2]", now=1.0)
    assert c.counters["bad_messages"] == 2 and c.recent() == []


def test_status_topics_and_alarms_are_tracked_with_age():
    c = MasterCollector()
    feed(c, "station/status", {"state": "waiting", "health": {}}, now=100.0)
    feed(c, "machine/alarm", {"ts": 99.0, "level": "alarm", "kind": "no_ack", "message": "x", "tile_id": "T1"}, now=100.0)
    snap = c.snapshot(now=110.0)
    assert snap["station"]["state"] == "waiting" and snap["station_age_s"] == 10.0
    assert snap["camera"] is None and snap["camera_age_s"] is None
    assert snap["alarms"][0]["kind"] == "no_ack"


def test_record_survives_a_restart(tmp_path):
    p = tmp_path / "m" / "master.db"
    c = MasterCollector(p)
    feed(c, "tile/T1/final", {"final_grade": "Reject", "destination": "bin_4"})
    c.close()
    assert MasterCollector(p).tally() == {"Reject": 1}


def test_csv_export_has_a_header_and_a_row_per_tile():
    c = MasterCollector()
    feed(c, "tile/T1/camera/result", {"grade": "Grade A"}, now=1.0)
    feed(c, "tile/T1/final", {"final_grade": "Grade A", "destination": "bin_1", "reasons": ["a", "b"]}, now=2.0)
    lines = c.export_csv().strip().splitlines()
    assert lines[0].startswith("tile_id,first_seen,camera_grade")
    assert len(lines) == 2 and "T1" in lines[1] and "a | b" in lines[1]


# ---- the page API ------------------------------------------------------------


def client(controls):
    c, bus = MasterCollector(), InMemoryBus()
    return c, bus, create_app(c, bus, controls_enabled=controls).test_client()


def test_overview_and_page_render():
    c, bus, cl = client(False)
    assert b"Tile station" in cl.get("/").data
    j = cl.get("/api/overview").get_json()
    assert j["controls"] is False and j["broker_connected"] is True and "tally" in j
    assert cl.get("/api/export.csv").mimetype == "text/csv"


def test_commands_are_refused_unless_enabled_and_need_json():
    c, bus, cl = client(False)
    assert cl.post("/api/command/vote", json={"enabled": True}).status_code == 403
    assert bus.published == []
    c, bus, cl = client(True)
    assert cl.post("/api/command/vote", data="x=1", content_type="text/plain").status_code == 415


def test_commands_publish_the_documented_topics():
    c, bus, cl = client(True)
    assert cl.post("/api/command/vote", json={"enabled": False}).status_code == 200
    assert cl.post("/api/command/hold_all", json={"enabled": True}).status_code == 200
    assert cl.post("/api/command/clear_alarms", json={}).status_code == 200
    assert cl.post("/api/command/belt", json={"percent": 40}).status_code == 200
    assert cl.post("/api/command/strike", json={"enabled": False}).status_code == 200
    sent = [(t, json.loads(p)) for t, p, *_ in bus.published]
    assert ("station/cmd", {"cmd": "vote", "enabled": False}) in sent
    assert ("station/cmd", {"cmd": "hold_all", "enabled": True}) in sent
    assert ("station/cmd", {"cmd": "clear_alarms"}) in sent
    assert ("station/belt", {"percent": 40}) in sent
    assert ("station/strike", {"enabled": False}) in sent


@pytest.mark.parametrize("body", [{"percent": 101}, {"percent": -1}, {"percent": "fast"}, {"percent": True}, {}])
def test_belt_value_is_validated(body):
    c, bus, cl = client(True)
    assert cl.post("/api/command/belt", json=body).status_code == 400
    assert bus.published == []


def test_non_boolean_flags_are_refused():
    c, bus, cl = client(True)
    assert cl.post("/api/command/vote", json={"enabled": "yes"}).status_code == 400


def test_command_when_the_broker_is_down_is_a_503():
    c, bus, cl = client(True)
    bus.link_up = False
    assert cl.post("/api/command/vote", json={"enabled": True}).status_code == 503


def test_master_sees_what_the_real_station_publishes_and_its_commands_work():
    """One bus, the real coordinator on one side, the master on the other."""
    now = [1000.0]
    bus = FusionBus()
    station = StationCoordinator(
        {"final": {"vote_enabled": True, "bins": {"Grade A": "bin_1", "Hold": "manual"}}, "unoq": {"require_ack": False}},
        bus, TileStore(":memory:"), clock=lambda: now[0])
    station.start()
    station.set_health("mic", OK)
    master = MasterCollector(clock=lambda: now[0])
    master.attach(bus)

    bus.publish("tile/T1/camera/result", {"tile_id": "T1", "grade": "Grade A", "seq": 1})
    station.on_tap({"valid": True, "grade": None, "confidence": None})
    now[0] += 3.0
    station.tick()

    (row,) = master.recent()
    assert row["tile_id"] == "T1" and row["camera_grade"] == "Grade A" and row["final_grade"] == "Grade A"
    assert master.snapshot()["station"]["state"] in ("waiting", "fault", "sent")  # heartbeat arrived

    # the master turns vote off through its own command path
    bus.publish("station/cmd", {"cmd": "vote", "enabled": False})
    assert station.vote_enabled is False
