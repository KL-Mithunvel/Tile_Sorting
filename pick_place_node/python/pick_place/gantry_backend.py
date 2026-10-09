"""The seam between the control UI and whatever moves (or pretends to move).

The Flask dashboard and TwinWorker only ever talk to a `GantryBackend`.
Today the only implementation is `MockGantrySimulator`; the real one
(serial link to the UNO Q MCU firmware, per
documents/programming/pick_place_control_protocol.md) slots in here without
touching the page or the routes.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pick_place.mock_state import GantryPose, MockGantrySimulator, build_default_patrol


@runtime_checkable
class GantryBackend(Protocol):
    mode: str

    def advance(self, dt_s: float) -> GantryPose: ...
    def jog(self, axis: str, delta_mm: float) -> None: ...
    def set_vacuum(self, on: bool) -> None: ...
    def set_mode(self, mode: str) -> None: ...


def create_backend(config: dict) -> GantryBackend:
    """Build the backend named by config['backend'] ('mock' by default)."""
    kind = config.get("backend", "mock")
    if kind == "mock":
        return MockGantrySimulator(
            build_default_patrol(config),
            travel_speed_mm_s=config["mock"]["travel_speed_mm_s"],
            workspace=config["workspace"],
        )
    if kind == "serial":
        raise NotImplementedError(
            "No serial gantry backend yet: the firmware and protocol.py are not written "
            "(documents/programming/pick_place_control_protocol.md)."
        )
    raise ValueError(f"unknown backend {kind!r} (expected 'mock' or 'serial')")
