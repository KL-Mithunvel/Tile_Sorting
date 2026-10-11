"""A frame-rate meter: events per second over a short recent window.

Pure (timestamps in, a number out) so it is testable without a clock. Used for the
camera's capture rate and the pipeline's processing rate, which are different
numbers on purpose: when processing is slower than capture the difference is
frames that were skipped, and a station that quietly processes 18 of a camera's
30 frames per second should say so.
"""

from __future__ import annotations

from collections import deque
from typing import Optional


class RateMeter:
    """`tick(t)` once per event; `rate()` is events/second over the last `window`
    events. None until there are two events (one event has no rate)."""

    def __init__(self, window: int = 30):
        if window < 2:
            raise ValueError(f"RateMeter: window must be >= 2, got {window}")
        self._times: deque[float] = deque(maxlen=window)

    def tick(self, t: float) -> None:
        self._times.append(t)

    def rate(self) -> Optional[float]:
        if len(self._times) < 2:
            return None
        span = self._times[-1] - self._times[0]
        return (len(self._times) - 1) / span if span > 0 else None

    def reset(self) -> None:
        self._times.clear()
