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


# ---- AutoArm: armed ONLY while a tile is in the system ------------------------------------------------------------------

from acoustic.striker_arming import AutoArm  # noqa: E402


class World:
    """A fake clock, the bus, and the Nano's status messages."""

    def __init__(self, **kw):
        self.t = 1000.0
        self.bus = InMemoryBus()
        self.waiting = 0
        self.arm = AutoArm(self.bus, awaiting=lambda: self.waiting, clock=lambda: self.t, **kw)
        self.arm.attach()
        self.status(armed=False)

    def status(self, **fields):
        body = {"state": "online", "link": True, "manual": False, "armed": False, "tof1_mm": 140, "tof1_ok": True,
                "nano_state": 0, "up_wanted": False, "must_leave": False, "retracted": False}
        body.update(fields)
        self._last = body
        self.bus.publish("station/nano/status", body)

    def step(self, dt=0.25, **fields):
        self.t += dt
        if fields:
            self._last.update(fields)
        self.bus.publish("station/nano/status", self._last)
        self.arm.tick()

    def strikes(self):
        return [m["enabled"] for m in self.bus.messages("station/strike")]


def test_it_stays_disarmed_with_no_tile_in_the_system():
    w = World()
    for _ in range(40):
        w.step()
    assert w.strikes() == [] and w.arm.in_system is False


def test_a_camera_tile_waiting_for_its_tap_arms_it_and_the_nano_reading_it_back_stops_the_repeats():
    w = World()
    w.waiting = 1
    w.step()
    assert w.strikes() == [True]
    w.step(armed=True)                                   # the Nano reports it is armed: nothing more to send
    for _ in range(8):
        w.step()
    assert w.strikes() == [True]
    assert "camera" in w.arm.reason


def test_an_unconfirmed_arm_is_repeated_but_not_every_tick():
    w = World(resend_s=1.0)
    w.waiting = 1
    for _ in range(8):                                   # 2 s of a Nano that never confirms
        w.step()
    assert w.strikes() == [True, True]


def test_a_tile_under_the_sensor_arms_it_even_if_the_camera_missed_it():
    w = World()
    w.step(tof1_mm=88)
    assert w.strikes() == [True] and "under the sensor" in w.arm.reason


def test_a_pending_request_keeps_it_armed_and_the_nanos_own_strike_state_is_not_a_reason():
    w = World()
    w.step(armed=True, up_wanted=True)
    for _ in range(20):
        w.step()
    assert w.strikes() == []                             # already armed and wanted: no disarm
    w.step(up_wanted=False, nano_state=8, armed=True)    # STRUCK -- but also what a quiet rest lowering passes through
    assert w.arm.in_system is False


def test_it_disarms_only_after_the_grace_and_a_flicker_does_not_flap_it():
    w = World(grace_s=2.0)
    w.step(armed=True, tof1_mm=60)
    w.step(tof1_mm=140)
    for _ in range(4):                                   # 1 s clear
        w.step()
    w.step(tof1_mm=60)                                   # the sensor flickers back: the countdown restarts
    for _ in range(6):
        w.step(tof1_mm=140)
    assert w.strikes() == []
    for _ in range(12):
        w.step()
    assert w.strikes() and set(w.strikes()) == {False}   # now it really was clear for 2 s (repeated until the Nano confirms)


def test_a_graded_tile_that_times_out_in_the_ledger_stops_holding_it_armed():
    w = World(grace_s=2.0)
    w.waiting = 1
    w.step(armed=True)
    for _ in range(4):
        w.step()
    w.waiting = 0                                        # the coordinator gave up on it (max wait): Hold, not waiting
    for _ in range(12):
        w.step()
    assert w.strikes() and set(w.strikes()) == {False}


def test_manual_mode_a_dead_link_a_stale_status_and_enabled_false_all_leave_the_arming_alone():
    w = World()
    w.waiting = 1
    w.step(manual=True)
    assert w.strikes() == [] and "MANUAL" in w.arm.reason
    w.step(manual=False, link=False)
    assert w.strikes() == []
    w.t += 10.0                                          # status went stale
    w.arm.tick()
    assert w.strikes() == []
    w.arm.set_enabled(False)
    w.step(link=True)
    assert w.strikes() == [] and "off" in w.arm.reason
    w.arm.set_enabled(True)
    w.step()
    assert w.strikes() == [True]


def test_status_reports_enabled_in_system_and_reason():
    w = World()
    w.waiting = 2
    w.step()
    s = w.arm.status()
    assert s["enabled"] is True and s["in_system"] is True and "2 tile" in s["reason"] and s["armed_commands"] == 1
