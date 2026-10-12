"""The Nano firmware core (acoustic_node/nano_firmware/nano_core.h), compiled on this
machine and driven over the REAL protocol module (acoustic/tap_protocol.py) and the
REAL Pi-side bridge (acoustic/tap_bridge.py), so a disagreement between the two ends
of the serial link shows up here instead of on the line.

Needs a C++11 compiler (`NANO_HOST_CXX` or `GANTRY_HOST_CXX`, or c++/g++/clang++ on
PATH); skipped without one. NOT a test of the board: pin numbers, the ToF library,
PWM and the AVR flash/RAM fit are all outside this.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "acoustic_node" / "firmware_host"))
import host_nano  # noqa: E402

from acoustic.tap_bridge import TapBridge  # noqa: E402
from acoustic.tap_protocol import checksum, encode, encode_belt, encode_ping, encode_strike, parse_line  # noqa: E402
from fusion.transport import InMemoryBus  # noqa: E402

# Core state codes (nano::State)
WAIT_TILE, SETTLE, ARM, ARM_SETTLE, RELEASE, COOLDOWN, FAULT = range(7)


@pytest.fixture(scope="module")
def exe(tmp_path_factory):
    path = host_nano.build_host(tmp_path_factory.mktemp("nano_host"))
    if path is None:
        pytest.skip("no C++ compiler (set NANO_HOST_CXX or GANTRY_HOST_CXX)")
    return path


@pytest.fixture
def nano(exe):
    n = host_nano.HostNano(exe)
    yield n
    n.close()


def msgs(lines):
    return [parse_line(line) for line in lines]


def of(lines, kind):
    return [m for m in msgs(lines) if m and m["type"] == kind]


def enable(nano, belt=50):
    """What system.sh does once the line is verified."""
    nano.rx(encode_strike(True))
    nano.rx(encode_belt(belt))
    nano.advance(50)  # let the belt-end ToF answer once


def one_tile(nano, ms=1000):
    nano.tof(1, 50)
    out = nano.advance(ms)
    return out


# ---- boot and the wire -------------------------------------------------------------


def test_boot_says_hello_with_a_valid_checksum_and_everything_safe(nano):
    (hello,) = of(nano.boot_lines, "hello")
    assert hello["fw"].startswith("nano-fw")
    p = nano.pins
    assert p["belt"] == 0 and p["arm"] == 0 and p["lock"] == 0
    assert p["strike"] == 0 and p["link"] == 0


def test_every_line_the_nano_writes_parses_cleanly(nano):
    enable(nano)
    out = one_tile(nano)
    out += nano.advance(1500)
    assert out, "the nano wrote nothing"
    assert all(m["type"] != "bad" for m in msgs(out)), msgs(out)


def test_checksum_agrees_with_python_on_every_ping_value(nano):
    # Round trip a spread of values: the Nano must accept what Python encodes
    # and answer with a PONG whose own checksum Python accepts.
    for n in (0, 1, 9, 10, 99, 100, 255, 1000, 9999, 65535):
        pongs = of(nano.rx(encode_ping(n)), "pong")
        assert pongs and pongs[0]["n"] == n


def test_corrupted_belt_command_is_ignored(nano):
    line = encode_belt(90).decode()
    flipped = line.replace("90", "99", 1)  # payload changed, checksum not
    nano.rx(flipped)
    nano.advance(60)
    assert nano.pins["cmd"] == 0 and nano.pins["bad"] == 1
    nano.rx(encode_belt(90))
    assert nano.pins["cmd"] == 90


def test_line_without_checksum_or_with_garbage_is_discarded(nano):
    for bad in ("BELT 80", "BELT 80*", "BELT 80*ZZ", "BELT 80*5", "*", "BELT 80*5E5E"):
        nano.rx(bad)
    assert nano.pins["cmd"] == 0
    assert nano.pins["bad"] == 6
    assert nano.pins["link"] == 0  # none of it counted as the Pi being alive


def test_overlong_line_is_dropped_and_the_next_one_still_works(nano):
    nano.rx_raw(b"X" * 200 + b"\n")
    assert nano.pins["bad"] == 1
    nano.rx(encode_belt(40))
    assert nano.pins["cmd"] == 40


def test_out_of_range_and_unknown_commands_are_rejected_not_clamped(nano):
    nano.rx(encode("BELT 101"))
    nano.rx(encode("BELT -5"))
    nano.rx(encode("STRIKE 2"))
    nano.rx(encode("DANCE 1"))
    assert nano.pins["cmd"] == 0 and nano.pins["strike"] == 0 and nano.pins["bad"] == 4


def test_bad_line_errors_are_rate_limited(nano):
    out = []
    for _ in range(20):
        out += nano.rx("junk")
    assert len(of(out, "err")) == 1
    assert nano.pins["bad"] == 20


# ---- belt and the watchdog -------------------------------------------------------------


def test_belt_follows_a_valid_command_and_heartbeat_reports_it(nano):
    nano.rx(encode_belt(60))
    out = nano.advance(1000)
    assert nano.pins["belt"] == 60
    hbs = of(out, "hb")
    assert hbs and hbs[-1]["belt_pct"] == 60
    assert hbs[-1]["tof3_ok"] is True


def test_heartbeat_period_is_about_500_ms(nano):
    out = nano.advance(2100)
    assert 3 <= len(of(out, "hb")) <= 5


def test_watchdog_stops_belt_and_inhibits_striker_after_2s_of_silence(nano):
    enable(nano, belt=70)
    # Pings keep it alive well past the watchdog period.
    for _ in range(8):
        nano.rx(encode_ping(1))
        nano.advance(500)
    assert nano.pins["belt"] == 70 and nano.pins["strike"] == 1
    # The last ping was 500 ms ago. Still running just before the 2000 ms limit...
    nano.advance(1400)
    assert nano.pins["belt"] == 70
    # ...stopped just after it.
    out = nano.advance(150)
    assert nano.pins["belt"] == 0
    assert nano.pins["strike"] == 0
    assert [m for m in of(out, "err") if m["code"] == "WATCHDOG"]


def test_after_a_watchdog_stop_pings_alone_do_not_restart_the_belt(nano):
    enable(nano, belt=70)
    nano.advance(2500)
    assert nano.pins["belt"] == 0
    for _ in range(6):
        nano.rx(encode_ping(2))
        nano.advance(500)
    assert nano.pins["belt"] == 0 and nano.pins["strike"] == 0
    nano.rx(encode_belt(45))
    nano.advance(60)
    assert nano.pins["belt"] == 45


def test_a_corrupt_stream_does_not_feed_the_watchdog(nano):
    enable(nano, belt=70)
    for _ in range(10):
        nano.rx("PING 1*00")  # wrong checksum, arrives forever
        nano.advance(300)
    assert nano.pins["belt"] == 0


def test_watchdog_does_nothing_before_the_pi_has_ever_spoken(nano):
    out = nano.advance(5000)
    assert not [m for m in of(out, "err") if m["code"] == "WATCHDOG"]


# ---- the strike -----------------------------------------------------------------------


def test_striker_is_inhibited_until_strike_1_arrives(nano):
    nano.rx(encode_belt(50))
    nano.advance(50)
    out = one_tile(nano, 1500)
    assert not of(out, "tap") and nano.pins["lock_rises"] == 0
    assert nano.pins["strike"] == 0


def test_one_tile_gives_one_strike_with_the_right_sequence(nano):
    enable(nano)
    nano.rx(encode_ping(1))
    nano.tof(1, 50)
    out = nano.advance(1100)
    (tap,) = of(out, "tap")
    assert tap["seq"] == 1 and tap["tile_present"] is True and tap["tof_mm"] == 50
    assert nano.pins["arm_rises"] == 1 and nano.pins["lock_rises"] == 1
    # The strike is the LOCK opening, after SETTLE + ARM + ARM_SETTLE (+ ToF debounce).
    assert 650 <= tap["uptime_ms"] - 50 <= 1000
    # Neither solenoid is left energised.
    assert nano.pins["arm"] == 0
    nano.advance(500)
    assert nano.pins["lock"] == 0 and nano.pins["tapled"] == 0


def test_tap_is_sent_the_instant_lock_opens(nano):
    enable(nano)
    nano.tof(1, 50)
    seen = None
    for _ in range(200):
        out = nano.advance(10)
        if of(out, "tap"):
            seen = nano.pins
            break
    assert seen is not None
    assert seen["lock"] == 1 and seen["state"] == RELEASE


def test_same_tile_cannot_strike_twice_and_the_next_tile_gets_seq_2(nano):
    enable(nano)
    nano.tof(1, 50)
    out = []
    for i in range(6):                 # tile just sits there, the Pi keeps pinging
        nano.rx(encode_ping(i))
        out += nano.advance(500)
    assert len(of(out, "tap")) == 1
    nano.rx(encode_ping(2))
    nano.tof(1, 65535)                 # tile leaves
    nano.advance(300)
    nano.rx(encode_ping(3))
    nano.tof(1, 45)                    # next tile
    out = nano.advance(1100)
    (tap,) = of(out, "tap")
    assert tap["seq"] == 2


def test_tap_reports_tile_absent_when_it_left_during_the_cycle(nano):
    enable(nano)
    nano.tof(1, 50)
    nano.advance(500)                  # in SETTLE/ARM
    nano.tof(1, 65535)                 # tile slid away before the drop
    out = nano.advance(700)
    (tap,) = of(out, "tap")
    assert tap["tile_present"] is False


def test_no_reading_at_the_strike_means_no_strike_and_an_error(nano):
    enable(nano)
    nano.tof(1, 50)
    nano.advance(500)
    nano.tof(1, None)                  # sensor stops answering mid-cycle
    out = nano.advance(700)
    assert not of(out, "tap")
    assert nano.pins["lock_rises"] == 0
    assert [m for m in of(out, "err") if m["code"] == "TOF1"]


def test_dead_striker_tof_never_starts_a_cycle_and_is_reported(nano):
    enable(nano)
    nano.tof(1, None)
    out = nano.advance(1500)
    assert not of(out, "tap") and nano.pins["arm_rises"] == 0
    assert [m for m in of(out, "err") if m["code"] == "TOF1"]
    assert of(out, "hb")[-1]["tof1_ok"] is False


def test_a_tile_that_arrived_while_inhibited_is_not_struck_later(nano):
    nano.rx(encode_belt(50))
    nano.advance(50)
    nano.tof(1, 50)
    nano.advance(500)                  # arrives and sits, inhibited
    nano.rx(encode_strike(True))
    out = nano.advance(2000)
    assert not of(out, "tap")


# ---- the belt-end ToF (fall prevention) ---------------------------------------------------


def test_belt_is_held_until_the_end_sensor_has_answered_once(exe):
    n = host_nano.HostNano(exe)
    try:
        n.tof(3, None)                 # unplugged from the start
        n.rx(encode_belt(60))
        out = n.advance(1500)
        assert n.pins["belt"] == 0 and n.pins["cmd"] == 60
        assert [m for m in of(out, "err") if m["code"] == "TOF3"]
        assert of(out, "hb")[-1]["tof3_ok"] is False
    finally:
        n.close()


def test_dead_end_sensor_stops_a_running_belt_and_recovery_resumes_it(nano):
    enable(nano, belt=60)
    assert nano.pins["belt"] == 60
    nano.tof(3, None)
    nano.rx(encode_ping(1))
    nano.advance(300)
    assert nano.pins["belt"] == 0 and nano.pins["cmd"] == 60
    nano.tof(3, 65535)
    nano.rx(encode_ping(2))
    nano.advance(300)
    assert nano.pins["belt"] == 60


def test_tile_at_the_belt_end_holds_the_belt_until_it_is_picked_up(nano):
    enable(nano, belt=60)
    nano.tof(3, 30)                    # tile at the end
    nano.rx(encode_ping(1))
    nano.advance(300)
    assert nano.pins["belt"] == 0 and nano.pins["cmd"] == 60
    nano.tof(3, 65535)                 # picked up
    nano.rx(encode_ping(2))
    nano.advance(300)
    assert nano.pins["belt"] == 60


def test_a_bench_without_an_end_sensor_can_run_when_configured(exe):
    n = host_nano.HostNano(exe, "--no-tof-end")
    try:
        n.tof(3, None)
        n.rx(encode_belt(35))
        n.advance(300)
        assert n.pins["belt"] == 35
    finally:
        n.close()


# ---- e-stop ---------------------------------------------------------------------------------


def test_estop_latches_a_fault_and_nothing_moves_until_reset(nano):
    enable(nano, belt=60)
    nano.tof(1, 50)
    nano.advance(300)                  # mid-cycle, ARM may be on
    nano.estop(True)
    out = nano.advance(50)
    assert nano.pins["state"] == FAULT
    assert nano.pins["arm"] == 0 and nano.pins["lock"] == 0 and nano.pins["belt"] == 0
    assert nano.pins["status"] == 0
    assert [m for m in of(out, "err") if m["code"] == "ESTOP"]
    # Releasing the button does not restart anything.
    nano.estop(False)
    nano.rx(encode_belt(80))
    nano.tof(1, 50)
    out = nano.advance(2000)
    assert nano.pins["state"] == FAULT and nano.pins["belt"] == 0
    assert not of(out, "tap")


# ---- against the real Pi-side bridge -----------------------------------------------------------


class FakeSerial:
    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)

    def close(self):
        pass


def test_the_real_bridge_turns_nano_taps_into_station_tap_messages(nano):
    bus = InMemoryBus()
    ser = FakeSerial()
    now = [100.0]
    bridge = TapBridge(ser, bus, clock=lambda: now[0], ping_s=0.5)

    def pump(lines):
        for line in lines:
            bridge.handle_line(line, now[0])

    pump(nano.boot_lines)
    enable(nano)
    nano.tof(1, 50)
    for _ in range(30):
        pump(nano.advance(100))
        now[0] += 0.1
        bridge.tick(now[0])
        for data in ser.written:         # the bridge's own PINGs go back to the Nano
            nano.rx(data)
        ser.written.clear()
    taps = bus.messages("station/tap")
    assert len(taps) == 1
    assert taps[0]["seq"] == 1 and taps[0]["tile_present"] is True and taps[0]["tof_mm"] == 50
    assert bridge.counters["bad_lines"] == 0 and bridge.counters["lost_taps"] == 0
    # And the keep-alives the bridge sent were enough to keep the belt running.
    assert nano.pins["belt"] == 50 and nano.pins["link"] == 1
    status = bus.messages("station/nano/status")[-1]
    assert status["link"] is True and status["tof1_ok"] is True and status["tof3_ok"] is True


def test_bridge_silence_trips_the_nano_watchdog(nano):
    enable(nano, belt=50)
    assert nano.pins["belt"] == 50
    nano.advance(2300)                 # the bridge process died: no pings at all
    assert nano.pins["belt"] == 0


def test_python_and_c_checksums_are_the_same_function():
    # Cheap guard that tap_protocol.checksum is a plain XOR (what the C++ computes).
    assert checksum("BELT 55") == 0x3F
    assert encode("BELT 55") == b"BELT 55*3F\n"


def test_nano_announces_when_the_pi_is_back_after_a_watchdog_trip(nano):
    enable(nano, belt=70)
    nano.advance(2300)                              # trip
    assert nano.pins["belt"] == 0
    out = nano.rx(encode_ping(1))                   # first valid line afterwards
    assert [m for m in of(out, "err") if m["code"] == "WDOGRESET"]
    out = nano.rx(encode_ping(2))                   # only once
    assert not [m for m in of(out, "err") if m["code"] == "WDOGRESET"]
    assert nano.pins["belt"] == 0                   # still nothing restarted by itself


def test_bridge_resend_restarts_the_belt_after_a_trip_end_to_end(nano):
    bus = InMemoryBus()
    ser = FakeSerial()
    bridge = TapBridge(ser, bus, clock=lambda: 100.0, resend_on_recover=True)
    bridge._on_belt("station/belt", b'{"percent": 55}')
    bridge._on_strike("station/strike", b'{"enabled": true}')
    for data in ser.written:
        nano.rx(data)
    ser.written.clear()
    nano.advance(100)
    assert nano.pins["belt"] == 55 and nano.pins["strike"] == 1
    nano.advance(2300)                              # the bridge process died
    assert nano.pins["belt"] == 0 and nano.pins["strike"] == 0
    for line in nano.rx(encode_ping(1)):            # it comes back and pings
        bridge.handle_line(line, 101.0)
    for data in ser.written:                        # ...and the bridge re-sends
        nano.rx(data)
    nano.advance(100)
    assert nano.pins["belt"] == 55 and nano.pins["strike"] == 1
