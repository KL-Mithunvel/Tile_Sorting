"""Where things are: named pickup point + named stacks, in gantry millimetres.

Pure — no I/O. The Pi names a source and a destination (`pickup`, `bin_1`...);
this module is the only place those names become XYZ and the only place that
knows how high each stack currently is. The MCU knows axes and mm only
(documents/programming/pick_place_control_protocol.md §3).

Z convention: Z is the height of the **vacuum-cup tip**, larger = higher (same as
the twin and the protocol doc's example). A tile hangs `thickness` below the tip.

    pickup.z      cup-tip Z when it touches the top of a tile waiting at the
                  conveyor end.
    stack.base_z  cup-tip Z when RELEASING the first tile onto an empty stack
                  (the carried tile's underside is then on the floor).
    n tiles in    release at base_z + n * thickness.

Every number comes from config.yaml and is provisional until taught-in on the
built gantry (Development Rule 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


class ConfigError(ValueError):
    """The configuration cannot describe a safe gantry (refuse to start)."""


class UnknownLocationError(KeyError):
    """A source/destination name that is not in config (refuse the job, never guess)."""


class StackFullError(RuntimeError):
    """The destination stack is at max_height; an operator must empty it."""


class OutOfEnvelopeError(ValueError):
    """A planned move leaves the soft-limit envelope."""


@dataclass(frozen=True)
class Point:
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class Envelope:
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float

    def contains(self, x: float, y: float, z: float) -> bool:
        return (self.x_min <= x <= self.x_max and self.y_min <= y <= self.y_max
                and self.z_min <= z <= self.z_max)


@dataclass(frozen=True)
class StackSpec:
    name: str
    x: float
    y: float
    base_z_mm: float
    max_height: int
    stacking: bool = True  # False = a drop-in bin: always release at base_z
    label: str = ""        # display only (e.g. "Grade A"); the Pi's name is `name`


class CoordinateModel:
    def __init__(self, envelope: Envelope, tile_thickness_mm: float, safe_z_mm: float,
                 pickups: dict, stacks: dict, safe_clearance_mm: float = 10.0,
                 tile_size_mm: float = 0.0, tile_weight_g: float = 0.0):
        self.envelope = envelope
        self.tile_thickness_mm = float(tile_thickness_mm)
        self.safe_z_mm = float(safe_z_mm)
        self.safe_clearance_mm = float(safe_clearance_mm)
        self.tile_size_mm = float(tile_size_mm)
        self.tile_weight_g = float(tile_weight_g)
        self._pickups: dict = dict(pickups)
        self._stacks: dict = dict(stacks)
        self._counts: dict = {name: 0 for name in self._stacks}
        self._validate()

    # ---- construction ----------------------------------------------------

    @classmethod
    def from_config(cls, config: dict) -> "CoordinateModel":
        try:
            ws = config["workspace"]
            env = Envelope(ws["x_min_mm"], ws["x_max_mm"], ws["y_min_mm"], ws["y_max_mm"],
                           ws["z_min_mm"], ws["z_max_mm"])
            gantry = config["gantry"]
            pickups = {n: Point(float(p["x"]), float(p["y"]), float(p["z"]))
                       for n, p in config["locations"].items()}
            stacks = {n: StackSpec(n, float(s["x"]), float(s["y"]), float(s["base_z_mm"]),
                                   int(s["max_height"]), bool(s.get("stacking", True)),
                                   str(s.get("label", "")))
                      for n, s in config["stacks"].items()}
            tile = config["tile"]
            return cls(env, tile["thickness_mm"], gantry["safe_z_mm"], pickups, stacks,
                       gantry.get("safe_clearance_mm", 10.0),
                       tile_size_mm=tile.get("size_mm", 0.0), tile_weight_g=tile.get("weight_g", 0.0))
        except (KeyError, TypeError) as exc:
            raise ConfigError(f"pick_place config is missing or malformed: {exc!r}") from exc

    def _validate(self) -> None:
        if self.tile_thickness_mm <= 0:
            raise ConfigError("tile.thickness_mm must be positive")
        if not self._pickups:
            raise ConfigError("no pickup locations configured")
        if not self._stacks:
            raise ConfigError("no stacks configured")
        if not self.envelope.contains(self.envelope.x_min, self.envelope.y_min, self.safe_z_mm):
            raise ConfigError(f"gantry.safe_z_mm {self.safe_z_mm} is outside the Z envelope")
        for n, p in self._pickups.items():
            if not self.envelope.contains(p.x, p.y, p.z):
                raise ConfigError(f"location {n!r} {p} is outside the envelope")
            if p.z >= self.safe_z_mm:
                raise ConfigError(f"location {n!r} z {p.z} is not below safe z {self.safe_z_mm}")
        for n, s in self._stacks.items():
            if s.max_height < 1:
                raise ConfigError(f"stack {n!r}: max_height must be at least 1")
            highest_release = s.base_z_mm + (s.max_height - 1) * self.tile_thickness_mm * s.stacking
            for z in (s.base_z_mm, highest_release):
                if not self.envelope.contains(s.x, s.y, z):
                    raise ConfigError(f"stack {n!r} release point ({s.x}, {s.y}, {z}) is outside the envelope")
            # A carried tile hangs `thickness` below the cup tip; at safe Z its underside
            # must clear the top of the tallest this stack can get.
            tallest_top = s.base_z_mm + (s.max_height - 1) * self.tile_thickness_mm * s.stacking
            clearance = (self.safe_z_mm - self.tile_thickness_mm) - tallest_top
            if clearance < self.safe_clearance_mm:
                raise ConfigError(
                    f"stack {n!r}: at safe z a carried tile clears a full stack by only "
                    f"{clearance:.1f} mm (need {self.safe_clearance_mm:.1f}); raise gantry.safe_z_mm "
                    f"or lower max_height")

    # ---- lookups ---------------------------------------------------------

    @property
    def pickup_names(self) -> tuple:
        return tuple(self._pickups)

    @property
    def stack_names(self) -> tuple:
        return tuple(self._stacks)

    def pickup_point(self, name: str) -> Point:
        try:
            return self._pickups[name]
        except KeyError:
            raise UnknownLocationError(f"unknown source {name!r}; known: {sorted(self._pickups)}") from None

    def _stack(self, name: str) -> StackSpec:
        try:
            return self._stacks[name]
        except KeyError:
            raise UnknownLocationError(f"unknown destination {name!r}; known: {sorted(self._stacks)}") from None

    def place_point(self, name: str) -> Point:
        """Where the cup tip goes to release the NEXT tile on `name`.
        Raises StackFullError when a stacking stack is full."""
        s = self._stack(name)
        n = self._counts[name]
        if s.stacking and n >= s.max_height:
            raise StackFullError(f"stack {name!r} is full ({n} tiles)")
        z = s.base_z_mm + (n * self.tile_thickness_mm if s.stacking else 0.0)
        return Point(s.x, s.y, z)

    # ---- stack bookkeeping ----------------------------------------------

    def count(self, name: str) -> int:
        self._stack(name)
        return self._counts[name]

    def counts(self) -> dict:
        return dict(self._counts)

    def is_full(self, name: str) -> bool:
        s = self._stack(name)
        return s.stacking and self._counts[name] >= s.max_height

    def record_placed(self, name: str) -> int:
        """Call only after the release has been confirmed `done`. Returns the new count."""
        self._stack(name)
        self._counts[name] += 1
        return self._counts[name]

    def reset_stack(self, name: str, count: int = 0) -> None:
        """Operator emptied (or restocked) a stack."""
        s = self._stack(name)
        if count < 0 or (s.stacking and count > s.max_height):
            raise ValueError(f"count {count} is outside 0..{s.max_height}")
        self._counts[name] = count

    # ---- for the GUI -----------------------------------------------------

    def describe(self) -> dict:
        """JSON-able snapshot: the layout plus live stack counts (/api/layout)."""
        stacks = []
        for name, s in self._stacks.items():
            n = self._counts[name]
            full = s.stacking and n >= s.max_height
            stacks.append({
                "name": name, "label": s.label, "x": s.x, "y": s.y,
                "base_z_mm": s.base_z_mm, "max_height": s.max_height, "stacking": s.stacking,
                "count": n, "full": full,
                "next_release_z_mm": None if full else s.base_z_mm + (n * self.tile_thickness_mm if s.stacking else 0.0),
            })
        return {
            "tile": {"size_mm": self.tile_size_mm, "thickness_mm": self.tile_thickness_mm,
                     "weight_g": self.tile_weight_g},
            "safe_z_mm": self.safe_z_mm,
            "pickups": [{"name": n, "x": p.x, "y": p.y, "z": p.z} for n, p in self._pickups.items()],
            "stacks": stacks,
        }

    # ---- envelope --------------------------------------------------------

    def check_in_envelope(self, x: Optional[float], y: Optional[float], z: Optional[float]) -> None:
        """Raise OutOfEnvelopeError if any given axis is outside; None = axis holds."""
        e = self.envelope
        for label, value, lo, hi in (("X", x, e.x_min, e.x_max), ("Y", y, e.y_min, e.y_max),
                                     ("Z", z, e.z_min, e.z_max)):
            if value is not None and not (lo <= value <= hi):
                raise OutOfEnvelopeError(f"{label}={value} is outside {lo}..{hi}")
