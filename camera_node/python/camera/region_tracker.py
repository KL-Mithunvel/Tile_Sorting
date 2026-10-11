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
from typing import Any, Optional, Sequence

from camera.belt_roi import roi_applies
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
    # "forward" (the centre's coordinate increased through the line: top-to-bottom
    # or left-to-right) or "reverse"; None unless `crossed`.
    direction: Optional[str] = None


class LineCrossingTracker:
    """Tile identity + per-tile line crossing, in one pure object.

    Each tile gets its own `LineCrossingDetector`, so two tiles on the belt each
    fire exactly once, however many frames they share, and a tile jumping to the
    other side of the line is never mistaken for its neighbour crossing it.

    `line` is the keyword arguments for `LineCrossingDetector`
    (position / orientation / direction / hysteresis / max_step), except that
    `orientation` may be **"auto"**: the line is then made PERPENDICULAR to the
    belt, whichever way the belt runs in the frame. Pass the belt's direction
    ("vertical" = running top-to-bottom, "horizontal" = left-to-right, as
    `belt_roi.BeltRoi.axis` reports it) to `update()`; once `auto_frames` frames
    have agreed, the orientation is fixed for the rest of the run. Until then no
    tile can fire -- a belt direction that is still being learned is not a
    reason to guess a line. A camera can be mounted either way round, and the
    belt can run along either image axis, so a fixed "vertical" default was
    silently wrong (parallel to the belt, never crossed) for half the setups.
    """

    def __init__(
        self,
        line: dict,
        match_distance: float = 0.15,
        max_missed_frames: int = 3,
        auto_frames: int = 8,
    ):
        line = dict(line)
        self._auto = line.get("orientation") == "auto"
        self._orientation: Optional[str] = None if self._auto else line.get("orientation", "vertical")
        line.pop("orientation", None)
        self._line = line
        # Validate eagerly, not on the first tile ("vertical" stands in while "auto" is unresolved).
        LineCrossingDetector(orientation=self._orientation or "vertical", **self._line)
        if auto_frames < 1:
            raise ValueError(f"LineCrossingTracker: auto_frames must be >= 1, got {auto_frames}")
        self._auto_frames = int(auto_frames)
        self._votes = {"vertical": 0, "horizontal": 0}
        self._tracker = RegionTracker(match_distance, max_missed_frames)
        self._detectors: dict[int, LineCrossingDetector] = {}
        self._crossing_count = 0

    @property
    def crossing_count(self) -> int:
        return self._crossing_count

    @property
    def orientation(self) -> Optional[str]:
        """The line's orientation ("vertical" / "horizontal"), or None while
        "auto" is still waiting to learn the belt's direction."""
        return self._orientation

    @property
    def resolved(self) -> bool:
        return self._orientation is not None

    def _learn_orientation(self, belt_axis: Optional[str]) -> None:
        if belt_axis not in self._votes:
            return  # no belt this frame, or its direction was unclear
        self._votes[belt_axis] += 1
        total = self._votes["vertical"] + self._votes["horizontal"]
        if total < self._auto_frames or self._votes["vertical"] == self._votes["horizontal"]:
            return
        belt = "vertical" if self._votes["vertical"] > self._votes["horizontal"] else "horizontal"
        # The line crosses the belt: perpendicular to the way it runs.
        self._orientation = "horizontal" if belt == "vertical" else "vertical"

    def update(self, regions: Sequence[Any], belt_axis: Optional[str] = None) -> list[Observation]:
        """Feed every region found this frame (anything with
        `.center_normalized`); returns one Observation per region, same order.
        `belt_axis` is only used while the orientation is "auto" and unresolved."""
        if not self.resolved:
            self._learn_orientation(belt_axis)

        ids = self._tracker.update([r.center_normalized for r in regions])

        for tid in self._tracker.lost:
            self._detectors.pop(tid, None)

        observed: list[Observation] = []
        for tid, region in zip(ids, regions):
            crossed = False
            direction = None
            if self.resolved:
                detector = self._detectors.get(tid)
                if detector is None:
                    detector = self._detectors[tid] = LineCrossingDetector(
                        orientation=self._orientation, **self._line
                    )
                event = detector.update(region.center_normalized)
                crossed = bool(event)
                if crossed:
                    self._crossing_count += 1
                    direction = event.direction
            observed.append(Observation(track_id=tid, region=region, crossed=crossed, direction=direction))
        return observed

    @property
    def active_ids(self) -> list[int]:
        """Ids currently tracked -- for callers that keep per-track display
        state and want to prune it."""
        return self._tracker.active_ids


def validate_line_config(trigger_cfg: Optional[dict], seg_cfg: dict) -> None:
    """Refuse a configuration that could never fire, at start-up rather than as
    a silent "0 tiles". `orientation: "auto"` reads the belt's direction from the
    belt ROI, so it cannot work with the ROI off."""
    orientation = (trigger_cfg or {}).get("orientation", "auto")
    if orientation == "auto" and not roi_applies(seg_cfg.get("belt_roi")):
        raise ValueError(
            "trigger_line.orientation is 'auto', which reads the belt's direction from "
            "segmentation.belt_roi, but the belt ROI is off. Turn the belt ROI on "
            "(mode 'color' or 'fixed') or set trigger_line.orientation to 'vertical' or 'horizontal'."
        )
