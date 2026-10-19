"""The Arduino Nano <-> Pi serial protocol: pure encode / parse, no I/O.

ASCII, one message per line, 115200 8N1. Every line carries an XOR checksum so
a byte corrupted on a long USB run or by motor noise is *rejected*, never
acted on — a corrupted `BELT 0` that became `BELT 90` is exactly the failure
this prevents.

    <payload>*<HH>\\n          HH = two hex digits, XOR of every payload byte

Nano -> Pi
    HELLO <fw>                                  once at boot
    HB <uptime_ms> <belt_pct> <tof1_ok> <tof3_ok> [<manual> <retracted> <tof1_mm> <armed> <state> <left_ms> <must_leave> <hold_pct> <up_wanted>]
                        every 500 ms (250 ms for the prototype striker)
    TAP <seq> <tile_present> <tof_mm> <uptime_ms> [<src>]   the instant LOCK opens
                        (single sprung striker: the instant the coil is RELEASED)
    RET <seq> <src> <uptime_ms>                 single sprung striker: the coil was
                        just ENERGISED (striker retracted) - a sound of its own
    PONG <n>                                    answer to PING
    ERR <code> <text...>

Pi -> Nano
    PING <n>            keep-alive, every 500 ms. THE NANO MUST STOP THE BELT
                        if no valid line arrives from the Pi for 2000 ms
                        (watchdog), so a dead bridge/Pi/cable cannot leave the
                        belt running.
    BELT <pct>          belt speed 0-100
    STRIKE <0|1>        allow / inhibit the striker
    MODE <0|1>          single sprung striker: 0 = the ToF starts cycles, 1 = manual
    RETRACT             manual mode: energise the coil and hold it
    RELEASE             manual mode: de-energise it (the strike)
    UP                  request-only striker: a tile is coming (the camera saw it) -> raise now
    HOLD <0-100>        coil duty % while the striker is held up (heat vs holding force; too low drops it)
    TAP                 manual mode: RETRACT, wait the hold time, RELEASE

`src` says why: 0 ToF, 1 manual, 2 safety release (timeout / inhibit / e-stop),
3 ToF-started but the ToF could not be read at the strike.

`seq` is a 16-bit counter that wraps; the bridge uses it to count lost TAP
lines. `tile_present` is the striker-station ToF reading at the strike.
Full description: documents/programming/nano_serial_protocol.md
"""

from __future__ import annotations

from typing import Optional


def checksum(payload: str) -> int:
    value = 0
    for byte in payload.encode("ascii", errors="replace"):
        value ^= byte
    return value


def encode(payload: str) -> bytes:
    """A line ready to write: payload, checksum, newline."""
    if "\n" in payload or "*" in payload:
        raise ValueError("payload must not contain newline or '*'")
    return f"{payload}*{checksum(payload):02X}\n".encode("ascii")


def encode_belt(percent: float) -> bytes:
    return encode(f"BELT {max(0, min(100, int(round(percent))))}")


def encode_ping(n: int) -> bytes:
    return encode(f"PING {int(n) % 65536}")


def encode_strike(enabled: bool) -> bytes:
    return encode(f"STRIKE {1 if enabled else 0}")


SRC_TOF, SRC_MANUAL, SRC_SAFETY, SRC_TOF_BLIND, SRC_REST = 0, 1, 2, 3, 4
SRC_NAMES = {SRC_TOF: "tof", SRC_MANUAL: "manual", SRC_SAFETY: "safety", SRC_TOF_BLIND: "tof_blind", SRC_REST: "rest"}


def encode_mode(manual: bool) -> bytes:
    return encode(f"MODE {1 if manual else 0}")


def encode_up() -> bytes:
    """A tile is coming (the camera saw it): raise the striker now. Request-only striker."""
    return encode("UP")


def encode_hold(percent: float) -> bytes:
    return encode(f"HOLD {max(0, min(100, int(round(percent))))}")


def encode_retract() -> bytes:
    return encode("RETRACT")


def encode_release() -> bytes:
    return encode("RELEASE")


def encode_tap_cycle() -> bytes:
    return encode("TAP")


def _ints(parts: list[str], n: int) -> Optional[list[int]]:
    if len(parts) != n:
        return None
    try:
        return [int(p) for p in parts]
    except ValueError:
        return None


def parse_line(raw) -> Optional[dict]:
    """One received line -> a message dict, or None for a blank line.

    A line that is damaged or not understood comes back as
    {"type": "bad", "reason": ..., "raw": ...} so the bridge can count it —
    never an exception, never silently accepted."""
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", errors="replace")
    line = raw.strip()
    if not line:
        return None
    bad = lambda reason: {"type": "bad", "reason": reason, "raw": line[:80]}  # noqa: E731

    payload, star, tail = line.rpartition("*")
    if not star:
        return bad("no checksum")
    try:
        given = int(tail, 16)
    except ValueError:
        return bad("checksum is not hex")
    if len(tail) != 2 or given != checksum(payload):
        return bad("checksum mismatch")

    parts = payload.split()
    if not parts:
        return bad("empty payload")
    kind, args = parts[0], parts[1:]

    if kind == "HELLO":
        return {"type": "hello", "fw": " ".join(args) or "?"}
    if kind == "HB":
        v = _ints(args, len(args)) if len(args) in (4, 6, 7, 11, 12, 13) else None
        if v is None:
            return bad("HB needs 4 (or 6, 7, 11, 12 or 13) integers")
        msg = {"type": "hb", "uptime_ms": v[0], "belt_pct": v[1], "tof1_ok": bool(v[2]), "tof3_ok": bool(v[3])}
        if len(v) >= 6:  # the single sprung striker also reports its mode and whether it is held up
            msg.update(manual=bool(v[4]), retracted=bool(v[5]))
        if len(v) >= 7:  # ... and the striker ToF's latest distance (65535 = no reading / nothing in range)
            msg["tof1_mm"] = None if v[6] == 65535 else v[6]
        if len(v) >= 11:  # ... and the sequence: armed (read back from the Nano), nano::State, ms to the strike, tile-must-leave
            msg.update(armed=bool(v[7]), state=v[8], left_ms=v[9], must_leave=bool(v[10]))
        if len(v) >= 12:
            msg["hold_pct"] = v[11]
        if len(v) == 13:
            msg["up_wanted"] = bool(v[12])
        return msg
    if kind == "TAP":
        v = _ints(args, 5) if len(args) == 5 else _ints(args, 4)
        if v is None or v[1] not in (0, 1) or (len(v) == 5 and v[4] not in SRC_NAMES):
            return bad("TAP needs seq, 0|1, tof_mm, uptime_ms [, src 0-3]")
        msg = {"type": "tap", "seq": v[0], "tile_present": bool(v[1]), "tof_mm": v[2], "uptime_ms": v[3]}
        if len(v) == 5:                      # only the single sprung striker says why it struck
            msg["src"] = SRC_NAMES[v[4]]
        return msg
    if kind == "RET":
        v = _ints(args, 3)
        if v is None or v[1] not in SRC_NAMES:
            return bad("RET needs seq, src 0-3, uptime_ms")
        return {"type": "ret", "seq": v[0], "src": SRC_NAMES[v[1]], "uptime_ms": v[2]}
    if kind == "PONG":
        v = _ints(args, 1)
        return {"type": "pong", "n": v[0]} if v else bad("PONG needs 1 integer")
    if kind == "ERR":
        return {"type": "err", "code": args[0] if args else "?", "text": " ".join(args[1:])}
    return bad(f"unknown message {kind!r}")
