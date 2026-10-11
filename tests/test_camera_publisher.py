"""camera/publisher.py: tile ids, the live-only rule, failure containment."""

import threading

from camera.publisher import TilePublisher, build_camera_payload, make_tile_id, status_from_shared_state


class FakeBus:
    def __init__(self, ok=True):
        self.ok, self.sent, self.started, self.stopped = ok, [], False, False
        self.connected = True

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def publish(self, topic, payload, qos=1, retain=False):
        if not self.ok:
            return False
        self.sent.append((topic, payload, qos, retain))
        return True


REC = {"seq": 3, "grade": "Grade B", "rule_grade": "Grade B", "crack_detected": True,
       "crack_severity": "minor", "corner_broken": False, "snapshot_path": "data/camera_captures/t.jpg"}


def make(bus=None, clock=lambda: 1_700_000_000.0):
    return TilePublisher({"heartbeat_s": 0.05}, bus=bus or FakeBus(), clock=clock)


def test_tile_ids_are_unique_even_if_camera_seq_repeats():
    p = make()
    a = p.publish_tile(dict(REC, seq=1), "camera")
    b = p.publish_tile(dict(REC, seq=1), "camera")  # camera seq restarted after a source switch
    assert a != b and a.startswith("T") and a == make_tile_id(p.run_id, 1)


def test_only_a_live_camera_is_forwarded():
    bus = FakeBus()
    p = make(bus)
    assert p.publish_tile(REC, "file") is None
    assert p.publish_tile(REC, "none") is None
    assert bus.sent == []


def test_payload_carries_the_contract_fields():
    bus = FakeBus()
    p = make(bus)
    tid = p.publish_tile(REC, "camera")
    topic, payload, qos, retain = bus.sent[0]
    assert topic == f"tile/{tid}/camera/result" and qos == 1 and retain is False
    assert payload["tile_id"] == tid and payload["grade"] == "Grade B" and payload["seq"] == 3
    assert payload["snapshot_path"].endswith("t.jpg")


def test_broker_refusal_is_counted_and_never_raises():
    p = make(FakeBus(ok=False))
    assert p.publish_tile(REC, "camera") is None
    assert p.dropped == 1 and p.published == 0


def test_bus_exception_is_contained():
    class Boom(FakeBus):
        def publish(self, *a, **k):
            raise OSError("socket closed")

    assert make(Boom()).publish_tile(REC, "camera") is None


def test_heartbeat_is_published_retained_and_stop_says_offline():
    bus = FakeBus()
    p = TilePublisher({"heartbeat_s": 0.02}, status_source=lambda: {"source_kind": "camera"}, bus=bus)
    p.start()
    threading.Event().wait(0.15)
    p.stop()
    beats = [m for m in bus.sent if m[0] == "station/camera/status"]
    assert beats and beats[0][3] is True and beats[0][1]["source_kind"] == "camera"
    assert beats[-1][1] == {"state": "offline"} and bus.stopped


def test_status_from_shared_state_reports_source_and_count():
    class S:
        def status(self):
            return {"source": {"kind": "camera", "status": "running"}, "station_state": "waiting",
                    "tile_count": 7, "errors": {"count": 0}}

    assert status_from_shared_state(S()) == {
        "source_kind": "camera", "source_status": "running", "station_state": "waiting", "tile_count": 7, "errors": 0}
