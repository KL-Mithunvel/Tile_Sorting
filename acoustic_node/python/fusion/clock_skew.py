"""How far each peer's clock is from this one, measured from the timestamps the
peers already put in their heartbeats. Pure: no I/O, no clock of its own.

Why this exists: the control LAN is isolated, so there is no internet NTP and
every node's clock drifts on its own (documents/electrical/network_plan.md §6).
Nothing on the line DECIDES on cross-node time -- tiles are paired by order and
tile id -- but records, alarms and the transit-time statistics from different
nodes are only comparable if the clocks agree, so a drifting peer should be
visible instead of silently making a log look wrong.

skew = peer timestamp - our receive time. It includes one-way network latency
(milliseconds on this LAN), so it is only meaningful at the scale of seconds.
A retained message replayed on reconnect carries an old timestamp and looks like
a huge skew once; the median over the last few samples (and a minimum sample
count before anything is reported) absorbs that.
"""

from __future__ import annotations

from collections import deque
from statistics import median
from typing import Optional


class SkewTracker:
    def __init__(self, warn_s: float = 1.0, window: int = 5, min_samples: int = 3, stale_s: float = 30.0):
        self._warn_s = float(warn_s)
        self._window = max(1, int(window))
        self._min_samples = max(1, min(int(min_samples), self._window))
        self._stale_s = float(stale_s)
        self._samples: dict[str, deque] = {}
        self._last_rx: dict[str, float] = {}

    def observe(self, peer: str, peer_ts, rx_ts: float) -> None:
        """Record one heartbeat. A missing / non-numeric timestamp is ignored (a
        last-will offline message has none)."""
        if isinstance(peer_ts, bool) or not isinstance(peer_ts, (int, float)):
            return
        if peer_ts != peer_ts or abs(peer_ts) == float("inf"):  # NaN / inf
            return
        self._samples.setdefault(peer, deque(maxlen=self._window)).append(float(peer_ts) - float(rx_ts))
        self._last_rx[peer] = float(rx_ts)

    def skews(self, now: float) -> dict[str, float]:
        """peer -> median skew in seconds (positive = the peer's clock is ahead),
        for peers with enough recent samples."""
        out: dict[str, float] = {}
        for peer, samples in self._samples.items():
            if len(samples) < self._min_samples:
                continue
            if now - self._last_rx.get(peer, now) > self._stale_s:
                continue
            out[peer] = float(median(samples))
        return out

    def health(self, now: float) -> Optional[dict]:
        """None until some peer has been measured. Otherwise
        {"level": "ok"|"warn", "detail": str}. Never a failure: nothing on the
        line is decided from cross-node time."""
        s = self.skews(now)
        if not s:
            return None
        detail = ", ".join(f"{p} {v:+.1f} s" for p, v in sorted(s.items()))
        worst = max(abs(v) for v in s.values())
        if worst > self._warn_s:
            return {"level": "warn", "detail": f"clocks disagree: {detail}"}
        return {"level": "ok", "detail": detail}


__all__ = ["SkewTracker"]
