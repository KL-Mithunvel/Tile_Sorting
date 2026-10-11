"""Gantry line protocol: synthetic strings only, no serial port."""

import pytest

from pick_place import protocol as p
from pick_place.protocol import (Alarm, Done, Err, Heartbeat, Log, Ok, Pos, SeqCounter, State, Unknown,
                                 parse_line)


# ---- encoding -----------------------------------------------------------------


def test_move_matches_the_protocol_doc_example():
    assert p.move(z=200, f=4000).encode(101) == "101 MOVE Z200.0 F4000.0"
    assert p.move(x=500, y=100, f=8000).encode(102) == "102 MOVE X500.0 Y100.0 F8000.0"


def test_numbers_are_trimmed_and_never_negative_zero():
    assert p.fmt_mm(12.5) == "12.5"
    assert p.fmt_mm(0.125) == "0.125"
    assert p.fmt_mm(-0.0001) == "0.0"
    assert p.fmt_mm(82.55) == "82.55"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_never_reach_the_wire(bad):
    with pytest.raises(ValueError):
        p.move(x=bad)
    with pytest.raises(ValueError):
        p.fmt_mm(bad)


def test_move_needs_an_axis_and_a_positive_feed():
    with pytest.raises(ValueError):
        p.move()
    with pytest.raises(ValueError):
        p.move(x=1, f=0)
    with pytest.raises(ValueError):
        p.move(x=1, f=-5)


def test_every_command_encodes():
    assert p.ping().encode(1) == "1 PING"
    assert p.home().encode(2) == "2 HOME ALL"
    assert p.home("z").encode(3) == "3 HOME Z"
    assert p.jog("x", 10, 600).encode(4) == "4 JOG X+10.0 F600.0"
    assert p.jog("Y", -2.5).encode(5) == "5 JOG Y-2.5"
    assert p.vac(True).encode(6) == "6 VAC ON"
    assert p.vac(False).encode(7) == "7 VAC OFF"
    assert p.stop().encode(8) == "8 STOP"
    assert p.get_pos().encode(9) == "9 GETPOS"
    assert p.get_state().encode(10) == "10 GETSTATE"
    assert p.clear_alarm().encode(11) == "11 CLEARALARM"
    assert p.set_env((0, 600), (0, 400), (0, 200)).encode(12) == \
        "12 SETENV X0.0 X600.0 Y0.0 Y400.0 Z0.0 Z200.0"
    assert p.set_speed("x", 8000, 400).encode(13) == "13 SETSPEED X MAX8000.0 ACC400.0"
    assert p.set_tolerance(1.0, 0.5).encode(14) == "14 SETTOL LOST1.0 SKEW0.5"


def test_bad_arguments_are_refused():
    for call in (lambda: p.home("W"), lambda: p.jog("A", 1), lambda: p.set_speed("Q", 1, 1),
                 lambda: p.set_env((10, 10), (0, 1), (0, 1)), lambda: p.set_tolerance(0, 1)):
        with pytest.raises(ValueError):
            call()
    with pytest.raises(ValueError):
        p.ping().encode(-1)


def test_only_home_move_jog_are_motion():
    motion = [p.home(), p.move(x=1), p.jog("X", 1)]
    other = [p.ping(), p.vac(True), p.stop(), p.get_pos(), p.clear_alarm()]
    assert all(c.is_motion for c in motion) and not any(c.is_motion for c in other)


def test_seq_counter_is_monotonic():
    s = SeqCounter()
    assert [s.next(), s.next(), s.next()] == [1, 2, 3]


# ---- parsing ------------------------------------------------------------------


def test_replies():
    assert parse_line("ok 101") == Ok(101)
    assert parse_line("err 104 VACUUM_TIMEOUT") == Err(104, "VACUUM_TIMEOUT")
    assert parse_line("done 109 X100.0 Y350.0 Z200.0") == Done(109, 100.0, 350.0, 200.0)
    assert parse_line("hb 12345") == Heartbeat(12345)
    assert parse_line("log homing X done") == Log("homing X done")
    assert parse_line("state IDLE VAC=0 HOMED=1") == State("IDLE", False, True)
    assert parse_line("state PICKING VAC=1 HOMED=1") == State("PICKING", True, True)


def test_crlf_and_padding_are_tolerated():
    assert parse_line("  ok 7 \r\n") == Ok(7)


def test_pos_without_and_with_encoders():
    assert parse_line("pos X1.5 Y2.0 Z3.25") == Pos(1.5, 2.0, 3.25, {})
    pos = parse_line("pos X10.0 Y20.0 Z5.0 EX10.4 EY20.0 EZ5.0 EA19.2")
    assert pos.enc == {"X": 10.4, "Y": 20.0, "Z": 5.0, "A": 19.2}
    dev = pos.deviation()
    assert dev["X"] == pytest.approx(0.4)
    assert dev["A"] == pytest.approx(-0.8)  # the clone motor is judged against commanded Y
    assert dev["Y"] == pytest.approx(0.0)


def test_pos_with_only_some_encoders_reports_only_those():
    pos = parse_line("pos X1 Y2 Z3 EX1.1")
    assert pos.deviation() == {"X": pytest.approx(0.1)}


def test_alarm_classification():
    a = parse_line("alarm LIMIT_X_MIN")
    assert (a.kind, a.axis, a.is_closed_loop_fault) == ("LIMIT", "X", False)
    a = parse_line("alarm LOST_STEPS_Z")
    assert (a.kind, a.axis, a.is_closed_loop_fault) == ("LOST_STEPS", "Z", True)
    a = parse_line("alarm SKEW_Y")
    assert (a.kind, a.axis, a.is_closed_loop_fault) == ("SKEW", "Y", True)
    assert parse_line("alarm ESTOP").kind == "ESTOP" and parse_line("alarm ESTOP").axis is None
    assert parse_line("alarm LINK_TIMEOUT").kind == "LINK_TIMEOUT"
    assert Alarm("SOMETHING_NEW").kind == "OTHER"


@pytest.mark.parametrize("junk", [
    "", "   ", "ok", "ok x", "err 5", "done 5 X1 Y2", "done 5 X1 Y2 Zabc", "pos X1 Y2",
    "pos X1 Y2 Znan", "state IDLE", "state IDLE VAC=2 HOMED=1", "alarm", "hb soon",
    "\x00\xff garbled", "MOVE X1",
])
def test_garbage_becomes_unknown_never_an_exception(junk):
    assert isinstance(parse_line(junk), Unknown)


def test_encode_then_parse_round_trip_for_the_reply_shapes():
    for line, event in (("ok 1", Ok(1)), ("done 2 X0.0 Y0.0 Z0.0", Done(2, 0.0, 0.0, 0.0))):
        assert parse_line(line) == event
