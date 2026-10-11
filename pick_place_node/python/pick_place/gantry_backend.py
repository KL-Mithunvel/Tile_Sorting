"""The seam between the control UI and whatever moves (or pretends to move).

The Flask dashboard and TwinWorker only ever talk to a `GantryBackend`
(`advance` / `jog` / `set_vacuum` / `set_mode` + `mode`). Three implementations:

    "mock"    MockGantrySimulator       pure motion, no protocol at all
    "sim"     SerialGantryBackend over  the line protocol against SimulatedMcu
              LoopbackLink                (the whole real path, no hardware)
    "serial"  SerialGantryBackend over  the real UNO Q MCU over USB serial
              SerialGantryLink            (never run against the board yet)

`create_job_driver()` picks the matching executor of queued tiles: the mock
driver for "mock", the real `JobRunner` for the other two.
"""

from __future__ import annotations

from typing import Callable, Optional, Protocol, runtime_checkable

from pick_place import protocol
from pick_place.gantry_session import GantrySession
from pick_place.mock_state import GantryPose, MockGantrySimulator, build_default_patrol
from pick_place.serial_link import LinkError

BACKEND_MODES = ("manual", "jobs")  # a real gantry has no "patrol"


@runtime_checkable
class GantryBackend(Protocol):
    mode: str

    def advance(self, dt_s: float) -> GantryPose: ...
    def jog(self, axis: str, delta_mm: float) -> None: ...
    def set_vacuum(self, on: bool) -> None: ...
    def set_mode(self, mode: str) -> None: ...


class SerialGantryBackend:
    """The twin's backend over a `GantrySession`: real position and state in, JOG /
    VAC out. It does not plan; the `JobRunner` shares the same session and runs
    plans while `mode == "jobs"`."""

    def __init__(self, session: GantrySession, jog_feed_mm_min: float = 1500.0,
                 initial_mode: str = "manual"):
        if initial_mode not in BACKEND_MODES:
            raise ValueError(f"mode must be one of {BACKEND_MODES}")
        self.session = session
        self._jog_feed = jog_feed_mm_min
        self.mode = initial_mode

    def advance(self, dt_s: float = 0.0) -> GantryPose:
        """Fold any new lines from the MCU in and return the latest real pose. (The
        JobRunner pumps the same session; pumping twice is harmless.)"""
        try:
            self.session.pump()
        except Exception:  # noqa: BLE001 - a dead link shows as UNKNOWN state, not a crash
            pass
        pos = self.session.pos
        return GantryPose(
            x=pos.x if pos else 0.0, y=pos.y if pos else 0.0, z=pos.z if pos else 0.0,
            vacuum_on=self.session.vacuum,
            machine_state=self._display_state(),
            mode=self.mode,
        )

    def _display_state(self) -> str:
        if not self.session.connected:
            return "ALARM"
        return {"NEEDS_HOMING": "IDLE"}.get(self.session.fsm, self.session.fsm)

    def jog(self, axis: str, delta_mm: float) -> None:
        self._take_manual_control()
        self.session.send(protocol.jog(axis, delta_mm, self._jog_feed))

    def set_vacuum(self, on: bool) -> None:
        self._take_manual_control()
        self.session.send(protocol.vac(bool(on)))

    def set_mode(self, mode: str) -> None:
        if mode not in BACKEND_MODES:
            raise ValueError(f"mode must be one of {BACKEND_MODES} on a real gantry")
        self.mode = mode

    def _take_manual_control(self) -> None:
        # A jog/vacuum click is a manual action: it pauses the job runner (which
        # only acts in "jobs" mode) and takes manual control.
        if self.mode != "manual":
            self.mode = "manual"


def create_backend(config: dict) -> GantryBackend:
    """Build the backend named by config['backend'] ('mock' by default)."""
    kind = config.get("backend", "mock")
    if kind == "mock":
        return MockGantrySimulator(
            build_default_patrol(config),
            travel_speed_mm_s=config["mock"]["travel_speed_mm_s"],
            workspace=config["workspace"],
        )
    if kind == "sim":
        from pick_place.fake_mcu import LoopbackLink, SimulatedMcu

        ws = config["workspace"]
        mcu = SimulatedMcu(envelope=((ws["x_min_mm"], ws["x_max_mm"]), (ws["y_min_mm"], ws["y_max_mm"]),
                                     (ws["z_min_mm"], ws["z_max_mm"])),
                           speed_mm_s=float(config["mock"]["travel_speed_mm_s"]))
        return SerialGantryBackend(GantrySession(LoopbackLink(mcu, autotick=True)),
                                   jog_feed_mm_min=_jog_feed(config))
    if kind == "serial":
        from pick_place.serial_link import SerialGantryLink

        ser = (config.get("gantry") or {}).get("serial") or {}
        link = SerialGantryLink(ser.get("port", "/dev/ttyACM0"), int(ser.get("baud", 115200))).open()
        return SerialGantryBackend(GantrySession(link), jog_feed_mm_min=_jog_feed(config))
    raise ValueError(f"unknown backend {kind!r} (expected 'mock', 'sim' or 'serial')")


def _jog_feed(config: dict) -> float:
    return float(((config.get("gantry") or {}).get("feed_mm_min") or {}).get("approach_z", 1500.0))


def create_job_driver(backend, queue, model, feeds, config: dict,
                      on_job_done: Optional[Callable[[str, bool, Optional[str]], None]] = None):
    """The executor of queued tiles that matches `backend`."""
    if isinstance(backend, SerialGantryBackend):
        from pick_place.job_runner import JobRunner

        return JobRunner(backend.session, queue, model, feeds, config,
                         enabled=lambda: backend.mode == "jobs", on_job_done=on_job_done)
    from pick_place.mock_job_driver import MockJobDriver

    return MockJobDriver(backend, queue, model, feeds, on_job_done=on_job_done)


__all__ = ["GantryBackend", "SerialGantryBackend", "create_backend", "create_job_driver", "LinkError"]
