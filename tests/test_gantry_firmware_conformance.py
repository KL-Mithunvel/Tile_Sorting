"""The gantry firmware core (pick_place_node/sketch/gantry_core.h), compiled on this
machine and driven over the real line protocol.

Part 1 runs the SAME scenarios against `SimulatedMcu` (the Python executable spec)
and the compiled C++ core, so a divergence between spec and firmware shows up as
one side failing a test the other passes. Part 2 is firmware-only behaviour the
spec does not model (trapezoid timing, homing geometry, watchdog, encoders).

Needs a C++11 compiler (`GANTRY_HOST_CXX`, or c++/g++/clang++ on PATH); skipped
without one. NOT a test of the board: pins, polarity, steps/mm, timers and the
AS5600 are all outside this.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pick_place_node" / "firmware_host"))
import host_mcu  # noqa: E402

from pick_place.coordinate_model import CoordinateModel  # noqa: E402
from pick_place.fake_mcu import LoopbackLink, SimulatedMcu  # noqa: E402
from pick_place.gantry_session import GantrySession  # noqa: E402
from pick_place.job_runner import JobRunner  # noqa: E402
from pick_place.mock_state import load_config  # noqa: E402
from pick_place.protocol import Alarm, Done, Err, Ok, Pos, State, parse_line  # noqa: E402
from pick_place.sequencer import Feeds  # noqa: E402
from pick_place.tile_feed import FinalQueue  # noqa: E402


@pytest.fixture(scope="module")
def exe(tmp_path_factory):
    path = host_mcu.build_host(tmp_path_factory.mktemp("gantry_host"))
    if path is None:
        pytest.skip("no C++ compiler (set GANTRY_HOST_CXX)")
    return path


class Machine:
    """One MCU (spec or firmware) behind the same tiny driver."""

    def __init__(self, kind, exe, encoders=False, link_timeout=None, vac_timeout_ms=None):
        self.kind = kind
        cfg = load_config()
        ws = cfg["workspace"]
        if kind == "sim":
            self.mcu = SimulatedMcu(envelope=((ws["x_min_mm"], ws["x_max_mm"]), (ws["y_min_mm"], ws["y_max_mm"]),
                                              (ws["z_min_mm"], ws["z_max_mm"])), speed_mm_s=750, honor_feed=False)
        else:
            args = ["--fast"]
            if encoders:
                args.append("--encoders")
            if link_timeout:
                args += ["--link-timeout", str(link_timeout)]
            if vac_timeout_ms:
                args += ["--vac-timeout", str(vac_timeout_ms)]
            self.mcu = host_mcu.HostMcu(exe, *args)
        self.seq = 0
        self.events = []
        self.log = []

    def close(self):
        if self.kind == "host":
            self.mcu.close()

    # ---- driving -----------------------------------------------------------------
    def send(self, text):
        self.seq += 1
        self.mcu.write(f"{self.seq} {text}")
        self._drain()
        return self.seq

    def _drain(self):
        for line in self.mcu.read_lines():
            self.log.append(line)
            self.events.append(parse_line(line))

    def run(self, seconds, dt=0.01):
        for _ in range(int(round(seconds / dt))):
            self.mcu.tick(dt)
            self._drain()

    def take(self):
        out, self.events = self.events, []
        return out

    def run_pinging(self, seconds, every=0.2):
        """What the Pi's runner does during a motion: PING, so the link watchdog stays quiet."""
        for _ in range(int(round(seconds / every))):
            self.run(every)
            self.send("PING")

    def outcome(self, seq):
        res = {"ok": False, "err": None, "done": None}
        for e in self.events:
            if isinstance(e, Ok) and e.seq == seq:
                res["ok"] = True
            elif isinstance(e, Err) and e.seq == seq:
                res["err"] = e.reason
            elif isinstance(e, Done) and e.seq == seq:
                res["done"] = (e.x, e.y, e.z)
        return res

    def last_state(self):
        states = [e for e in self.events if isinstance(e, State)]
        return states[-1] if states else None

    def alarms(self):
        return [e.reason for e in self.events if isinstance(e, Alarm)]

    def home(self, pinging=False):
        seq = self.send("HOME ALL")
        (self.run_pinging if pinging else self.run)(8.0)
        assert self.outcome(seq)["done"] is not None, self.log[-10:]
        return seq

    # ---- fault injection (the only place the two differ) --------------------------------
    def estop(self, on):
        if self.kind == "sim":
            if on:
                self.mcu.inject_alarm("ESTOP")
        else:
            self.mcu.control(f"@estop {1 if on else 0}")

    def lose_steps(self, axis, mm):
        if self.kind == "sim":
            self.mcu.enc_offset[axis] = mm
        else:
            self.mcu.control(f"@enc_offset {axis} {mm}")


@pytest.fixture(params=["sim", "host"])
def machine(request, exe):
    m = Machine(request.param, exe, encoders=True)
    yield m
    m.close()


# =============================================================================================
# Part 1 - the spec's behaviours, on both
# =============================================================================================


def test_power_up_needs_homing(machine):
    machine._drain()
    st = machine.last_state()
    assert st is not None and st.fsm == "NEEDS_HOMING" and st.homed is False and st.vacuum is False


def test_every_command_is_answered_and_garbage_is_bad_cmd(machine):
    s = machine.send("PING")
    assert machine.outcome(s)["ok"]
    s = machine.send("FLY X1")
    assert machine.outcome(s)["err"] == "BAD_CMD"
    machine.mcu.write("not a command")
    machine._drain()
    assert any(isinstance(e, Err) and e.reason == "BAD_CMD" for e in machine.events)


def test_motion_before_homing_is_refused(machine):
    for cmd in ("MOVE X10", "JOG X+5"):
        s = machine.send(cmd)
        assert machine.outcome(s)["err"] == "NOT_HOMED", cmd


def test_home_all_ends_idle_at_the_home_corner(machine):
    seq = machine.send("HOME ALL")
    assert machine.outcome(seq)["ok"]
    machine.run(8.0)
    out = machine.outcome(seq)
    assert out["done"] == (0.0, 0.0, 200.0)
    st = machine.last_state()
    assert st.fsm == "IDLE" and st.homed is True


def test_move_arrives_and_reports_done_with_the_final_position(machine):
    machine.home()
    machine.take()
    s = machine.send("MOVE X250 Y100 Z50 F6000")
    assert machine.outcome(s)["ok"]
    assert machine.last_state().fsm == "MOVING"
    machine.run(6.0)
    d = machine.outcome(s)["done"]
    assert d is not None and abs(d[0] - 250) < 0.1 and abs(d[1] - 100) < 0.1 and abs(d[2] - 50) < 0.1
    assert machine.last_state().fsm == "IDLE"
    # a `pos` while moving carries the position the Pi's HMI shows
    assert any(isinstance(e, Pos) for e in machine.events)


def test_out_of_bounds_and_busy(machine):
    machine.home()
    machine.take()
    assert machine.outcome(machine.send("MOVE X9999"))["err"] == "OUT_OF_BOUNDS"
    assert machine.outcome(machine.send("MOVE Z-5"))["err"] == "OUT_OF_BOUNDS"
    first = machine.send("MOVE X300 F3000")
    assert machine.outcome(first)["ok"]
    assert machine.outcome(machine.send("MOVE X10"))["err"] == "BUSY"


def test_jog_is_relative_and_needs_exactly_one_axis(machine):
    machine.home()
    machine.take()
    assert machine.outcome(machine.send("JOG X+10 Y+10"))["err"] == "BAD_CMD"
    s = machine.send("JOG Z-20 F6000")
    machine.run(3.0)
    d = machine.outcome(s)["done"]
    assert d is not None and abs(d[2] - 180.0) < 0.1


def test_stop_cancels_the_motion_in_flight_without_losing_the_home(machine):
    machine.home()
    machine.take()
    m = machine.send("MOVE X500 F3000")
    machine.run(0.2)
    stop = machine.send("STOP")
    machine.run(0.1)
    assert machine.outcome(stop)["ok"]
    assert machine.outcome(m)["err"] == "STOPPED" and machine.outcome(m)["done"] is None
    st = machine.last_state()
    assert st.fsm == "IDLE" and st.homed is True
    # and the machine still moves afterwards
    s = machine.send("MOVE X100 F6000")
    assert machine.outcome(s)["ok"]


def test_vacuum_on_is_answered_only_when_the_grip_is_confirmed(machine):
    s = machine.send("VAC ON")
    assert not machine.outcome(s)["ok"]  # not yet: waiting for the sensor
    assert machine.last_state().vacuum is True  # the pump is running
    machine.run(0.5)
    assert machine.outcome(s)["ok"]
    off = machine.send("VAC OFF")
    assert machine.outcome(off)["ok"] and machine.last_state().vacuum is False


def test_a_missed_grip_is_vacuum_timeout_and_drops_the_pump(machine):
    if machine.kind == "sim":
        machine.mcu.vacuum_failures = 1
    else:
        machine.mcu.control("@vac_fail 1")
    s = machine.send("VAC ON")
    machine.run(3.0)
    assert machine.outcome(s)["err"] == "VACUUM_TIMEOUT"
    assert machine.last_state().vacuum is False


def test_an_alarm_latches_refuses_motion_and_needs_clear_plus_rehome(machine):
    machine.home()
    machine.take()
    m = machine.send("MOVE X400 F3000")
    machine.run(0.1)
    machine.estop(True)
    machine.run(0.2)
    assert machine.outcome(m)["err"] == "STOPPED"
    assert "ESTOP" in machine.alarms()
    st = machine.last_state()
    assert st.fsm == "ALARM" and st.homed is False
    assert machine.outcome(machine.send("MOVE X10"))["err"] == "ALARM_ACTIVE"
    assert machine.outcome(machine.send("HOME ALL"))["err"] == "ALARM_ACTIVE"
    assert machine.outcome(machine.send("PING"))["ok"]  # the safe verbs still answer
    machine.estop(False)
    assert machine.outcome(machine.send("CLEARALARM"))["ok"]
    st = machine.last_state()
    assert st.fsm == "NEEDS_HOMING" and st.homed is False
    assert machine.outcome(machine.send("MOVE X10"))["err"] == "NOT_HOMED"
    machine.home()
    assert machine.last_state().homed is True


def test_lost_steps_beyond_the_tolerance_raise_the_closed_loop_alarm(machine):
    machine.home()
    machine.take()
    assert machine.outcome(machine.send("SETTOL LOST1.0 SKEW0.5"))["ok"]
    machine.lose_steps("X", 3.0)
    machine.run(1.0)
    assert "LOST_STEPS_X" in machine.alarms()
    assert machine.last_state().homed is False


def test_skew_between_the_two_y_motors_raises_skew_y(machine):
    machine.home()
    machine.take()
    machine.send("SETTOL LOST5.0 SKEW0.5")
    machine.lose_steps("A", 2.0)
    machine.run(1.0)
    assert "SKEW_Y" in machine.alarms()


def test_setenv_changes_the_bounds_and_rejects_nonsense(machine):
    machine.home()
    machine.take()
    assert machine.outcome(machine.send("SETENV X0 X100 Y0 Y100 Z0 Z200"))["ok"]
    assert machine.outcome(machine.send("MOVE X150"))["err"] == "OUT_OF_BOUNDS"
    assert machine.outcome(machine.send("SETENV X10 X5 Y0 Y100 Z0 Z200"))["err"] == "BAD_CMD"
    assert machine.outcome(machine.send("SETENV X0 X100"))["err"] == "BAD_CMD"
    assert machine.outcome(machine.send("SETTOL LOST0 SKEW1"))["err"] == "BAD_CMD"


# ---- the real JobRunner, on the spec and on the firmware -------------------------------------


class RunnerRig:
    def __init__(self, machine):
        self.cfg = load_config()
        self.t = 0.0
        self.m = machine
        self.session = GantrySession(LoopbackLink(machine.mcu), clock=lambda: self.t)
        self.queue = FinalQueue()
        self.model = CoordinateModel.from_config(self.cfg)
        self.done = []
        self.runner = JobRunner(self.session, self.queue, self.model, Feeds.from_config(self.cfg), self.cfg,
                                clock=lambda: self.t, enabled=lambda: True,
                                on_job_done=lambda *a: self.done.append(a))

    def run(self, seconds, dt=0.01):
        for _ in range(int(round(seconds / dt))):
            self.t += dt
            self.m.mcu.tick(dt)
            self.runner.tick()

    def add(self, tile_id="T1", dest="bin_2"):
        self.queue.accept({"tile_id": tile_id, "final_grade": "Grade B", "destination": dest, "attempt": 1}, 0)


@pytest.fixture(params=["sim", "host"])
def rig(request, exe):
    m = Machine(request.param, exe)
    r = RunnerRig(m)
    r.run(0.5)
    assert r.runner.request_home()
    r.run(8.0)
    assert r.session.homed and r.runner.status()["phase"] == "idle", r.runner.status()
    yield r
    m.close()


def test_runner_places_a_tile_on_the_named_stack(rig):
    rig.add("T1", "bin_2")
    rig.run(30.0)
    assert rig.done == [("T1", True, None)], (rig.done, rig.runner.status())
    assert rig.model.count("bin_2") == 1


def test_runner_places_two_tiles_in_order_and_stacks_them(rig):
    rig.add("T1", "bin_1")
    rig.add("T2", "bin_1")
    rig.run(60.0)
    assert [d[0] for d in rig.done if d[1]] == ["T1", "T2"] and rig.model.count("bin_1") == 2


def test_runner_retries_a_missed_grip_once_then_fails_cleanly(rig):
    if rig.m.kind == "sim":
        rig.m.mcu.vacuum_failures = 2
    else:
        rig.m.mcu.control("@vac_fail 2")
    rig.add("T1", "bin_1")
    rig.run(40.0)
    (d,) = rig.done
    assert d[0] == "T1" and d[1] is False and "VACUUM_TIMEOUT" in (d[2] or "")
    assert rig.model.count("bin_1") == 0 and rig.runner.status()["phase"] != "job"


def test_runner_aborts_the_job_on_an_estop_mid_move(rig):
    rig.add("T1", "bin_2")
    rig.run(1.0)
    rig.m.estop(True)
    rig.run(2.0)
    assert rig.done and rig.done[0][1] is False
    assert rig.runner.status()["alarm"] == "ESTOP"
    assert rig.model.count("bin_2") == 0


# =============================================================================================
# Part 2 - firmware-only behaviour
# =============================================================================================


@pytest.fixture
def fw(exe):
    m = Machine("host", exe, encoders=True)
    yield m
    m.close()


def test_number_formatting_matches_the_python_side(fw):
    from pick_place.protocol import fmt_mm

    for v in (0.0, 500.0, 12.5, 0.125, -3.0, 199.9999, 0.0004, -0.0004, 1234.5678, 0.1, 2.0005):
        assert fw.mcu.fmt(v) == fmt_mm(v), v


def test_trapezoid_move_time_matches_the_analytic_profile(exe):
    """100 mm at 6000 mm/min with the real (slow) default limits: accel 400 mm/s^2,
    100 mm/s cruise -> t = 100/100 + 100/400 = 1.25 s (the generator starts from a
    small minimum speed, so a few percent slower is expected)."""
    m = host_mcu.HostMcu(exe)  # defaults, NOT --fast: the real steps/mm and limits
    try:
        mach = Machine.__new__(Machine)
        mach.kind, mach.mcu, mach.seq, mach.events, mach.log = "host", m, 0, [], []
        seq = mach.send("HOME ALL")
        mach.run(120.0, dt=0.05)
        assert mach.outcome(seq)["done"] is not None
        mach.take()
        seq = mach.send("MOVE X100 F6000")
        elapsed = 0.0
        while mach.outcome(seq)["done"] is None and elapsed < 5.0:
            mach.run(0.01)
            elapsed += 0.01
        assert 1.2 < elapsed < 1.45, elapsed
        assert m.steps("X") > 0
    finally:
        m.close()


def test_exact_step_count_for_a_move(fw):
    fw.home()
    fw.take()
    before = fw.mcu.steps("X")
    s = fw.send("MOVE X250 F30000")
    fw.run(3.0)
    assert fw.outcome(s)["done"] is not None
    assert fw.mcu.steps("X") - before == 250 * 20  # --fast: 20 steps/mm


def test_homing_order_is_z_then_x_then_y(fw):
    s = fw.send("HOME ALL")
    first_moved = []
    for _ in range(800):
        fw.mcu.tick(0.01)
        fw._drain()
        for axis in "ZXY":
            if axis not in first_moved and fw.mcu.steps(axis) > 0:
                first_moved.append(axis)
        if fw.outcome(s)["done"] is not None:
            break
    assert first_moved == ["Z", "X", "Y"]


def test_homing_references_the_axis_to_the_switch_not_to_where_it_started(fw):
    fw.home()
    # real positions: X/Y rest `backoff` (2 mm) off their switch at real 0, Z 2 mm below its at real 220
    assert abs(fw.mcu.phys("X") - 2.0) < 0.15
    assert abs(fw.mcu.phys("Y") - 2.0) < 0.15
    assert abs(fw.mcu.phys("Z") - 218.0) < 0.15


def test_a_limit_switch_hit_mid_move_stops_within_a_step_and_alarms(fw):
    fw.home()
    fw.send("MOVE X300 F30000")
    fw.run(2.0)
    fw.take()
    s = fw.send("MOVE X0 F30000")  # back toward the X switch
    fw.run(0.1)
    fw.mcu.control("@limit X 1")
    steps_at_hit = fw.mcu.steps("X")
    fw.run(0.3)
    assert fw.mcu.steps("X") - steps_at_hit <= 1
    assert "LIMIT_X_MIN" in fw.alarms() and fw.outcome(s)["err"] == "STOPPED"


def test_moving_away_from_a_pressed_switch_is_allowed(fw):
    fw.home()
    fw.take()
    fw.mcu.control("@limit X 1")  # pressed while at rest
    s = fw.send("MOVE X50 F30000")
    fw.run(2.0)
    assert fw.outcome(s)["done"] is not None and not fw.alarms()


def test_estop_cannot_be_cleared_until_the_loop_is_closed(fw):
    fw.home()
    fw.take()
    fw.estop(True)
    fw.run(0.1)
    assert fw.mcu.control("@dump")[0].split()[1] == "enabled=0"  # drivers dropped
    assert fw.outcome(fw.send("CLEARALARM"))["err"] == "ALARM_ACTIVE"
    fw.estop(False)
    assert fw.outcome(fw.send("CLEARALARM"))["ok"]
    assert fw.mcu.control("@dump")[0].split()[1] == "enabled=1"


def test_a_homing_that_never_finds_its_switch_is_an_alarm_not_a_silent_success(fw):
    fw.mcu.control("@switch X -100000")  # the X switch is unreachable
    s = fw.send("HOME ALL")
    fw.run(20.0)
    assert fw.outcome(s)["done"] is None and fw.outcome(s)["err"] == "STOPPED"
    assert "HOME_FAIL" in fw.alarms()
    assert fw.last_state().homed is False and fw.last_state().fsm == "ALARM"


def test_link_watchdog_stops_a_move_when_the_pi_goes_silent(exe):
    m = Machine("host", exe, link_timeout=400)
    try:
        m.home(pinging=True)
        m.take()
        s = m.send("MOVE X500 F3000")
        m.run(1.0)  # no further commands
        assert "LINK_TIMEOUT" in m.alarms() and m.outcome(s)["err"] == "STOPPED"
    finally:
        m.close()


def test_pings_keep_the_watchdog_quiet_during_a_long_move(exe):
    m = Machine("host", exe, link_timeout=400)
    try:
        m.home(pinging=True)
        m.take()
        s = m.send("MOVE X500 F6000")
        m.run_pinging(8.0)
        assert not m.alarms()
        assert m.outcome(s)["done"] is not None
    finally:
        m.close()


def test_the_watchdog_is_idle_when_nothing_is_moving(exe):
    m = Machine("host", exe, link_timeout=400)
    try:
        m.run(3.0)
        assert not m.alarms()
    finally:
        m.close()


def test_an_unreadable_encoder_is_a_fault_not_a_pass(fw):
    fw.home()
    fw.take()
    fw.mcu.control("@enc_fail X 1")
    fw.run(0.5)
    assert any(a.startswith("ENCODER_X") for a in fw.alarms())


def test_encoder_wrap_around_is_unwrapped(fw):
    """Moving more than one encoder revolution (40 mm of belt) must not look like lost steps."""
    fw.home()
    fw.take()
    s = fw.send("MOVE X300 F30000")
    fw.run(3.0)
    assert fw.outcome(s)["done"] is not None and not fw.alarms()
    s = fw.send("MOVE X0 F30000")
    fw.run(3.0)
    assert fw.outcome(s)["done"] is not None and not fw.alarms()
    pos = [e for e in fw.events if isinstance(e, Pos) and e.enc]
    assert pos and all(abs(p.enc["X"] - p.x) < 1.0 for p in pos[-5:])


def test_a_sensor_glitch_shorter_than_the_debounce_is_not_a_grip(exe):
    m = Machine("host", exe, vac_timeout_ms=400)
    try:
        m.mcu.control("@vac_confirm 10")
        m.mcu.control("@vac_hold 8")  # confirms for 8 ms only, below the 20 ms debounce
        s = m.send("VAC ON")
        m.run(1.0)
        assert not m.outcome(s)["ok"] and m.outcome(s)["err"] == "VACUUM_TIMEOUT"
    finally:
        m.close()
