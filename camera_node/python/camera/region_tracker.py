"""Pure tile identity: keep each tile's identity across frames.

The bug this exists for (CLAUDE.md, Known Technical Debt): on the real belt clip
~35% of frames hold two tile-sized regions whose areas differ by ~0.02%. A
pipeline that reports "the largest region" therefore swaps between two
physically different tiles on segmentation noise, and the reported centre
teleports ~0.6 of the frame in one frame. Nothing downstream -- the line
trigger, the count, the per-tile grade -- can be right on top of that.

The fix is to stop pretending there is one tile. Every region found in a frame is
matched to the tiles seen in the previous frame by nearest centre, and each
match keeps its track id. A tile moves a few percent of the frame per frame, so
it is always far closer to *itself* than to its neighbour half a frame away,
however similar their areas are.

`RegionTracker` does the association; `LineCrossingTracker` gives each track its
own `LineCrossingDetector` so every tile fires exactly once, independently.

No I/O: centres in, ids out, so it is synthetic-sequence testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from camera.line_trigger import LineCrossingDetector

Center = tuple[float, float]


@dataclass
class _Track:
    center: Center
    missed: int = 0


class RegionTracker:
    """Associates this frame's region centres with the tiles seen before.

    Args:
        max_match_distance: farthest a tile's centre may move between two
            consecutive frames and still be the same tile, in normalised
            (0.0-1.0) frame units (Euclidean). It must exceed real belt travel
            per frame and stay well below the gap between neighbouring tiles.
            The two are separated by ~10x on the real clip; re-derive on the
            rig once belt speed and frame rate are known.
        max_missed: how many consecutive frames a tile may go unseen (a shape
            gate flicker, a momentary occlusion) before its track is dropped.
    """

    def __init__(self, max_match_distance: float = 0.15, max_missed: int = 3):
        if max_match_distance <= 0:
            raise ValueError(f"RegionTracker: max_match_distance must be > 0, got {max_match_distance}")
        if max_missed < 0:
            raise ValueError(f"RegionTracker: max_missed must be >= 0, got {max_missed}")
        self.max_match_distance = float(max_match_distance)
        self.max_missed = int(max_missed)
        self._tracks: dict[int, _Track] = {}
        self._next_id = 1
        self.lost: list[int] = []

    @property
    def active_ids(self) -> list[int]:
        return sorted(self._tracks)

    def update(self, centers: Sequence[Center]) -> list[int]:
        """Feed this frame's region centres; returns one track id per centre,
        in the same order. `self.lost` is then the ids dropped this call.

        Matching is greedy over ALL (track, detection) pairs nearest-first
        rather than detection by detection, so the order regions arrive in
        (largest-first, which is the noisy part) cannot decide who wins a
        contested track.
        """
        self.lost = []
        pairs = sorted(
            (_dist(track.center, c), tid, di)
            for tid, track in self._tracks.items()
            for di, c in enumerate(centers)
        )

        assigned: dict[int, int] = {}  # detection index -> track id
        used_tracks: set[int] = set()
        for dist, tid, di in pairs:
            if dist > self.max_match_distance:
                break  # sorted: every remaining pair is farther still
            if tid in used_tracks or di in assigned:
                continue
            assigned[di] = tid
            used_tracks.add(tid)

        ids: list[int] = []
        for di, c in enumerate(centers):
            tid = assigned.get(di)
            if tid is None:  # nothing close enough: a new tile has appeared
                tid = self._next_id
                self._next_id += 1
                self._tracks[tid] = _Track(center=c)
            else:
                self._tracks[tid].center = c
                self._tracks[tid].missed = 0
            ids.append(tid)

        for tid in list(self._tracks):
            if tid in used_tracks or tid in ids:
                continue
            self._tracks[tid].missed += 1
            if self._tracks[tid].missed > self.max_missed:
                del self._tracks[tid]
                self.lost.append(tid)
        return ids


def _dist(a: Center, b: Center) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


@dataclass
class Observation:
    """One region seen this frame, with its identity and whether it just
    crossed the line."""

    track_id: int
    region: Any  # a TileRegion; typed loosely so this module needs no cv2
    crossed: bool


class LineCrossingTracker:
    """Tile identity + per-tile line crossing, in one pure object.

    Each tile gets its own `LineCrossingDetector`, so two tiles on the belt each
    fire exactly once, however many frames they share, and a tile jumping to the
    other side of the line is never mistaken for its neighbour crossing it.

    `line` is the keyword arguments for `LineCrossingDetector`
    (position / orientation / direction / hysteresis / max_step).
    """

    def __init__(
        self,
        line: dict,
        match_distance: float = 0.15,
        max_missed_frames: int = 3,
    ):
        self._line = dict(line)
        LineCrossingDetector(**self._line)  # validate eagerly, not on the first tile
        self._tracker = RegionTracker(match_distance, max_missed_frames)
        self._detectors: dict[int, LineCrossingDetector] = {}
        self._crossing_count = 0

    @property
    def crossing_count(self) -> int:
        return self._crossing_count

    def update(self, regions: Sequence[Any]) -> list[Observation]:
        """Feed every region found this frame (anything with
        `.center_normalized`); returns one Observation per region, same order."""
        ids = self._tracker.update([r.center_normalized for r in regions])

        for tid in self._tracker.lost:
            self._detectors.pop(tid, None)

        observed: list[Observation] = []
        for tid, region in zip(ids, regions):
            detector = self._detectors.get(tid)
            if detector is None:
                detector = self._detectors[tid] = LineCrossingDetector(**self._line)
            crossed = bool(detector.update(region.center_normalized))
            if crossed:
                self._crossing_count += 1
            observed.append(Observation(track_id=tid, region=region, crossed=crossed))
        return observed

    @property
    def active_ids(self) -> list[int]:
        """Ids currently tracked -- for callers that keep per-track display
        state and want to prune it."""
        return self._tracker.active_ids
