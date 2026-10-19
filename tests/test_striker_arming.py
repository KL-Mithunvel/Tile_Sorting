"""The Pi's half of 'the camera raises the striker' (acoustic/striker_arming.py). The Nano's half is in
tests/test_nano_sprung_striker.py (the end-to-end test at its bottom drives the real firmware core)."""

from acoustic.striker_arming import CameraArming
from fusion.transport import InMemoryBus


def camera(bus, tid):
    bus.publish(f"tile/{tid}/camera/result", {"tile_id": tid, "grade": "Grade A", "seq": 1})


def test_each_graded_tile_asks_for_the_striker_up():
    t = [100.0]
    bus = InMemoryBus()
    arm = CameraArming(bus, clock=lambda: t[0])
    arm.attach()
    camera(bus, "T1")
    t[0] += 3.0
    camera(bus, "T2")
    assert bus.messages("station/striker/cmd") == [{"action": "up"}, {"action": "up"}]
    assert arm.requests == 2


def test_two_results_within_the_gap_are_one_request():
    t = [100.0]
    bus = InMemoryBus()
    CameraArming(bus, min_gap_s=0.3, clock=lambda: t[0]).attach()
    camera(bus, "T1")
    t[0] += 0.1
    camera(bus, "T2")
    assert len(bus.messages("station/striker/cmd")) == 1


def test_disabled_it_sends_nothing():
    bus = InMemoryBus()
    CameraArming(bus, enabled=False).attach()
    camera(bus, "T1")
    assert not bus.messages("station/striker/cmd")
