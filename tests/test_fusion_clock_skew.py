"""SkewTracker (pure) and its health entry on the StationCoordinator."""

from fusion.clock_skew import SkewTracker
from fusion.coordinator import OK, WARN, StationCoordinator
from fusion.store import TileStore
from fusion.transport import InMemoryBus


def feed(t: SkewTracker, peer: str, offset: float, n: int = 3, start: float = 100.0):
    for i in range(n):
        rx = start + i * 2.0
        t.observe(peer, rx + offset, rx)
    return start + (n - 1) * 2.0


def test_nothing_reported_until_enough_samples():
    t = SkewTracker(min_samples=3)
    feed(t, "camera", 0.0, n=2)
    assert t.health(105.0) is None
    feed(t, "camera", 0.0, n=3)
    assert t.health(105.0)["level"] == "ok"


def test_in_step_peer_is_ok_and_drifted_peer_warns():
    t = SkewTracker(warn_s=1.0)
    now = feed(t, "camera", 0.2)
    feed(t, "gantry", -4.0)
    h = t.health(now)
    assert h["level"] == "warn"
    assert "gantry -4.0 s" in h["detail"] and "camera +0.2 s" in h["detail"]


def test_one_stale_retained_message_does_not_trigger_a_warning():
    t = SkewTracker(warn_s=1.0)
    t.observe("gantry", 100.0 - 3600.0, 100.0)  # retained message from an hour ago
    for i in range(1, 5):
        rx = 100.0 + 2.0 * i
        t.observe("gantry", rx + 0.02, rx)
    assert t.health(108.0)["level"] == "ok"


def test_missing_or_bad_timestamps_are_ignored():
    t = SkewTracker()
    for bad in (None, "12", True, float("nan"), float("inf")):
        t.observe("camera", bad, 100.0)
    assert t.skews(100.0) == {}


def test_a_peer_that_went_quiet_drops_out():
    t = SkewTracker(stale_s=30.0)
    now = feed(t, "camera", 0.0)
    assert "camera" in t.skews(now)
    assert t.skews(now + 60.0) == {}


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _rig(clock_warn_s=1.0):
    clock, bus = Clock(), InMemoryBus()
    c = StationCoordinator(
        {"health": {"clock_warn_s": clock_warn_s}, "unoq": {"require_ack": True}}, bus, TileStore(":memory:"), clock=clock
    )
    c.start()
    return clock, bus, c


def test_coordinator_has_no_clock_entry_until_a_peer_sends_timestamps():
    clock, bus, c = _rig()
    bus.publish("station/camera/status", {"source_kind": "camera", "source_status": "running"})
    assert "clock" not in c.snapshot()["health"]


def test_coordinator_reports_ok_then_warns_on_a_drifted_camera_clock():
    clock, bus, c = _rig()
    for _ in range(3):
        bus.publish("station/camera/status", {"source_kind": "camera", "source_status": "running", "ts": clock.t + 0.1})
        clock.t += 2.0
    assert c.snapshot()["health"]["clock"]["level"] == OK
    for _ in range(5):
        bus.publish("station/camera/status", {"source_kind": "camera", "source_status": "running", "ts": clock.t + 30.0})
        clock.t += 2.0
    h = c.snapshot()["health"]["clock"]
    assert h["level"] == WARN and "camera" in h["detail"]
    # A clock warning must never make the station fault by itself.
    assert c.snapshot()["state"] != "fault" or "clock" not in c.snapshot()["state_detail"]
