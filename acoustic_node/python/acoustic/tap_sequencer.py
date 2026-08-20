"""Pure gravity-drop tap-sequencer FSM — no I/O, no threading, no clock of its own.

Mirrors the arm/lock timing sequence that acoustic_node/sketch/sketch.ino runs on
the MCU (see documents/electrical/schematics/acoustic_station_wiring.md and
documents/project/Acoustic_Sorting_Subsystem.md §3.1 for the mechanism this
models): a ToF sensor confirms a tile is in place, an ARM solenoid raises the
striker/ball to drop height, then a LOCK solenoid releases it to free-fall onto
the tile under gravity. Neither solenoid touches the tile directly.

Kept here and tested with synthetic time steps the same way
pick_place/mock_state.py's MockGantrySimulator is (.claude/CLAUDE.md Development
Rule 1: hardware I/O stays separate from pure logic), for two reasons:

1. Validates the intended timing/state design before real hardware exists.
2. Doubles as the dev-machine simulator of tap events (acoustic/config.yaml's
   trigger.mode: "simulated" — see acoustic/live_monitor.py), so the audio
   capture pipeline can be developed without a ToF sensor or solenoids
   connected. Same "Simulation vs real mode" split as camera_node/pick_place_node.

The sketch is the source of truth for real hardware timing; acoustic/config.yaml's
tap_mechanism section mirrors these constants for the simulator only and is not
read by the sketch — keep the two in sync by hand.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

_STATE_ORDER = ["SETTLE", "ARM", "ARM_SETTLE", "RELEASE", "COOLDOWN"]


def load_config(path: Optional[Path] = None) -> dict:
    if path is None:
        path = Path(__file__).parent / "config.yaml"
    with open(path, "r") as f:
        return yaml.safe_load(f)


@dataclass(frozen=True)
class TapState:
    state: str
    arm_on: bool
    lock_on: bool
    tap_fired: bool
    tap_count: int


class TapSequencer:
    """States: WAIT_TILE -> SETTLE -> ARM -> ARM_SETTLE -> RELEASE -> COOLDOWN -> WAIT_TILE.

    `advance(dt_s, tile_present)` is the only entry point, mirroring
    MockGantrySimulator.advance(dt_s). `tile_present` must go False then True
    again (an edge, not a level) to arm a new cycle — this stops the same tile
    re-triggering a tap for as long as it happens to sit at the station.
    """

    def __init__(
        self,
        settle_s: float,
        arm_s: float,
        arm_settle_s: float,
        lock_pulse_s: float,
        cooldown_s: float,
    ):
        for name, value in (
            ("settle_s", settle_s),
            ("arm_s", arm_s),
            ("arm_settle_s", arm_settle_s),
            ("lock_pulse_s", lock_pulse_s),
            ("cooldown_s", cooldown_s),
        ):
            if value < 0:
                raise ValueError(f"{name} must be non-negative")

        self._durations = {
            "SETTLE": settle_s,
            "ARM": arm_s,
            "ARM_SETTLE": arm_settle_s,
            "RELEASE": lock_pulse_s,
            "COOLDOWN": cooldown_s,
        }
        self._state = "WAIT_TILE"
        self._remaining = 0.0
        self._tile_was_absent = True  # allow the very first tile to arm
        self._tap_count = 0

    @property
    def state(self) -> str:
        return self._state

    @property
    def tap_count(self) -> int:
        return self._tap_count

    def advance(self, dt_s: float, tile_present: bool) -> TapState:
        if dt_s < 0:
            raise ValueError("dt_s must be non-negative")

        tap_fired = False

        if self._state == "WAIT_TILE":
            if not tile_present:
                self._tile_was_absent = True
            elif self._tile_was_absent:
                self._tile_was_absent = False
                self._enter("SETTLE")
        else:
            self._remaining -= dt_s
            if self._remaining <= 0:
                tap_fired = self._advance_state()

        return TapState(
            state=self._state,
            arm_on=(self._state == "ARM"),
            lock_on=(self._state == "RELEASE"),
            tap_fired=tap_fired,
            tap_count=self._tap_count,
        )

    def _enter(self, state: str) -> None:
        self._state = state
        self._remaining = self._durations[state]

    def _advance_state(self) -> bool:
        next_index = _STATE_ORDER.index(self._state) + 1
        if next_index >= len(_STATE_ORDER):
            self._state = "WAIT_TILE"
            self._remaining = 0.0
            return False

        next_state = _STATE_ORDER[next_index]
        self._enter(next_state)
        if next_state == "RELEASE":
            self._tap_count += 1
            return True
        return False
