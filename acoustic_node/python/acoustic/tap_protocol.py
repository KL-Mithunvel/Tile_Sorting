"""The Arduino Nano <-> Pi serial protocol: pure encode / parse, no I/O.

ASCII, one message per line, 115200 8N1. Every line carries an XOR checksum so
a byte corrupted on a long USB run or by motor noise is *rejected*, never
acted on — a corrupted `BELT 0` that became `BELT 90` is exactly the failure
this prevents.

    <payload>*<HH>\\n          HH = two hex digits, XOR of every payload byte

Nano -> Pi
    HELLO <fw>                                  once at boot
    HB <uptime_ms> <belt_pct> <tof1_ok> <tof3_ok>   every 500 ms
    TAP <seq> <tile_present> <tof_mm> <uptime_ms>   the instant LOCK opens
    PONG <n>                                    answer to PING
    ERR <code> <text...>

Pi -> Nano
    PING <n>            keep-alive, every 500 ms. THE NANO MUST STOP THE BELT
                        if no valid line arrives from the Pi for 2000 ms
                        (watchdog), so a dead bridge/Pi/cable cannot leave the
                        belt running.
    BELT <pct>          belt speed 0-100
    STRIKE <0|1>        allow / inhibit the striker

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
        v = _ints(args, 4)
        if v is None:
            return bad("HB needs 4 integers")
        return {"type": "hb", "uptime_ms": v[0], "belt_pct": v[1], "tof1_ok": bool(v[2]), "tof3_ok": bool(v[3])}
    if kind == "TAP":
        v = _ints(args, 4)
        if v is None or v[1] not in (0, 1):
            return bad("TAP needs seq, 0|1, tof_mm, uptime_ms")
        return {"type": "tap", "seq": v[0], "tile_present": bool(v[1]), "tof_mm": v[2], "uptime_ms": v[3]}
    if kind == "PONG":
        v = _ints(args, 1)
        return {"type": "pong", "n": v[0]} if v else bad("PONG needs 1 integer")
    if kind == "ERR":
        return {"type": "err", "code": args[0] if args else "?", "text": " ".join(args[1:])}
    return bad(f"unknown message {kind!r}")
