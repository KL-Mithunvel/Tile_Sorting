"""Pure parsing/formatting for the io_smoke_test bench sketch's line protocol.

Deliberately a tiny bench-only subset of documents/programming/
pick_place_control_protocol.md §5 -- no seq numbers, no motion commands, just
enough to read the 4 sensor circuits and drive the vacuum output. See
sketch/sketch.ino's header comment for the exact line shapes parsed/emitted
here, including the known LOW/HIGH polarity discrepancy in the wiring doc
that this module deliberately does NOT resolve (it reports raw levels).

No I/O -- synthetic-string testable, same pattern as camera/line_trigger.py.
Not wired into pytest.ini's pythonpath (bench_tests/ is throwaway bring-up
code, same convention as stepper_smoke_test) -- run this file directly
(`python io_protocol.py`) for a quick self-check instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class IOStatus:
    """Raw digitalRead levels (True = HIGH, False = LOW) -- NOT pre-interpreted
    as triggered/ok. See sketch.ino's header re: the doc's polarity claim."""

    lim_x: bool
    lim_y: bool
    lim_z: bool
    estop: bool
    vac: bool


def parse_status_line(line: str) -> Optional[IOStatus]:
    """Parses one 'IO LIM_X=0 LIM_Y=0 LIM_Z=0 ESTOP=0 VAC=0' line from the
    sketch. Returns None for anything else (blank lines, the boot banner,
    'ok ...'/'err ...' acks) so callers can just skip what doesn't parse."""
    line = line.strip()
    if not line.startswith("IO "):
        return None

    fields = {}
    for token in line[3:].split():
        if "=" not in token:
            return None
        key, _, value = token.partition("=")
        fields[key] = value

    try:
        return IOStatus(
            lim_x=fields["LIM_X"] == "1",
            lim_y=fields["LIM_Y"] == "1",
            lim_z=fields["LIM_Z"] == "1",
            estop=fields["ESTOP"] == "1",
            vac=fields["VAC"] == "1",
        )
    except KeyError:
        return None


def format_vac_command(on: bool) -> str:
    """The line to send the sketch to switch the vacuum output."""
    return "VAC ON\n" if on else "VAC OFF\n"


if __name__ == "__main__":
    # Quick manual self-check -- see module docstring re: why this isn't in pytest.
    assert parse_status_line("IO LIM_X=0 LIM_Y=1 LIM_Z=0 ESTOP=1 VAC=0") == IOStatus(
        lim_x=False, lim_y=True, lim_z=False, estop=True, vac=False
    )
    assert parse_status_line("=== io smoke test ===") is None
    assert parse_status_line("ok VAC ON") is None
    assert parse_status_line("") is None
    assert format_vac_command(True) == "VAC ON\n"
    assert format_vac_command(False) == "VAC OFF\n"
    print("io_protocol self-check OK")
