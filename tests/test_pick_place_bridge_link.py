"""BridgeGantryLink: the g_cmd / g_poll transport to the UNO Q MCU - fake Bridge, no board."""

import threading
import time

import pytest

from pick_place import protocol
from pick_place.bridge_link import BridgeGantryLink
from pick_place.fake_mcu import SimulatedMcu
from pick_place.gantry_backend import SerialGantryBackend, create_backend
from pick_place.gantry_session import GantrySession
from pick_place.mock_state import load_config
from pick_place.serial_link import LinkError


class FakeBridge:
    """Stands in for arduino.app_utils.Bridge. Replies queued by the test come back from
    g_poll in one newline-separated string, exactly like the firmware's bridgePoll()."""

    def __init__(self):
        self.sent = []
        self.pending = []
        self.fail_poll = False
        self.fail_cmd = False
        self.poll_calls = 0
        self._lock = threading.Lock()

    def call(self, name, *args):
        with self._lock:
            if name == "g_cmd":
                if self.fail_cmd:
                    raise TimeoutError("g_cmd timed out")
                self.sent.append(args[0])
                return None
            if name == "g_poll":
                self.poll_calls += 1
                if self.fail_poll:
                    raise TimeoutError("g_poll timed out")
                out, self.pending = self.pending, []
                return "".join(line + "\n" for line in out)
        raise AssertionError(f"unexpected bridge method {name}")


class McuBridge(FakeBridge):
    """A FakeBridge whose far end is the SimulatedMcu, clocked in real time."""

    def __init__(self, mcu):
        super().__init__()
        self.mcu = mcu
        self._last = time.monotonic()

    def call(self, name, *args):
        with self._lock:
            now = time.monotonic()
            self.mcu.tick(now - self._last)
            self._last = now
            if name == "g_cmd":
                self.mcu.write(args[0])
                return None
            return "".join(line + "\n" for line in self.mcu.read_lines())


def wait_for(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.003)
    return False


def test_polled_lines_are_split_stripped_and_blank_lines_dropped():
    bridge = FakeBridge()
    link = BridgeGantryLink(poll_interval_s=0.002, bridge=bridge).open()
    try:
        bridge.pending = ["ok 1", "pos X1.0 Y2.0 Z3.0", "hb 500"]
        assert wait_for(lambda: len(link._lines.queue) >= 3)
        assert link.read_lines() == ["ok 1", "pos X1.0 Y2.0 Z3.0", "hb 500"]
        assert link.read_lines() == []
    finally:
        link.close()
    assert not link.connected


def test_write_line_sends_the_stripped_line_through_g_cmd():
    bridge = FakeBridge()
    link = BridgeGantryLink(poll_interval_s=0.002, bridge=bridge).open()
    try:
        link.write_line("  7 PING \n")
        assert bridge.sent == ["7 PING"] and link.connected
    finally:
        link.close()


def test_a_failed_write_is_a_link_error_and_marks_the_link_lost():
    bridge = FakeBridge()
    link = BridgeGantryLink(poll_interval_s=0.002, bridge=bridge).open()
    try:
        bridge.fail_cmd = True
        with pytest.raises(LinkError):
            link.write_line("1 PING")
        assert not link.connected and "write failed" in link.error
        with pytest.raises(LinkError):
            link.write_line("2 PING")
    finally:
        link.close()


def test_one_failed_poll_is_survived_but_a_run_of_them_loses_the_link():
    bridge = FakeBridge()
    link = BridgeGantryLink(poll_interval_s=0.002, fail_limit=3, bridge=bridge).open()
    try:
        bridge.fail_poll = True
        time.sleep(0.004)
        bridge.fail_poll = False  # a blip: the counter must reset on the next good poll
        assert wait_for(lambda: bridge.poll_calls >= 3)
        assert link.connected
        bridge.fail_poll = True
        assert wait_for(lambda: not link.connected)
        assert "poll failed" in link.error
    finally:
        link.close()


def test_a_link_that_was_never_opened_refuses_writes():
    with pytest.raises(LinkError):
        BridgeGantryLink(bridge=FakeBridge()).write_line("1 PING")


def test_open_without_the_app_lab_runtime_is_a_link_error():
    with pytest.raises(LinkError):
        BridgeGantryLink().open()  # no arduino.app_utils on a laptop


def test_a_full_queue_counts_dropped_lines_instead_of_blocking():
    bridge = FakeBridge()
    link = BridgeGantryLink(poll_interval_s=0.001, bridge=bridge)
    link._lines = type(link._lines)(maxsize=2)
    link.open()
    try:
        bridge.pending = ["a", "b", "c", "d"]
        assert wait_for(lambda: link.dropped_lines >= 2)
        assert link.read_lines() == ["a", "b"]
    finally:
        link.close()


def test_a_session_homes_and_moves_over_the_bridge_link_against_the_simulated_mcu():
    mcu = SimulatedMcu(speed_mm_s=5000, honor_feed=False)
    link = BridgeGantryLink(poll_interval_s=0.002, bridge=McuBridge(mcu)).open()
    session = GantrySession(link)
    try:
        session.send(protocol.home("ALL"))
        assert wait_for(lambda: (session.pump(), session.homed)[1], timeout=5.0)
        assert link.connected
    finally:
        link.close()


def test_the_bridge_backend_is_a_serial_backend_so_the_job_runner_drives_it(monkeypatch):
    cfg = load_config()
    cfg["backend"] = "bridge"
    created = {}

    def fake_open(self):
        created["opened"] = True
        self._bridge = FakeBridge()
        return self

    monkeypatch.setattr(BridgeGantryLink, "open", fake_open)
    backend = create_backend(cfg)
    assert isinstance(backend, SerialGantryBackend) and created["opened"]
