"""Session, simulated-MCU rules, serial link and serial backend — no real port."""

import threading
import time

import pytest

from pick_place import protocol
from pick_place.fake_mcu import LoopbackLink, SimulatedMcu
from pick_place.gantry_backend import (SerialGantryBackend, create_backend, create_job_driver)
from pick_place.gantry_session import GantrySession
from pick_place.mock_state import load_config
from pick_place.serial_link import LinkError, SerialGantryLink


def make(**kw):
    mcu = SimulatedMcu(speed_mm_s=5000, honor_feed=False, **kw)
    t = [0.0]
    session = GantrySession(LoopbackLink(mcu), clock=lambda: t[0])
    return mcu, session, t


def settle(mcu, session, seconds=1.0, dt=0.01):
    for _ in range(int(seconds / dt)):
        mcu.tick(dt)
        session.pump()


# ---- the MCU's rules (what the firmware must also do) -------------------------------------------


def test_not_homed_and_unknown_and_bad_commands():
    mcu, s, _ = make()
    a = s.send(protocol.move(x=10))
    b = s.send(protocol.Command("FLY", ("X1",)))
    s.pump()
    assert (s.result(a).status, s.result(a).reason) == ("err", "NOT_HOMED")
    assert (s.result(b).status, s.result(b).reason) == ("err", "BAD_CMD")
    s.pump()
    mcu.write("garbage")
    events = s.pump()
    assert [type(e).__name__ for e in events] == ["Err"] and events[0].reason == "BAD_CMD"


def test_home_move_done_and_busy_and_out_of_bounds():
    mcu, s, _ = make()
    h = s.send(protocol.home())
    settle(mcu, s)
    assert s.result(h).status == "done" and s.homed and s.fsm == "IDLE"
    m1 = s.send(protocol.move(x=100, y=50, z=100))
    m2 = s.send(protocol.move(x=200))  # while m1 is in flight
    oob = None
    s.pump()
    assert s.result(m2).reason == "BUSY"
    settle(mcu, s)
    assert s.result(m1).status == "done" and s.result(m1).pos == pytest.approx((100.0, 50.0, 100.0))
    oob = s.send(protocol.move(x=9999))
    s.pump()
    assert s.result(oob).reason == "OUT_OF_BOUNDS"


def test_motion_is_acked_before_done_and_vac_ok_is_the_confirmation():
    mcu, s, _ = make()
    s.send(protocol.home())
    settle(mcu, s)
    m = s.send(protocol.move(z=50))
    s.pump()
    assert s.result(m).status == "ok" and not s.result(m).finished
    settle(mcu, s)
    assert s.result(m).finished
    v = s.send(protocol.vac(True))
    s.pump()
    assert not s.result(v).finished  # grip not confirmed yet
    settle(mcu, s, 0.2)
    assert s.result(v).finished and s.vacuum


def test_vacuum_miss_and_alarm_latch_rules():
    mcu, s, _ = make()
    s.send(protocol.home())
    settle(mcu, s)
    mcu.vacuum_failures = 1
    v = s.send(protocol.vac(True))
    settle(mcu, s, 0.2)
    assert s.result(v).reason == "VACUUM_TIMEOUT" and not s.vacuum

    mcu.inject_alarm("LIMIT_Y_MIN")
    s.pump()
    assert s.alarm.reason == "LIMIT_Y_MIN" and s.alarm.axis == "Y"
    blocked = s.send(protocol.move(x=1))
    allowed = s.send(protocol.get_pos())
    s.pump()
    assert s.result(blocked).reason == "ALARM_ACTIVE" and s.result(allowed).status == "ok"
    c = s.send(protocol.clear_alarm())
    s.pump()
    assert s.result(c).status == "ok" and s.alarm is None and not s.homed  # cleared, but must re-home


def test_stop_cancels_the_motion_with_stopped():
    mcu, s, _ = make()
    s.send(protocol.home())
    settle(mcu, s)
    m = s.send(protocol.move(x=500))
    s.pump()
    s.send(protocol.stop())
    s.pump()
    assert s.result(m).status == "err" and s.result(m).reason == "STOPPED"


def test_setenv_changes_the_bounds_the_mcu_enforces():
    mcu, s, _ = make()
    s.send(protocol.home())
    settle(mcu, s)
    s.send(protocol.set_env((0, 100), (0, 100), (0, 200)))
    m = s.send(protocol.move(x=150))
    s.pump()
    assert s.result(m).reason == "OUT_OF_BOUNDS"


def test_pos_lines_carry_encoders_and_heartbeats_are_tracked():
    mcu, s, t = make()
    s.send(protocol.home())
    settle(mcu, s)
    s.send(protocol.move(x=100))
    settle(mcu, s, 0.5)
    mcu.enc_offset["Z"] = 0.4
    s.send(protocol.get_pos())
    s.pump()
    assert s.pos.enc["Z"] == pytest.approx(s.pos.z + 0.4)
    assert s.last_hb_uptime_ms is not None and s.heartbeat_age_s() is not None


# ---- serial link with a fake port --------------------------------------------------------------


class FakePort:
    def __init__(self, *a, **kw):
        self.written = []
        self.to_read = []
        self.closed = False
        self.fail_read = False
        self._gate = threading.Event()

    def readline(self):
        if self.fail_read:
            raise OSError("device disconnected")
        if self.to_read:
            return self.to_read.pop(0)
        time.sleep(0.005)
        return b""

    def write(self, data):
        self.written.append(data)

    def close(self):
        self.closed = True


def wait_for(cond, timeout=1.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.005)
    return False


def test_serial_link_reads_lines_and_writes_newline_terminated_ascii():
    port = FakePort()
    link = SerialGantryLink("COM9", 115200, serial_factory=lambda *a, **k: port).open()
    try:
        port.to_read = [b"ok 1\r\n", b"\n", b"pos X1.0 Y2.0 Z3.0\n"]
        assert wait_for(lambda: len(link._lines.queue) >= 2)
        assert link.read_lines() == ["ok 1", "pos X1.0 Y2.0 Z3.0"]
        link.write_line("5 PING")
        assert port.written == [b"5 PING\n"] and link.connected
    finally:
        link.close()
    assert port.closed


def test_serial_link_reports_a_lost_cable_and_refuses_writes():
    port = FakePort()
    link = SerialGantryLink("COM9", serial_factory=lambda *a, **k: port).open()
    try:
        port.fail_read = True
        assert wait_for(lambda: not link.connected)
        assert "read failed" in link.error
        with pytest.raises(LinkError):
            link.write_line("1 PING")
    finally:
        link.close()


def test_serial_link_open_failures_are_link_errors():
    def boom(*a, **k):
        raise OSError("no such port")

    with pytest.raises(LinkError):
        SerialGantryLink("COM99", serial_factory=boom).open()
    with pytest.raises(LinkError):
        SerialGantryLink("COM9").write_line("1 PING")  # never opened


def test_session_over_a_dead_link_raises_instead_of_dropping_the_command():
    port = FakePort()
    link = SerialGantryLink("COM9", serial_factory=lambda *a, **k: port).open()
    s = GantrySession(link)
    try:
        port.fail_read = True
        assert wait_for(lambda: not link.connected)
        assert s.connected is False
        with pytest.raises(LinkError):
            s.send(protocol.ping())
    finally:
        link.close()


# ---- the twin's backend over a session -----------------------------------------------------------


def test_backend_reports_real_pose_and_state():
    mcu, s, _ = make()
    b = SerialGantryBackend(s)
    s.send(protocol.home())
    settle(mcu, s)
    pose = b.advance(0.0)
    assert pose.z == pytest.approx(200.0) and pose.machine_state == "IDLE" and pose.mode == "manual"


def test_jog_and_vacuum_send_real_commands_and_take_manual_control():
    mcu, s, _ = make()
    b = SerialGantryBackend(s, initial_mode="jobs")
    b.jog("x", 10)
    b.set_vacuum(True)
    assert b.mode == "manual"
    assert [ln.split()[1] for ln in mcu.received] == ["JOG", "VAC"]
    assert mcu.received[0].endswith("JOG X+10.0 F1500.0")


def test_backend_has_no_patrol_mode_and_bad_axes_are_refused():
    _, s, _ = make()
    b = SerialGantryBackend(s)
    with pytest.raises(ValueError):
        b.set_mode("auto")
    with pytest.raises(ValueError):
        b.jog("w", 1)
    b.set_mode("jobs")
    assert b.mode == "jobs"


def test_factory_builds_sim_backend_and_matching_job_driver():
    from pick_place.coordinate_model import CoordinateModel
    from pick_place.job_runner import JobRunner
    from pick_place.mock_job_driver import MockJobDriver
    from pick_place.sequencer import Feeds
    from pick_place.tile_feed import FinalQueue

    cfg = load_config()
    model, feeds, q = CoordinateModel.from_config(cfg), Feeds.from_config(cfg), FinalQueue()

    backend = create_backend({**cfg, "backend": "sim"})
    try:
        assert isinstance(backend, SerialGantryBackend)
        assert isinstance(create_job_driver(backend, q, model, feeds, cfg), JobRunner)
    finally:
        backend.session._link.close()

    mock = create_backend(cfg)
    assert isinstance(create_job_driver(mock, q, model, feeds, cfg), MockJobDriver)
