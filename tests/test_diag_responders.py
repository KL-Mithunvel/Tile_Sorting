"""Every node answers the diagnostic ping with its own status, and a ping can
never create work (no tile, no pick job, no state change)."""

import json

from acoustic.tap_bridge import TapBridge
from camera.publisher import TilePublisher
from fusion.coordinator import FAIL, OK, StationCoordinator
from fusion.store import TileStore
from fusion.transport import InMemoryBus
from master.collector import MasterCollector
from pick_place.tile_feed import FeedClient


def ping(bus, pid="p1"):
    bus.publish("station/diag/ping", {"id": pid}, qos=0)


def pong(bus, node):
    msgs = [json.loads(p) for t, p, *_ in bus.published if t == f"station/diag/pong/{node}"]
    return msgs[-1] if msgs else None


def test_station_answers_with_its_state_and_health():
    bus = InMemoryBus()
    c = StationCoordinator({"unoq": {"require_ack": False}}, bus, TileStore(":memory:"), clock=lambda: 10.0)
    c.start()
    c.set_health("mic", OK, "listening")
    ping(bus)
    p = pong(bus, "station")
    assert p["id"] == "p1" and p["node"] == "station"
    assert p["ok"] is False  # camera has not been heard: the station is in FAULT, and says so
    assert p["detail"]["state"] == "fault" and p["detail"]["health"]["camera"] == FAIL
    bus.publish("station/camera/status", {"source_kind": "camera", "source_status": "running"}, qos=0)
    bus.publish("station/diag/ping", {"id": "p2"}, qos=0)
    assert pong(bus, "station")["ok"] is True


def test_a_ping_creates_no_tile_and_changes_no_state():
    bus = InMemoryBus()
    c = StationCoordinator({}, bus, TileStore(":memory:"), clock=lambda: 10.0)
    c.start()
    before = c.snapshot()
    ping(bus)
    after = c.snapshot()
    assert after["counters"] == before["counters"] and after["awaiting_count"] == 0
    assert not [t for t, *_ in bus.published if t.startswith("tile/")]


def test_gantry_feed_answers_and_a_ping_is_not_a_pick_job():
    bus = InMemoryBus()
    feed = FeedClient(bus, clock=lambda: 5.0)
    feed.start()
    ping(bus)
    p = pong(bus, "gantry")
    assert p["ok"] is True and p["detail"]["queue_depth"] == 0
    assert feed.queue.snapshot()["depth"] == 0 and feed.queue.snapshot()["counters"]["accepted"] == 0


class _Serial:
    def write(self, data):
        pass

    def readline(self):
        return b""

    def close(self):
        pass


def test_nano_answer_reflects_the_serial_link_not_just_the_bridge_process():
    from acoustic.tap_protocol import encode

    bus = InMemoryBus()
    now = [100.0]
    b = TapBridge(_Serial(), bus, clock=lambda: now[0], stale_s=2.0)
    b._bus.subscribe("station/diag/ping", b._on_diag_ping)
    ping(bus)
    assert pong(bus, "nano")["ok"] is False  # bridge is up, Nano never spoke
    b.handle_line(encode("HB 1 40 1 1"), now=100.0)
    ping(bus, "p2")
    assert pong(bus, "nano")["ok"] is True and pong(bus, "nano")["detail"]["belt_pct"] == 40
    now[0] = 110.0  # the Nano has gone quiet
    ping(bus, "p3")
    assert pong(bus, "nano")["ok"] is False


def test_master_answers():
    bus = InMemoryBus()
    m = MasterCollector(clock=lambda: 1.0)
    m.attach(bus)
    ping(bus)
    assert pong(bus, "master")["ok"] is True


class _CamBus:
    def __init__(self):
        self.subs, self.sent = {}, []
        self.connected = True

    def subscribe(self, f, cb):
        self.subs[f] = cb

    def start(self):
        pass

    def stop(self):
        pass

    def publish(self, topic, payload, qos=1, retain=False):
        self.sent.append((topic, payload))
        return True


def test_camera_answers_ok_only_for_a_running_live_camera():
    bus = _CamBus()
    state = {"source_kind": "camera", "source_status": "running", "tile_count": 3}
    p = TilePublisher({"heartbeat_s": 100}, status_source=lambda: state, bus=bus, clock=lambda: 1.0)
    p.start()
    try:
        bus.subs["station/diag/ping"]("station/diag/ping", b'{"id": "x"}')
        topic, payload = [m for m in bus.sent if m[0] == "station/diag/pong/camera"][-1]
        assert payload["id"] == "x" and payload["ok"] is True and payload["detail"]["tile_count"] == 3
        state["source_kind"] = "file"  # replaying a recording: results are not live
        bus.subs["station/diag/ping"]("station/diag/ping", b'{"id": "y"}')
        assert [m for m in bus.sent if m[0] == "station/diag/pong/camera"][-1][1]["ok"] is False
    finally:
        p.stop()
