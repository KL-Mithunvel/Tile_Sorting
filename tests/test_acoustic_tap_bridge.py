"""Nano serial protocol + bridge: corrupted lines are rejected, strike events
carry the tile-present flag, lost events are counted, the Nano is kept alive."""

import json

from acoustic.tap_bridge import TapBridge
from acoustic.tap_protocol import checksum, encode, encode_belt, encode_ping, encode_strike, parse_line
from fusion.transport import InMemoryBus


def line(payload: str) -> bytes:
    return encode(payload)


# ---- protocol ---------------------------------------------------------------


def test_encode_roundtrips_through_parse():
    assert encode_belt(55).decode() == f"BELT 55*{checksum('BELT 55'):02X}\n"
    assert parse_line(line("TAP 7 1 112 123456")) == {
        "type": "tap", "seq": 7, "tile_present": True, "tof_mm": 112, "uptime_ms": 123456}
    assert parse_line(line("HB 5000 60 1 0")) == {
        "type": "hb", "uptime_ms": 5000, "belt_pct": 60, "tof1_ok": True, "tof3_ok": False}
    assert parse_line(line("HELLO nano-1.0"))["fw"] == "nano-1.0"
    assert parse_line(line("PONG 3")) == {"type": "pong", "n": 3}
    assert parse_line(line("ERR TOF1 sensor timeout")) == {"type": "err", "code": "TOF1", "text": "sensor timeout"}


def test_belt_command_is_clamped():
    assert encode_belt(250).startswith(b"BELT 100*")
    assert encode_belt(-5).startswith(b"BELT 0*")
    assert encode_strike(True).startswith(b"STRIKE 1*") and encode_ping(70000).startswith(b"PING 4464*")


def test_corrupted_line_is_rejected_not_acted_on():
    good = line("BELT 0").decode()
    flipped = good.replace("BELT 0", "BELT 9")  # a corrupted byte, checksum now wrong
    assert parse_line(flipped)["type"] == "bad"
    assert parse_line("TAP 1 1 100 5")["type"] == "bad"  # no checksum at all
    assert parse_line("TAP 1 1 100 5*ZZ")["type"] == "bad"
    assert parse_line("") is None and parse_line("   \n") is None


def test_malformed_arguments_are_bad():
    assert parse_line(line("TAP 1 2 100 5"))["type"] == "bad"  # present must be 0|1
    assert parse_line(line("HB 1 2"))["type"] == "bad"
    assert parse_line(line("WAT 1"))["type"] == "bad"


# ---- bridge -----------------------------------------------------------------


class FakeSerial:
    def __init__(self):
        self.written = []
        self.closed = False

    def write(self, data):
        self.written.append(data)

    def readline(self):
        return b""

    def close(self):
        self.closed = True


def rig():
    bus, ser = InMemoryBus(), FakeSerial()
    return bus, ser, TapBridge(ser, bus, clock=lambda: 100.0, ping_s=0.5, status_s=1.0, stale_s=2.0)


def taps(bus):
    return bus.messages("station/tap")


def test_tap_line_becomes_a_station_tap_message_with_presence():
    bus, ser, b = rig()
    b.handle_line(line("TAP 1 1 118 4000"), now=100.0)
    b.handle_line(line("TAP 2 0 400 9000"), now=101.0)
    t = taps(bus)
    assert [x["tile_present"] for x in t] == [True, False] and t[0]["tof_mm"] == 118 and t[0]["seq"] == 1


def test_lost_tap_events_are_counted_and_alarmed():
    bus, ser, b = rig()
    b.handle_line(line("TAP 1 1 100 1"))
    b.handle_line(line("TAP 4 1 100 2"))  # 2 and 3 never arrived
    assert b.counters["lost_taps"] == 2
    assert any(a["kind"] == "tap_gap" for a in bus.messages("machine/alarm"))


def test_retransmitted_tap_is_one_strike():
    bus, ser, b = rig()
    b.handle_line(line("TAP 5 1 100 1"))
    b.handle_line(line("TAP 5 1 100 1"))
    assert len(taps(bus)) == 1 and b.counters["duplicates"] == 1


def test_nano_reboot_resets_the_sequence_without_a_false_gap():
    bus, ser, b = rig()
    b.handle_line(line("TAP 900 1 100 1"))
    b.handle_line(line("HELLO nano-1.0"))
    b.handle_line(line("TAP 0 1 100 1"))
    assert b.counters["lost_taps"] == 0 and len(taps(bus)) == 2


def test_sequence_wraps_at_16_bits():
    bus, ser, b = rig()
    b.handle_line(line("TAP 65535 1 100 1"))
    b.handle_line(line("TAP 0 1 100 2"))
    assert b.counters["lost_taps"] == 0


def test_bad_lines_are_counted_and_publish_nothing():
    bus, ser, b = rig()
    b.handle_line("TAP 1 1 100 1*00")
    assert b.counters["bad_lines"] == 1 and taps(bus) == []


def test_tick_pings_the_nano_and_publishes_retained_status():
    bus, ser, b = rig()
    b.handle_line(line("HB 500 60 1 1"), now=100.0)
    b.tick(now=100.6)
    assert ser.written and ser.written[0].startswith(b"PING 1*")
    status = json.loads([m for m in bus.published if m[0] == "station/nano/status"][-1][1])
    assert status["link"] is True and status["belt_pct"] == 60 and status["tof3_ok"] is True


def test_link_goes_false_when_the_nano_goes_quiet():
    bus, ser, b = rig()
    b.handle_line(line("HB 500 60 1 1"), now=100.0)
    b.tick(now=110.0)
    status = json.loads([m for m in bus.published if m[0] == "station/nano/status"][-1][1])
    assert status["link"] is False


def test_belt_and_strike_commands_are_written_as_checked_lines():
    bus, ser, b = rig()
    b.start()  # subscribes (the thread runs against FakeSerial, harmless)
    try:
        bus.publish("station/belt", {"percent": 35})
        bus.publish("station/strike", {"enabled": False})
        bus.publish("station/belt", b"{garbage")
    finally:
        b.stop()
    assert encode_belt(35) in ser.written and encode_strike(False) in ser.written
    assert any(a["kind"] == "bad_message" for a in bus.messages("machine/alarm"))
    assert ser.closed


def test_stop_does_not_send_belt_zero_it_just_goes_silent():
    bus, ser, b = rig()
    b.start()
    b.stop()
    assert not any(w.startswith(b"BELT") for w in ser.written)
