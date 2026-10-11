"""One conversation with the gantry MCU: numbered commands out, parsed events in.

Pure given a line link (`write_line(str)`, `read_lines() -> list[str]`,
`connected`) and a clock — no threads, no serial port of its own. Both the HMI
backend (jog / vacuum / pose for the twin) and the job runner (plans) sit on ONE
session, so a reply is never consumed by the wrong reader: `pump()` drains the
link, updates the state below, and every command's outcome is looked up by
sequence number with `result()`.

Protocol: documents/programming/pick_place_control_protocol.md §5.

Outcome rules (what "finished" means) are the doc's:
    motion commands (HOME / MOVE / JOG)  -> `ok`, later `done`   (or `err`)
    everything else                      -> `ok`                  (or `err`)
`VAC ON` is answered only once the confirm sensor has seen the grip, so its `ok`
is the confirmation and `err VACUUM_TIMEOUT` is the miss.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Callable, Optional

from pick_place import protocol
from pick_place.protocol import (Alarm, Command, Done, Err, Event, Heartbeat, Log, Ok, Pos, State, Unknown)


@dataclass
class CommandResult:
    seq: int
    command: Command
    sent_ts: float
    status: str = "sent"          # sent -> ok -> done, or err
    reason: Optional[str] = None  # for err
    acked_ts: Optional[float] = None
    pos: Optional[tuple] = None   # final X, Y, Z from `done`

    @property
    def finished(self) -> bool:
        return self.status in ("done", "err") or (self.status == "ok" and not self.command.is_motion)

    @property
    def failed(self) -> bool:
        return self.status == "err"


class GantrySession:
    def __init__(self, link, clock: Callable[[], float] = time.monotonic, keep: int = 400, keep_logs: int = 50):
        self._link = link
        self._clock = clock
        self._lock = threading.RLock()
        self._seq = protocol.SeqCounter()
        self._results: "OrderedDict[int, CommandResult]" = OrderedDict()
        self._keep = keep
        self.pos: Optional[Pos] = None
        self.state: Optional[State] = None
        self.alarm: Optional[Alarm] = None
        self.last_rx_ts: Optional[float] = None
        self.last_hb_ts: Optional[float] = None
        self.last_hb_uptime_ms: Optional[int] = None
        self.last_tx_ts: Optional[float] = None
        self.logs: deque = deque(maxlen=keep_logs)
        self.unknown_lines = 0
        self.lines_rx = 0

    # ---- sending ----------------------------------------------------------

    @property
    def connected(self) -> bool:
        return bool(getattr(self._link, "connected", True))

    def send(self, command: Command) -> int:
        """Encode, write and register the command. Raises the link's error if it is
        down — a command is never silently dropped."""
        with self._lock:
            seq = self._seq.next()
            line = command.encode(seq)
            now = self._clock()
            self._results[seq] = CommandResult(seq, command, now)
            while len(self._results) > self._keep:
                self._results.popitem(last=False)
            self._link.write_line(line)
            self.last_tx_ts = now
            return seq

    def result(self, seq: int) -> Optional[CommandResult]:
        with self._lock:
            return self._results.get(seq)

    # ---- receiving --------------------------------------------------------

    def pump(self) -> list:
        """Drain the link, fold every line into the session state, return the events."""
        with self._lock:
            events = []
            for line in self._link.read_lines():
                self.lines_rx += 1
                self.last_rx_ts = self._clock()
                ev = protocol.parse_line(line)
                self._apply(ev)
                events.append(ev)
            return events

    def _apply(self, ev: Event) -> None:
        now = self._clock()
        if isinstance(ev, Ok):
            r = self._results.get(ev.seq)
            if r is not None and r.status == "sent":
                r.status, r.acked_ts = "ok", now
        elif isinstance(ev, Err):
            r = self._results.get(ev.seq)
            if r is not None:
                r.status, r.reason, r.acked_ts = "err", ev.reason, r.acked_ts or now
        elif isinstance(ev, Done):
            r = self._results.get(ev.seq)
            if r is not None and r.status != "err":
                r.status, r.pos = "done", (ev.x, ev.y, ev.z)
                r.acked_ts = r.acked_ts or now
            # `pos` telemetry stops before the last step; `done` carries the final
            # position, so the displayed position must not lag behind it.
            enc = self.pos.enc if self.pos is not None else {}
            self.pos = Pos(ev.x, ev.y, ev.z, enc)
        elif isinstance(ev, Pos):
            self.pos = ev
        elif isinstance(ev, State):
            self.state = ev
            if ev.fsm != "ALARM":
                self.alarm = None  # the latch is gone; a re-home is still needed (HOMED=0)
        elif isinstance(ev, Alarm):
            self.alarm = ev
        elif isinstance(ev, Heartbeat):
            self.last_hb_ts, self.last_hb_uptime_ms = now, ev.uptime_ms
        elif isinstance(ev, Log):
            self.logs.append(ev.text)
        elif isinstance(ev, Unknown):
            self.unknown_lines += 1

    # ---- convenience --------------------------------------------------------

    @property
    def homed(self) -> bool:
        return bool(self.state and self.state.homed)

    @property
    def vacuum(self) -> bool:
        return bool(self.state and self.state.vacuum)

    @property
    def fsm(self) -> str:
        return self.state.fsm if self.state else "UNKNOWN"

    def heartbeat_age_s(self) -> Optional[float]:
        return None if self.last_hb_ts is None else self._clock() - self.last_hb_ts
