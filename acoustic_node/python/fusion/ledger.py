"""Which tile does the next tap belong to?

The camera sees tile N cross its line; some seconds later (belt speed is
open-loop, so "some seconds" is not a number we can trust) the tile reaches the
tap station and a strike is heard. Timing alone cannot say which tile that was,
so the ledger relies on ORDER: tiles keep their physical order along a belt, so
the oldest tile still waiting for a tap is the one being tapped.

That is only safe if a miss is visible, so the ledger is fail-closed:

- a tile that waits longer than `max_wait_s` is expired (the coordinator turns
  that into HOLD) — it is never silently handed the next tile's tap;
- a tap with no waiting tile is an *orphan*, counted and reported, never
  attached to a tile that has not arrived yet;
- a tile younger than `min_transit_s` cannot be tapped yet (it has not had
  time to travel to the tap station), which stops an early noise being
  attributed to a tile that is still under the camera.

No clock of its own: every call takes `now`, so tests drive it with
synthetic times. No I/O.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Optional

AWAITING_TAP = "awaiting_tap"
TAPPED = "tapped"
TIMED_OUT = "timed_out"


@dataclass
class TileEntry:
    tile_id: str
    seq: Optional[int]
    camera_grade: Optional[str]
    camera: dict
    created: float
    state: str = AWAITING_TAP
    tapped_at: Optional[float] = None
    acoustic: Optional[dict] = field(default=None)


class TileLedger:
    def __init__(self, max_wait_s: float = 30.0, min_transit_s: float = 0.0, keep: int = 500):
        if max_wait_s <= 0:
            raise ValueError("max_wait_s must be > 0")
        if min_transit_s < 0 or min_transit_s >= max_wait_s:
            raise ValueError("min_transit_s must be >= 0 and < max_wait_s")
        self.max_wait_s = max_wait_s
        self.min_transit_s = min_transit_s
        self._keep = keep
        self._entries: "OrderedDict[str, TileEntry]" = OrderedDict()
        self._awaiting: deque = deque()
        self.orphan_taps = 0

    # ---- inputs ----------------------------------------------------------

    def add_camera(
        self,
        tile_id: str,
        camera_grade: Optional[str],
        camera: dict,
        now: float,
        seq: Optional[int] = None,
    ) -> tuple[TileEntry, bool]:
        """Register a tile the camera graded. Returns (entry, is_new). A tile id
        seen before (an MQTT QoS 1 re-delivery) returns the existing entry
        untouched, so a duplicate can never occupy two tap slots."""
        existing = self._entries.get(tile_id)
        if existing is not None:
            return existing, False
        entry = TileEntry(tile_id=tile_id, seq=seq, camera_grade=camera_grade, camera=camera, created=now)
        self._entries[tile_id] = entry
        self._awaiting.append(tile_id)
        self._prune()
        return entry, True

    def _prune(self) -> None:
        """Bound memory: forget the oldest FINISHED tiles (the store keeps
        them). A tile still waiting for its tap is never forgotten."""
        excess = len(self._entries) - self._keep
        if excess <= 0:
            return
        for tile_id in [t for t, e in self._entries.items() if e.state != AWAITING_TAP][:excess]:
            del self._entries[tile_id]

    def bind_tap(self, now: float) -> Optional[TileEntry]:
        """A tap was heard: hand it to the oldest eligible waiting tile, or
        None (an orphan — counted)."""
        if self._awaiting:
            head = self._entries[self._awaiting[0]]
            if now - head.created >= self.min_transit_s:
                self._awaiting.popleft()
                head.state = TAPPED
                head.tapped_at = now
                return head
        self.orphan_taps += 1
        return None

    def expire(self, now: float) -> list[TileEntry]:
        """Tiles that have waited past `max_wait_s`, oldest first."""
        out = []
        while self._awaiting:
            head = self._entries[self._awaiting[0]]
            if now - head.created < self.max_wait_s:
                break
            self._awaiting.popleft()
            head.state = TIMED_OUT
            out.append(head)
        return out

    # ---- views -----------------------------------------------------------

    def get(self, tile_id: str) -> Optional[TileEntry]:
        return self._entries.get(tile_id)

    @property
    def awaiting(self) -> list[TileEntry]:
        return [self._entries[t] for t in self._awaiting]

    def __len__(self) -> int:
        return len(self._entries)
