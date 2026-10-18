"""Pure parsing and pass/fail logic for the encoder_smoke_test bench sketch.

The sketch reports raw numbers; every pass/fail decision is made here so the
tolerance lives in config.yaml, not in firmware. No I/O -- synthetic-string
testable. Not wired into pytest.ini (bench_tests/ is throwaway bring-up code,
same convention as io_smoke_test) -- run this file directly
(`python enc_protocol.py`) for a quick self-check.

Status line (see sketch/sketch.ino, statusLine()):
  ENC ST=<0 idle|1 moving|2 done|3 stopped> AX=<axis|-1> SEQ=<n> MUX=<0|1> DRV=<0|1>
      A<n> raw angle 0..4095 (-1 = read failed)   P<n> position, 0.1 deg
      S<n> AS5600 status byte (-1 = failed)       E<n> failed reads, total
      F<n> out-leg of last move, 0.1 deg          N<n> net after return, 0.1 deg
      M<n> failed reads during last move          (n = 0..3)
      SC=<8 comma-separated mux-channel flags: bit0 0x36, bit1 0x29, -1 select failed> or SC=-
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

NUM_ENC = 4

ST_IDLE, ST_MOVING, ST_DONE, ST_STOPPED = 0, 1, 2, 3

# AS5600 STATUS register bits
MD, ML, MH = 0x20, 0x10, 0x08


@dataclass(frozen=True)
class EncStatus:
    state: int
    axis: int
    seq: int
    mux_ok: bool
    drivers_on: bool
    raw: tuple
    pos_deg: tuple
    mag: tuple
    errors: tuple
    fwd_deg: tuple
    net_deg: tuple
    move_fails: tuple
    scan: Optional[tuple]  # None = not scanned yet


def mag_state(status_byte: int) -> str:
    """Human label for an AS5600 STATUS byte."""
    if status_byte < 0:
        return "read error"
    if not status_byte & MD:
        return "no magnet"
    if status_byte & ML:
        return "too weak"
    if status_byte & MH:
        return "too strong"
    return "ok"


def parse_status_line(line: str) -> Optional[EncStatus]:
    """Parses one 'ENC ...' line. None for anything else (banner, blanks)."""
    line = line.strip()
    if not line.startswith("ENC "):
        return None
    fields = {}
    for token in line[4:].split():
        key, sep, value = token.partition("=")
        if not sep:
            return None
        fields[key] = value
    try:
        def per_channel(prefix: str, scale: float = 1.0) -> tuple:
            return tuple(int(fields[f"{prefix}{n}"]) * scale for n in range(NUM_ENC))

        scan_text = fields["SC"]
        scan = None if scan_text == "-" else tuple(int(v) for v in scan_text.split(","))
        return EncStatus(
            state=int(fields["ST"]),
            axis=int(fields["AX"]),
            seq=int(fields["SEQ"]),
            mux_ok=fields["MUX"] == "1",
            drivers_on=fields["DRV"] == "1",
            raw=tuple(int(x) for x in per_channel("A")),
            pos_deg=per_channel("P", 0.1),
            mag=tuple(int(x) for x in per_channel("S")),
            errors=tuple(int(x) for x in per_channel("E")),
            fwd_deg=per_channel("F", 0.1),
            net_deg=per_channel("N", 0.1),
            move_fails=tuple(int(x) for x in per_channel("M")),
            scan=scan,
        )
    except (KeyError, ValueError):
        return None


def evaluate_channel(fwd_deg: float, net_deg: float, move_fails: int, mag: int,
                     expected_deg: float, tolerance_deg: float,
                     min_motion_fraction: float = 0.1) -> dict:
    """Verdict for one encoder after a move of `expected_deg` out and back.

    direction is +1/-1 (sign of the out-leg: which way the encoder counts when
    the motor turns 'forward') or 0 if it did not move -- useful for knowing
    whether the encoder is mounted mirrored, not a failure by itself."""
    direction = 0 if fwd_deg == 0 else (1 if fwd_deg > 0 else -1)
    reason = ""
    if move_fails > 0:
        reason = f"{move_fails} failed I2C reads during the move"
    elif abs(fwd_deg) < expected_deg * min_motion_fraction:
        reason = "no encoder movement (motor moved but this encoder did not)"
    elif abs(abs(fwd_deg) - expected_deg) > tolerance_deg:
        reason = f"out-leg {abs(fwd_deg):.1f} deg, expected {expected_deg:.0f} +/- {tolerance_deg:.0f}"
    elif abs(net_deg) > tolerance_deg:
        reason = f"did not return: {net_deg:+.1f} deg off after the reverse leg"
    elif mag >= 0 and mag_state(mag) != "ok":
        reason = f"magnet: {mag_state(mag)}"
    return {
        "verdict": "FAIL" if reason else "PASS",
        "reason": reason,
        "fwd_deg": round(fwd_deg, 1),
        "net_deg": round(net_deg, 1),
        "direction": direction,
    }


def build_result(status: EncStatus, axes: list, channel_names: list,
                 expected_deg: float, tolerance_deg: float) -> Optional[dict]:
    """One completed move -> a result record. `axes` is config.yaml's list;
    the sketch's AX index is the position in that list."""
    if status.axis < 0 or status.axis >= len(axes):
        return None
    axis_cfg = axes[status.axis]
    channels = []
    for ch in axis_cfg["channels"]:
        res = evaluate_channel(status.fwd_deg[ch], status.net_deg[ch],
                               status.move_fails[ch], status.mag[ch],
                               expected_deg, tolerance_deg)
        res["name"] = channel_names[ch]
        res["channel"] = ch
        channels.append(res)
    # Not-driven encoders must stay put; a different one moving points at
    # crossed mux channels (plan 5, test 2).
    stray = [channel_names[ch] for ch in range(NUM_ENC)
             if ch not in axis_cfg["channels"] and abs(status.fwd_deg[ch]) > tolerance_deg]
    overall = "PASS" if all(c["verdict"] == "PASS" for c in channels) and not stray else "FAIL"
    return {
        "time": time.strftime("%H:%M:%S"),
        "seq": status.seq,
        "axis": axis_cfg["name"],
        "overall": overall,
        "channels": channels,
        "stray": stray,
    }


if __name__ == "__main__":
    line = ("ENC ST=2 AX=1 SEQ=3 MUX=1 DRV=0 "
            "A0=100 P0=0 S0=32 E0=0 F0=0 N0=0 M0=0 "
            "A1=200 P1=3600 S1=32 E1=0 F1=3598 N1=12 M1=0 "
            "A2=300 P2=-3600 S2=32 E2=0 F2=-3610 N2=-5 M2=0 "
            "A3=400 P3=0 S3=16 E3=2 F3=0 N3=0 M3=0 SC=1,1,1,1,2,0,0,0")
    s = parse_status_line(line)
    assert s is not None and s.state == ST_DONE and s.raw == (100, 200, 300, 400)
    assert s.fwd_deg[1] == 359.8 and s.fwd_deg[2] == -361.0
    assert s.scan == (1, 1, 1, 1, 2, 0, 0, 0)
    assert parse_status_line("ENC garbage") is None and parse_status_line("hello") is None
    assert parse_status_line(line.replace("SC=1,1,1,1,2,0,0,0", "SC=-")).scan is None

    assert mag_state(0x20) == "ok" and mag_state(0x30) == "too weak"
    assert mag_state(0x28) == "too strong" and mag_state(0) == "no magnet" and mag_state(-1) == "read error"

    ok = evaluate_channel(359.8, 1.2, 0, 0x20, 360, 5)
    assert ok["verdict"] == "PASS" and ok["direction"] == 1
    assert evaluate_channel(-361.0, -0.5, 0, 0x20, 360, 5)["direction"] == -1
    assert "no encoder movement" in evaluate_channel(0.4, 0.1, 0, 0x20, 360, 5)["reason"]
    assert "expected" in evaluate_channel(300, 0, 0, 0x20, 360, 5)["reason"]
    assert "did not return" in evaluate_channel(360, 30, 0, 0x20, 360, 5)["reason"]
    assert "I2C" in evaluate_channel(360, 0, 3, 0x20, 360, 5)["reason"]
    assert "magnet" in evaluate_channel(360, 0, 0, 0x30, 360, 5)["reason"]

    axes = [{"name": "X", "channels": [0]}, {"name": "Y + A", "channels": [1, 2]}, {"name": "Z", "channels": [3]}]
    names = ["X", "Y", "Y'", "Z"]
    res = build_result(s, axes, names, 360, 5)
    assert res["overall"] == "PASS" and len(res["channels"]) == 2 and res["stray"] == []
    crossed = parse_status_line(line.replace("F0=0 ", "F0=900 "))
    assert build_result(crossed, axes, names, 360, 5)["stray"] == ["X"]
    print("enc_protocol self-check OK")
