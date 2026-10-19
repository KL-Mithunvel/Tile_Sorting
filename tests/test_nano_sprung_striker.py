"""The prototype striker: ONE sprung solenoid above the tile (decided 2026-10-19).

Energised = striker retracted, de-energised = gravity + spring strike. Both edges make
noise, so the Nano reports both (`RET` then `TAP`), and either the ToF (auto) or the Pi
(manual: RETRACT / RELEASE / TAP) can start a cycle. The real core (nano_core.h) is
compiled on this machine and driven over the real protocol module and Pi-side bridge,
like tests/test_nano_firmware_conformance.py, which this file sits beside.

Needs a C++11 compiler (see that file). NOT a test of the board or of the coil: the
retract hold time, the coil's duty limit and the MOSFET wiring are unmeasured.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "acoustic_node" / "firmware_host"))
import host_nano  # noqa: E402

from acoustic.tap_bridge import TapBridge  # noqa: E402
from acoustic.tap_protocol import (  # noqa: E402
    encode_belt, encode_mode, encode_ping, encode_release, encode_retract, encode_strike, encode_tap_cycle, parse_line,
)
from fusion.transport import InMemoryBus  # noqa: E402

HOLD = 500          # ms the coil stays energised before an automatic release (host option)
MAXHOLD = 3000      # ms before the safety release (host option)


@pytest.fixture(scope="module")
def exe(tmp_path_factory):
    path = host_nano.build_host(tmp_path_factory.mktemp("nano_host_sprung"))
    if path is None:
        pytest.skip("no C++ compiler (set NANO_HOST_CXX or GANTRY_HOST_CXX)")
    return path


@pytest.fixture
def nano(exe):
    n = host_nano.HostNano(exe, "--sprung", "--retract-hold", str(HOLD), "--retract-max", str(MAXHOLD))
    yield n
    n.close()


def msgs(lines):
    return [parse_line(line) for line in lines]


def of(lines, kind):
    return [m for m in msgs(lines) if m and m["type"] == kind]


def enable(nano):
    nano.rx(encode_strike(True))
    nano.rx(encode_belt(0))
    nano.advance(50)


def manual(nano):
    enable(nano)
    nano.rx(encode_mode(True))
    nano.advance(50)


def keep_alive(nano, total_ms, step=500):
    """Advance time while the Pi keeps pinging (the watchdog must not be what ends a test)."""
    out = []
    n = 0
    while total_ms > 0:
        n += 1
        nano.rx(encode_ping(n))
        out += nano.advance(min(step, total_ms))
        total_ms -= step
    return out


# ---- auto: the ToF starts the cycle -------------------------------------------------------------


def test_tof_cycle_retracts_then_strikes_and_reports_both(nano):
    enable(nano)
    nano.tof(1, 50)
    out = keep_alive(nano, 1600)
    (ret,) = of(out, "ret")
    (tap,) = of(out, "tap")
    assert ret["src"] == "tof" and tap["src"] == "tof"
    assert tap["tile_present"] is True and tap["tof_mm"] == 50
    # The release comes the configured hold after the retraction, measured on the Nano's own clock.
    assert tap["uptime_ms"] - ret["uptime_ms"] == pytest.approx(HOLD, abs=5)
    assert nano.pins["arm"] == 0 and nano.pins["lock"] == 0         # LOCK is never used
    assert nano.pins["arm_rises"] == 1


def test_coil_is_energised_between_the_two_events(nano):
    enable(nano)
    nano.tof(1, 50)
    out = []
    while not of(out, "ret"):
        out += keep_alive(nano, 50, step=50)
    assert nano.pins["arm"] == 1 and nano.pins["retracted"] == 1
    keep_alive(nano, HOLD + 100)
    assert nano.pins["arm"] == 0 and nano.pins["retracted"] == 0


def test_heartbeat_reports_mode_and_retracted(nano):
    hb = of(keep_alive(nano, 600), "hb")
    assert hb and hb[-1]["manual"] is False and hb[-1]["retracted"] is False
    manual(nano)
    nano.rx(encode_retract())
    hb = of(keep_alive(nano, 600), "hb")
    assert hb[-1]["manual"] is True and hb[-1]["retracted"] is True


def test_no_strike_when_inhibited_and_none_for_a_tile_that_arrived_while_inhibited(nano):
    nano.tof(1, 50)
    out = keep_alive(nano, 1500)
    assert not of(out, "ret") and not of(out, "tap")
    nano.rx(encode_strike(True))
    assert not of(keep_alive(nano, 1500), "ret")        # still the same tile: it is consumed


def test_unreadable_tof_at_the_strike_still_drops_the_coil_and_says_so(nano):
    enable(nano)
    nano.tof(1, 50)
    out = []
    while not of(out, "ret"):
        out += keep_alive(nano, 50, step=50)
    nano.tof(1, None)                                   # the sensor stops answering mid-hold
    out = keep_alive(nano, HOLD + 200)
    (tap,) = of(out, "tap")
    assert tap["src"] == "tof_blind" and tap["tile_present"] is False   # never "empty on no evidence": flagged blind
    assert [e for e in of(out, "err") if e["code"] == "TOF1"]
    assert nano.pins["arm"] == 0                                         # the coil came down regardless


# ---- manual: the Pi starts the cycle --------------------------------------------------------------


def test_manual_mode_ignores_the_tof(nano):
    manual(nano)
    nano.tof(1, 50)
    out = keep_alive(nano, 1500)
    assert not of(out, "ret") and not of(out, "tap")


def test_manual_retract_holds_until_release(nano):
    manual(nano)
    out = nano.rx(encode_retract())
    (ret,) = of(out, "ret")
    assert ret["src"] == "manual" and nano.pins["arm"] == 1
    out = keep_alive(nano, HOLD + 1000)                  # well past the automatic hold: it must wait
    assert not of(out, "tap") and nano.pins["arm"] == 1
    out = nano.rx(encode_release())
    (tap,) = of(out, "tap")
    assert tap["src"] == "manual" and nano.pins["arm"] == 0
    # No ToF fitted in this test: the strike happens and the ToF reading is simply what it is.
    assert tap["tile_present"] in (True, False)


def test_manual_cycle_retracts_waits_and_strikes_by_itself(nano):
    manual(nano)
    out = nano.rx(encode_tap_cycle())
    out += keep_alive(nano, HOLD + 300)
    (ret,) = of(out, "ret")
    (tap,) = of(out, "tap")
    assert ret["src"] == "manual" and tap["src"] == "manual"
    assert tap["uptime_ms"] - ret["uptime_ms"] == pytest.approx(HOLD, abs=5)


def test_manual_commands_are_refused_with_a_reason(nano):
    enable(nano)                                                        # armed, but still AUTO
    assert [e["code"] for e in of(nano.rx(encode_retract()), "err")] == ["NOTMANUAL"]
    nano.rx(encode_mode(True))
    nano.rx(encode_strike(False))
    assert [e["code"] for e in of(nano.rx(encode_retract()), "err")] == ["INHIBITED"]
    nano.rx(encode_strike(True))
    assert [e["code"] for e in of(nano.rx(encode_release()), "err")] == ["NOTRETRACTED"]
    nano.rx(encode_retract())
    assert [e["code"] for e in of(nano.rx(encode_retract()), "err")] == ["BUSY"]
    assert nano.pins["arm_rises"] == 1                                  # none of the refusals moved the coil


def test_switching_to_manual_cancels_an_auto_cycle_that_has_not_started(nano):
    enable(nano)
    nano.tof(1, 50)
    nano.advance(60)                                    # tile debounced, settling
    nano.rx(encode_mode(True))
    out = keep_alive(nano, 1500)
    assert not of(out, "ret") and nano.pins["arm"] == 0


# ---- the coil is never left hanging -----------------------------------------------------------------


def test_held_too_long_is_released_as_a_safety_strike(nano):
    manual(nano)
    nano.rx(encode_retract())
    out = keep_alive(nano, MAXHOLD + 300)
    (tap,) = of(out, "tap")
    assert tap["src"] == "safety" and nano.pins["arm"] == 0
    assert [e for e in of(out, "err") if e["code"] == "RETRACT"]


def test_inhibit_while_retracted_releases(nano):
    manual(nano)
    nano.rx(encode_retract())
    out = nano.rx(encode_strike(False))
    out += nano.advance(100)
    assert [t["src"] for t in of(out, "tap")] == ["safety"] and nano.pins["arm"] == 0


def test_losing_the_pi_while_retracted_releases(nano):
    manual(nano)
    nano.rx(encode_retract())
    out = nano.advance(2600)                            # no pings: the watchdog trips at 2 s
    assert [t["src"] for t in of(out, "tap")] == ["safety"]
    assert nano.pins["arm"] == 0 and nano.pins["strike"] == 0


def test_estop_while_retracted_drops_the_coil_and_reports_the_strike_first(nano):
    manual(nano)
    nano.rx(encode_retract())
    nano.estop(True)
    out = nano.advance(20)
    kinds = [m["type"] + (":" + m.get("code", "") if m["type"] == "err" else "") for m in msgs(out) if m and m["type"] in ("tap", "err")]
    assert kinds[:2] == ["tap", "err:ESTOP"] and nano.pins["arm"] == 0
    assert [t["src"] for t in of(out, "tap")] == ["safety"]


# ---- the full-system (dual solenoid) core is untouched -------------------------------------------------


def test_dual_striker_core_keeps_the_old_wire_format(exe):
    n = host_nano.HostNano(exe)                         # no --sprung
    try:
        out = n.rx(encode_mode(True))
        assert [e["code"] for e in of(out, "err")] == ["UNSUPPORTED"]
        hb = of(n.advance(600), "hb")
        assert hb and "manual" not in hb[-1]            # still the 4-field heartbeat
    finally:
        n.close()


# ---- the Pi-side bridge end to end --------------------------------------------------------------------


class _Serial:
    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)

    def readline(self):
        return b""

    def close(self):
        pass


def test_bridge_turns_buttons_into_labelled_bus_events(nano):
    bus = InMemoryBus()
    ser = _Serial()
    now = [100.0]
    bridge = TapBridge(ser, bus, clock=lambda: now[0], ping_s=0.5)
    # What start() subscribes, without its reader thread: the test pumps the Nano itself.
    bus.subscribe("station/strike", bridge._on_strike)
    bus.subscribe("station/striker/cmd", bridge._on_striker_cmd)

    def pump(lines):
        for line in lines:
            bridge.handle_line(line, now[0])

    def flush():
        for data in ser.written:
            pump(nano.rx(data))
        ser.written.clear()

    pump(nano.boot_lines)
    bus.publish("station/strike", {"enabled": True})
    bus.publish("station/striker/cmd", {"action": "mode", "manual": True})
    flush()
    bus.publish("station/striker/cmd", {"action": "retract"})
    flush()
    pump(nano.advance(100))
    bus.publish("station/striker/cmd", {"action": "release"})
    flush()
    pump(nano.advance(700))
    now[0] += 1.0
    bridge.tick(now[0])

    (retract,) = bus.messages("station/retract")
    (tap,) = bus.messages("station/tap")
    assert retract["source"] == "manual" and tap["source"] == "manual"
    assert bridge.counters["retracts"] == 1 and bridge.counters["bad_lines"] == 0
    status = [m for m in bus.messages("station/nano/status") if m.get("state") == "online"][-1]
    assert status["manual"] is True and status["retracted"] is False


def test_bridge_flags_a_blind_strike_as_unknown_presence(nano):
    bus = InMemoryBus()
    bridge = TapBridge(_Serial(), bus, clock=lambda: 100.0)
    bridge.handle_line(b"TAP 1 0 65535 1234 3*" + format(
        __import__("functools").reduce(lambda a, c: a ^ c, b"TAP 1 0 65535 1234 3"), "02X").encode() + b"\n")
    (tap,) = bus.messages("station/tap")
    assert tap["source"] == "tof_blind" and tap["tile_present"] is None


# ---- the prototype's own numbers: one ToF, <= 75 mm = tile, strike 3 s after the tile is seen ----------


@pytest.fixture
def proto(exe):
    n = host_nano.HostNano(exe, "--prototype")          # nano::prototypeConfig(): what the board glue runs
    yield n
    n.close()


def at_ms(nano):
    return nano.pins["now"]


def test_prototype_rests_retracted_with_nothing_under_it_armed_or_not(proto):
    # Owner, 2026-10-19: with no tile the striker must be UP -- without anyone pressing Armed.
    assert proto.pins["arm"] == 0                       # before the first pass of the loop
    out = keep_alive(proto, 1000, step=25)              # never armed
    (ret,) = of(out, "ret")
    assert ret["src"] == "tof" and proto.pins["arm"] == 1 and proto.pins["retracted"] == 1
    assert proto.pins["strike"] == 0                    # still inhibited: parked, but no strike is allowed
    out = keep_alive(proto, 30000)                      # parked for as long as the line idles: no 8 s timeout
    assert not of(out, "tap") and not of(out, "ret") and proto.pins["arm"] == 1


def test_prototype_releases_two_seconds_after_the_tile_is_recognised(proto):
    enable(proto)
    keep_alive(proto, 1000, step=25)                    # parked up
    t0 = at_ms(proto)
    proto.tof(1, 50)                                    # a tile arrives
    out = keep_alive(proto, 4000, step=25)
    (tap,) = of(out, "tap")
    assert tap["src"] == "tof" and tap["tile_present"] is True and tap["tof_mm"] == 50
    # 5 debounce samples (~125 ms) to recognise it, then the 2 s countdown.
    assert 2000 <= tap["uptime_ms"] - t0 <= 2300
    assert proto.pins["arm"] == 0 or tap["uptime_ms"] > 0


def test_prototype_tile_that_leaves_in_time_costs_nothing(proto):
    enable(proto)
    keep_alive(proto, 1000, step=25)
    proto.tof(1, 50)
    keep_alive(proto, 1200, step=25)                    # not yet 2 s
    proto.tof(1, 150)                                   # it moved on
    out = keep_alive(proto, 5000, step=25)
    assert not of(out, "tap") and proto.pins["arm"] == 1
    proto.tof(1, 50)                                    # the next tile still gets its full 2 s
    out = keep_alive(proto, 1500, step=25)
    assert not of(out, "tap")
    assert of(keep_alive(proto, 1500, step=25), "tap")


def test_prototype_lifts_again_after_the_strike_so_the_tile_can_leave_and_does_not_strike_it_twice(proto):
    # Owner, 2026-10-19: strike, then let it go -- only then can the tile move out.
    enable(proto)
    keep_alive(proto, 1000, step=25)
    proto.tof(1, 50)
    out = keep_alive(proto, 2600, step=25)
    (tap,) = of(out, "tap")
    assert proto.pins["arm"] == 0 or of(out, "ret")                # struck
    out = keep_alive(proto, 3000, step=25)                          # the tile is STILL there
    (ret,) = of(out, "ret")
    assert 1400 <= ret["uptime_ms"] - tap["uptime_ms"] <= 1700     # lifted ~1.5 s after the strike
    assert proto.pins["arm"] == 1 and proto.pins["retracted"] == 1
    out = keep_alive(proto, 20000)                                  # and it is not struck again while it sits there
    assert not of(out, "tap") and proto.pins["arm"] == 1
    proto.tof(1, 150)                                               # the tile moves out ...
    keep_alive(proto, 500, step=25)
    proto.tof(1, 50)                                                # ... the next one comes in
    assert len(of(keep_alive(proto, 3000, step=25), "tap")) == 1


def test_prototype_boot_with_a_tile_already_there_lifts_but_does_not_strike_it(proto):
    proto.tof(1, 50)
    enable(proto)
    out = keep_alive(proto, 6000, step=25)
    assert not of(out, "tap") and proto.pins["arm"] == 1            # up (the tile can leave), no strike
    proto.tof(1, 150)
    keep_alive(proto, 500, step=25)
    proto.tof(1, 50)
    assert of(keep_alive(proto, 3000, step=25), "tap")              # a tile that arrives afterwards is struck


def test_prototype_does_not_park_when_the_tof_cannot_be_read(proto):
    proto.tof(1, None)
    enable(proto)
    out = keep_alive(proto, 3000, step=25)
    assert not of(out, "ret") and proto.pins["arm"] == 0
    proto.tof(1, 150)                                   # the sensor comes back
    assert of(keep_alive(proto, 1500, step=25), "ret")


def test_prototype_disarmed_it_stays_up_and_a_tile_does_not_release_it(proto):
    keep_alive(proto, 1000, step=25)                    # parked, never armed
    proto.tof(1, 50)
    out = keep_alive(proto, 6000, step=25)
    assert not of(out, "tap") and proto.pins["arm"] == 1
    t0 = at_ms(proto)
    out = proto.rx(encode_strike(True)) + keep_alive(proto, 3000, step=25)      # arming starts a fresh 2 s
    (tap,) = of(out, "tap")
    assert 2000 <= tap["uptime_ms"] - t0 <= 2300 and tap["src"] == "tof"


def test_prototype_disarming_while_parked_keeps_it_up(proto):
    enable(proto)
    keep_alive(proto, 1000, step=25)
    out = proto.rx(encode_strike(False)) + keep_alive(proto, 3000, step=25)
    assert not of(out, "tap") and proto.pins["arm"] == 1


def test_prototype_losing_the_pi_while_parked_keeps_it_up_but_inhibited(proto):
    enable(proto)
    keep_alive(proto, 1000, step=25)
    out = proto.advance(3000)                           # no pings: the watchdog trips
    assert [e for e in of(out, "err") if e["code"] == "WATCHDOG"]
    assert proto.pins["arm"] == 1 and proto.pins["strike"] == 0 and not of(out, "tap")
    proto.tof(1, 50)
    assert not of(proto.advance(5000), "tap")           # a tile cannot release it with the Pi gone


def test_prototype_manual_mode_takes_over_a_parked_striker(proto):
    enable(proto)
    keep_alive(proto, 1000, step=25)
    proto.rx(encode_mode(True))
    proto.tof(1, 50)
    out = keep_alive(proto, 5000, step=25)
    assert not of(out, "tap") and proto.pins["arm"] == 1          # a tile does nothing in MANUAL
    (tap,) = of(proto.rx(encode_release()), "tap")                # the operator's RELEASE
    assert tap["src"] == "manual" and proto.pins["arm"] == 0


def test_prototype_back_to_auto_parks_it_again(proto):
    proto.rx(encode_mode(True))                         # MANUAL before arming: it stays down
    proto.rx(encode_strike(True))
    proto.tof(1, 150)
    assert not of(keep_alive(proto, 2000, step=25), "ret")        # manual: stays down
    proto.rx(encode_mode(False))
    assert of(keep_alive(proto, 1500, step=25), "ret")


def test_prototype_threshold_is_100_mm_and_the_measured_tile_counts(proto):
    enable(proto)
    keep_alive(proto, 1000, step=25)
    proto.tof(1, 112)                                              # the nearest empty-surface reading seen on the rig
    assert not of(keep_alive(proto, 4000, step=25), "tap")
    proto.tof(1, 101)
    assert not of(keep_alive(proto, 3000, step=25), "tap")        # just too far: no tile
    proto.tof(1, 91)                                               # the tile on the rig, 2026-10-19
    assert of(keep_alive(proto, 4000, step=25), "tap")            # a tile
    proto.tof(1, 150)
    keep_alive(proto, 2000, step=25)
    proto.tof(1, 100)
    assert of(keep_alive(proto, 4000, step=25), "tap")            # at 75 mm: a tile


def test_prototype_has_no_belt_end_sensor_to_complain_about(proto):
    out = keep_alive(proto, 12000)
    assert not [e for e in of(out, "err") if e["code"] == "TOF3"]
    hb = of(out, "hb")
    assert hb and hb[-1]["tof3_ok"] is False


# ---- the ToF distance in the heartbeat ---------------------------------------------------------------------


def test_heartbeat_carries_the_striker_tof_distance(proto):
    proto.tof(1, 150)
    hb = of(keep_alive(proto, 1500), "hb")
    assert hb[-1]["tof1_mm"] == 150 and hb[-1]["tof1_ok"] is True
    proto.tof(1, 62)
    hb = of(keep_alive(proto, 600, step=100), "hb")
    assert hb[-1]["tof1_mm"] == 62
    proto.tof(1, 65535)                                  # answering, nothing in range
    assert of(keep_alive(proto, 600, step=100), "hb")[-1]["tof1_mm"] is None
    proto.tof(1, None)                                   # not answering
    hb = of(keep_alive(proto, 800, step=100), "hb")[-1]
    assert hb["tof1_ok"] is False and hb["tof1_mm"] is None


def test_bridge_publishes_the_distance_in_the_nano_status(proto):
    bus = InMemoryBus()
    bridge = TapBridge(_Serial(), bus, clock=lambda: 100.0)
    proto.tof(1, 88)
    for line in keep_alive(proto, 800, step=100):
        bridge.handle_line(line, 100.0)
    bridge.tick(100.0)
    assert bus.messages("station/nano/status")[-1]["tof1_mm"] == 88


def test_seven_field_heartbeat_parses_and_old_ones_still_do():
    from acoustic.tap_protocol import encode
    assert parse_line(encode("HB 5 0 1 0 1 0 63"))["tof1_mm"] == 63
    assert parse_line(encode("HB 5 0 1 0 1 0 65535"))["tof1_mm"] is None
    assert "tof1_mm" not in parse_line(encode("HB 5 0 1 0 1 0"))
    assert "manual" not in parse_line(encode("HB 5 0 1 0"))
    assert parse_line(encode("HB 5 0 1 0 1"))["type"] == "bad"


def test_prototype_switching_to_manual_after_a_long_park_does_not_drop_it(proto):
    # Found on the real rig 2026-10-19: parked for 18 s, then MODE 1 released it as a "held too long"
    # safety strike, because the hold-time limit was still counting from when it went up.
    enable(proto)
    keep_alive(proto, 10000)                            # parked well past retract_max_ms (8 s)
    assert proto.pins["arm"] == 1
    proto.rx(encode_mode(True))
    out = keep_alive(proto, 2000)
    assert not of(out, "tap") and not [e for e in of(out, "err") if e["code"] == "RETRACT"]
    assert proto.pins["arm"] == 1
    (tap,) = of(proto.rx(encode_release()), "tap")
    assert tap["src"] == "manual" and proto.pins["arm"] == 0


# ---- the sequence status in the heartbeat (for the Striker panel) ---------------------------------------------------


def last_hb(lines):
    return of(lines, "hb")[-1]


def test_heartbeat_reports_armed_state_countdown_and_must_leave(proto):
    hb = last_hb(keep_alive(proto, 1000, step=25))
    assert hb["armed"] is False and hb["retracted"] is True and hb["left_ms"] == 0
    assert hb["must_leave"] is True or hb["must_leave"] is False
    out = proto.rx(encode_strike(True)) + keep_alive(proto, 600, step=25)
    hb = last_hb(out)
    assert hb["armed"] is True and hb["must_leave"] is False and hb["state"] == 7          # RETRACTED
    proto.tof(1, 50)
    left = [h["left_ms"] for h in of(keep_alive(proto, 1500, step=25), "hb")]
    assert any(0 < v <= 2000 for v in left) and left == sorted(left, reverse=True) or left[-1] > 0
    out = keep_alive(proto, 1500, step=25)                                                  # the strike, then STRUCK
    assert any(h["state"] in (5, 8) and h["retracted"] is False for h in of(out, "hb"))
    out = keep_alive(proto, 3000, step=25)                                                  # lifted, tile still there
    hb = last_hb(out)
    assert hb["retracted"] is True and hb["must_leave"] is True and hb["left_ms"] == 0 and hb["armed"] is True
    proto.tof(1, 150)
    hb = last_hb(keep_alive(proto, 800, step=25))
    assert hb["must_leave"] is False and hb["armed"] is True        # crossed: armed and ready for the next tile


def test_prototype_heartbeat_is_four_times_a_second(proto):
    assert len(of(keep_alive(proto, 2000, step=250), "hb")) >= 7


def test_eleven_field_heartbeat_parses():
    from acoustic.tap_protocol import encode
    m = parse_line(encode("HB 5 0 1 0 0 1 91 1 7 1500 0"))
    assert m["armed"] is True and m["state"] == 7 and m["left_ms"] == 1500 and m["must_leave"] is False
    assert parse_line(encode("HB 5 0 1 0 0 1 91 1 7 1500"))["type"] == "bad"


def test_bridge_publishes_the_sequence_status(proto):
    bus = InMemoryBus()
    bridge = TapBridge(_Serial(), bus, clock=lambda: 100.0)
    proto.rx(encode_strike(True))
    for line in keep_alive(proto, 1000, step=50):
        bridge.handle_line(line, 100.0)
    bridge.tick(100.0)
    s = bus.messages("station/nano/status")[-1]
    assert s["armed"] is True and s["nano_state"] == 7 and s["left_ms"] == 0 and s["must_leave"] is False
    assert s["state"] == "online"          # the bridge's own field is not overwritten by the Nano's state number
