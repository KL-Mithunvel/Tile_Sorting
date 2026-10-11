"""Expand one pick-and-place job into the ordered list of gantry commands.

Pure — no I/O. Protocol doc §5.5 is the shape:

    raise to safe Z -> XY over source -> descend -> VAC ON -> lift ->
    XY over destination -> descend to the current stack height -> VAC OFF -> retract

The plan is data. The job runner sends one step at a time, waits for `done`, and
owns retry, parking and `record_placed()` — this module changes no state, so
planning a job that is then never run leaves the stacks untouched.

Order is Z-then-XY-then-Z, always: the cup is at safe Z whenever X or Y changes,
which is what keeps a carried tile from dragging across a stack.
"""

from __future__ import annotations

from dataclasses import dataclass

from pick_place import protocol
from pick_place.coordinate_model import CoordinateModel
from pick_place.protocol import Command


@dataclass(frozen=True)
class Feeds:
    """Move speeds, mm/min. Provisional until tuned on the built gantry."""

    travel_xy: float
    travel_z: float
    approach_z: float  # the slow final descent onto a tile / stack
    lift_z: float      # lifting with the tile on the cup

    @classmethod
    def from_config(cls, config: dict) -> "Feeds":
        f = config["gantry"]["feed_mm_min"]
        return cls(float(f["travel_xy"]), float(f["travel_z"]), float(f["approach_z"]), float(f["lift_z"]))


@dataclass(frozen=True)
class PlanStep:
    command: Command
    label: str


@dataclass(frozen=True)
class PickPlacePlan:
    source: str
    destination: str
    steps: tuple
    pick_first: int  # steps[pick_first:pick_last + 1] is the "pick" — re-issue it once on VACUUM_TIMEOUT
    pick_last: int
    place_z: float   # where the cup tip releases, so a report can say how high the stack was

    @property
    def pick_steps(self) -> tuple:
        return self.steps[self.pick_first:self.pick_last + 1]

    @property
    def release_index(self) -> int:
        """Index of the VAC OFF step; the tile is on the stack once it is `done`."""
        return next(i for i, s in enumerate(self.steps) if s.label == "release")


def plan_pick_place(source: str, destination: str, model: CoordinateModel, feeds: Feeds) -> PickPlacePlan:
    """Raises UnknownLocationError / StackFullError / OutOfEnvelopeError before any
    command exists, so a bad job is refused whole rather than started."""
    src = model.pickup_point(source)
    dst = model.place_point(destination)
    safe = model.safe_z_mm

    raw = [
        ("raise", protocol.move(z=safe, f=feeds.travel_z), (None, None, safe)),
        ("over_source", protocol.move(x=src.x, y=src.y, f=feeds.travel_xy), (src.x, src.y, None)),
        ("descend_pick", protocol.move(z=src.z, f=feeds.approach_z), (None, None, src.z)),
        ("grip", protocol.vac(True), None),
        ("lift", protocol.move(z=safe, f=feeds.lift_z), (None, None, safe)),
        ("over_destination", protocol.move(x=dst.x, y=dst.y, f=feeds.travel_xy), (dst.x, dst.y, None)),
        ("descend_place", protocol.move(z=dst.z, f=feeds.approach_z), (None, None, dst.z)),
        ("release", protocol.vac(False), None),
        ("retract", protocol.move(z=safe, f=feeds.travel_z), (None, None, safe)),
    ]
    for _label, _cmd, target in raw:
        if target is not None:
            model.check_in_envelope(*target)

    steps = tuple(PlanStep(cmd, label) for label, cmd, _ in raw)
    labels = [s.label for s in steps]
    return PickPlacePlan(source, destination, steps,
                         pick_first=labels.index("descend_pick"), pick_last=labels.index("grip"),
                         place_z=dst.z)


def plan_park(model: CoordinateModel, feeds: Feeds) -> PlanStep:
    """The single command that makes the gantry safe after a failed job."""
    return PlanStep(protocol.move(z=model.safe_z_mm, f=feeds.travel_z), "park")
