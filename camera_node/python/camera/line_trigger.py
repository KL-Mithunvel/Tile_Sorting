"""Pure line-crossing trigger: fire once when a tile's centre crosses a line.

The station's conveyor carries tiles past a fixed point, so the natural moment
to photograph and grade one is when it reaches a chosen line in the frame --
not when it has finished leaving the frame (which is what `tile_tracker.py`
detects, and where the tile is often half out of shot or motion-blurred at the
edge).

No camera I/O -- feed it the tile's centre one frame at a time and it hands
back True on the exact frame the centre crosses. Works in **normalized**
coordinates (0.0-1.0 of frame width/height) so the same configured line
position is valid at any camera resolution; converting pixels to fractions is
the caller's job, and `TileRegion.center_normalized` does it.

Mirrors the debounce/cooldown shape of acoustic_node's `TriggerDetector` and
this package's `TileTracker`: state is only advanced by `update()`, nothing is
read from the outside world, and it is tested with synthetic sequences.

Why a deadband rather than a bare sign change: a tile sitting almost exactly on
the line jitters by a pixel or two between frames from segmentation noise, and
a naive `side != previous_side` test would fire repeatedly for one tile. The
centre must travel `hysteresis` past the line before the crossing is committed,
which costs nothing when a tile is genuinely moving and suppresses the jitter
case entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

BEFORE = -1
AFTER = 1

_VALID_ORIENTATIONS = {"vertical", "horizontal"}
_VALID_DIRECTIONS = {"forward", "reverse", "both"}


@dataclass
class CrossingEvent:
    """What happened on the frame a crossing was committed."""

    crossed: bool
    direction: Optional[str] = None  # "forward" (-> increasing) or "reverse"
    coordinate: Optional[float] = None  # the normalized coord at the crossing

    def __bool__(self) -> bool:
        return self.crossed


class LineCrossingDetector:
    """Fires once per tile, when its centre crosses the configured line.

    Args:
        position: where the line sits, as a fraction of the frame. 0.5 is the
            middle. For a "vertical" line this is a fraction of the WIDTH (the
            line runs top-to-bottom and tiles cross it travelling left/right);
            for "horizontal" it is a fraction of the HEIGHT.
        orientation: "vertical" or "horizontal".
        direction: which way a crossing counts. "forward" means the centre's
            coordinate increased through the line (left->right for a vertical
            line, top->bottom for a horizontal one), "reverse" the opposite,
            "both" accepts either. Set this to match the conveyor's travel so a
            tile nudged backwards is not counted twice.
        hysteresis: how far past the line (in the same 0-1 units) the centre
            must travel before the crossing is committed. 0 disables the
            deadband.
    """

    def __init__(
        self,
        position: float = 0.5,
        orientation: str = "vertical",
        direction: str = "both",
        hysteresis: float = 0.02,
    ):
        if not 0.0 <= position <= 1.0:
            raise ValueError(f"LineCrossingDetector: position must be 0.0-1.0, got {position}")
        if orientation not in _VALID_ORIENTATIONS:
            raise ValueError(
                f"LineCrossingDetector: orientation must be one of {sorted(_VALID_ORIENTATIONS)}, "
                f"got '{orientation}'"
            )
        if direction not in _VALID_DIRECTIONS:
            raise ValueError(
                f"LineCrossingDetector: direction must be one of {sorted(_VALID_DIRECTIONS)}, "
                f"got '{direction}'"
            )
        if hysteresis < 0:
            raise ValueError(f"LineCrossingDetector: hysteresis must be >= 0, got {hysteresis}")

        self.position = float(position)
        self.orientation = orientation
        self.direction = direction
        self.hysteresis = float(hysteresis)

        self._side: Optional[int] = None
        self._crossing_count = 0

    @property
    def crossing_count(self) -> int:
        return self._crossing_count

    @property
    def armed(self) -> bool:
        """True once a tile has been seen decisively on one side of the line,
        i.e. a crossing could be committed on a later frame."""
        return self._side is not None

    def reset(self) -> None:
        """Forget which side the current tile was on, without touching the
        count. Called when the tile leaves, so the next tile starts clean."""
        self._side = None

    def coordinate_of(self, center_normalized: tuple[float, float]) -> float:
        """The one coordinate this detector cares about: x for a vertical
        line, y for a horizontal one."""
        return center_normalized[0] if self.orientation == "vertical" else center_normalized[1]

    def update(self, center_normalized: Optional[tuple[float, float]]) -> CrossingEvent:
        """Feed one frame's tile centre, or None if no tile was detected.

        Returns a truthy CrossingEvent on exactly the frame a crossing is
        committed, and a falsy one otherwise -- so `if detector.update(c):`
        reads naturally while the event still carries the direction.

        Passing None resets the remembered side: the tile has gone, and the
        next one must re-establish which side it entered from before it can
        trigger. Without that, a new tile appearing on the far side of the
        line would look like a continuation of the previous one.
        """
        if center_normalized is None:
            self.reset()
            return CrossingEvent(crossed=False)

        coord = self.coordinate_of(center_normalized)
        delta = coord - self.position

        # Inside the deadband the tile has no committed side yet; hold
        # whatever we last knew and wait for it to travel clear of the line.
        if abs(delta) < self.hysteresis:
            return CrossingEvent(crossed=False)

        side = AFTER if delta > 0 else BEFORE

        if self._side is None:
            # First decisive sighting of this tile. We cannot call this a
            # crossing: we have no previous side to compare against, and a
            # tile that enters the frame already past the line never crossed
            # it in view.
            self._side = side
            return CrossingEvent(crossed=False)

        if side == self._side:
            return CrossingEvent(crossed=False)

        previous, self._side = self._side, side
        moved = "forward" if previous == BEFORE else "reverse"

        if self.direction != "both" and moved != self.direction:
            # A real crossing, but the wrong way for this conveyor. The side
            # is still updated above, so travelling back across the line the
            # right way afterwards does fire.
            return CrossingEvent(crossed=False, direction=moved, coordinate=coord)

        self._crossing_count += 1
        return CrossingEvent(crossed=True, direction=moved, coordinate=coord)
