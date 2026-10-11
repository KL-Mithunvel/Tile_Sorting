"""Choose WHICH frame of a tile's pass over the line gets graded: the sharpest one.

Grading the frame in which the tile's centre first crosses the line is consistent
(every tile is graded at the same place) but ignores that frames differ in blur: the
tile is moving, and a webcam's exposure and compression make some frames visibly
smearier than their neighbours. So for a short stretch around the line -- the "band"
-- every frame of the tile is scored for sharpness, and when the tile has crossed
and left the band the best one is handed on to be graded.

Costs: grading happens a few frames later (the tile has to leave the band), and the
graded frame can be up to `band` away from the line instead of exactly on it. Both
are small: at the sample belt speed (14.5 px/frame) a band of 0.06 of a 1080-px frame
is about 9 frames either side.

Pure: observations in, picks out, scoring supplied by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence


def belt_axis_from_line(orientation: Optional[str]) -> Optional[str]:
    """The way the belt runs, given the trigger line: the line crosses the belt, so the belt runs
    perpendicular to it. A "horizontal" line means a belt running top-to-bottom ("vertical").
    Used when the belt itself could not be measured (belt ROI off), so the sharpness metric still
    knows which edges motion smears."""
    return {"horizontal": "vertical", "vertical": "horizontal"}.get(orientation)


@dataclass
class Pick:
    """The frame chosen for one tile."""

    track_id: int
    region: Any  # the TileRegion in the chosen frame
    score: Optional[float]  # its sharpness; None when picking is off
    candidates: int  # how many frames were scored to choose it
    frame: Any = None  # the full frame the region came from
    frame_index: Optional[int] = None


@dataclass
class _Track:
    crossed: bool = False
    done: bool = False
    in_band_frames: int = 0
    candidates: int = 0
    best_score: float = -1.0
    best_region: Any = None
    best_frame: Any = None
    best_index: Optional[int] = None
    absent: int = 0


class BestFramePicker:
    """
    Args:
        band: half-width of the zone around the line, as a fraction of the frame
            (0.0-1.0) along the axis the tile travels. 0 turns picking off: a tile
            is then graded on the frame it crosses, as before.
        max_frames: give up waiting after this many frames in the band (a tile
            that stops on the line must still be graded).
        max_absent: updates a tile may be missing before it is treated as gone.
    """

    def __init__(self, band: float = 0.06, max_frames: int = 30, max_absent: int = 3):
        if band < 0:
            raise ValueError(f"BestFramePicker: band must be >= 0, got {band}")
        if max_frames < 1:
            raise ValueError(f"BestFramePicker: max_frames must be >= 1, got {max_frames}")
        self.band = float(band)
        self.max_frames = int(max_frames)
        self.max_absent = int(max_absent)
        self._tracks: dict[int, _Track] = {}

    def reset(self) -> None:
        self._tracks.clear()

    def update(
        self,
        observations: Sequence[Any],
        orientation: Optional[str],
        position: float,
        score_fn: Callable[[Any], float],
        frame: Any = None,
        frame_index: Optional[int] = None,
    ) -> list[Pick]:
        """Feed one frame's `LineCrossingTracker` observations (each with
        `.track_id`, `.region`, `.crossed`); returns the tiles whose frame has now
        been chosen. `score_fn(region)` rates a region's sharpness and is only
        called for tiles inside the band."""
        if self.band <= 0 or orientation is None:
            return [
                Pick(o.track_id, o.region, None, 1, frame, frame_index) for o in observations if o.crossed
            ]

        picks: list[Pick] = []
        seen: set[int] = set()
        axis = 1 if orientation == "horizontal" else 0  # a horizontal line is crossed along y

        for obs in observations:
            seen.add(obs.track_id)
            st = self._tracks.setdefault(obs.track_id, _Track())
            st.absent = 0
            if st.done:
                continue
            if obs.crossed:
                st.crossed = True

            in_band = abs(obs.region.center_normalized[axis] - position) <= self.band
            # A crossing seen outside the band (a very fast tile, a narrow band) must
            # still produce a candidate, or the tile would never be graded.
            if in_band or (obs.crossed and st.best_region is None):
                st.in_band_frames += 1
                st.candidates += 1
                score = float(score_fn(obs.region))
                if score > st.best_score or st.best_region is None:
                    st.best_score, st.best_region = score, obs.region
                    st.best_frame, st.best_index = frame, frame_index

            if st.crossed and st.best_region is not None and (not in_band or st.in_band_frames >= self.max_frames):
                picks.append(self._emit(obs.track_id, st))

        for tid in list(self._tracks):
            if tid in seen:
                continue
            st = self._tracks[tid]
            st.absent += 1
            if st.absent > self.max_absent:
                # The tile has gone. If it crossed and was never emitted (it left the
                # frame while still in the band), grade the best frame seen.
                if st.crossed and not st.done and st.best_region is not None:
                    picks.append(self._emit(tid, st))
                del self._tracks[tid]
        return picks

    @staticmethod
    def _emit(track_id: int, st: _Track) -> Pick:
        st.done = True
        pick = Pick(track_id, st.best_region, st.best_score, st.candidates, st.best_frame, st.best_index)
        st.best_region = st.best_frame = None  # let the frame be collected
        return pick
