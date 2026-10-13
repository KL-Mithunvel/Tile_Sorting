"""The UNO Q's station service: the gantry as one more node on the station bus.

    python -m pick_place.gantry_station --host 192.168.1.11            # config.yaml's backend
    python -m pick_place.gantry_station --host 192.168.1.11 --backend sim

It receives each tile's final from the Pi (`tile_feed.FeedClient`), runs the
queue on the gantry (`JobRunner` over a serial/simulated MCU, or the mock
driver), and reports back (`station_link.GantryReporter`): per-tile `placed`,
retained `station/gantry/status` with a last-will, `machine/alarm` edges, and
the remote commands (`GantryCommands`). No dashboard: for the 3D twin run
`pick_place.twin_prototype --station-host ...`, which attaches the same link.

`attach_station_link()` is the one place that wires reporter + commands to a
driver, so the headless service, the twin and the tests cannot drift apart.

Never run on the UNO Q. Needs paho-mqtt there (unconfirmed for the App Lab
container) and, for `--backend serial`, a working MCU link and firmware.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from dataclasses import dataclass
from typing import Optional

from pick_place.coordinate_model import CoordinateModel
from pick_place.gantry_backend import create_backend, create_job_driver
from pick_place.mock_state import load_config
from pick_place.sequencer import Feeds
from pick_place.station_link import TOPIC_STATUS, GantryCommands, GantryReporter
from pick_place.tile_feed import FeedClient, PahoFeedBus
from pick_place.worker import SharedState, TwinWorker

log = logging.getLogger(__name__)


@dataclass
class StationLink:
    feed: FeedClient
    driver: object
    reporter: GantryReporter
    commands: GantryCommands
    home_on_start: bool = False
    _home_requested: bool = False

    def _maybe_home(self) -> None:
        """`gantry.station.home_on_start`: ask for ONE homing run once the config has
        been pushed. Off by default - homing moves the machine, so it is opt-in and
        the operator is expected to know the gantry is clear."""
        if not self.home_on_start or self._home_requested:
            return
        request = getattr(self.driver, "request_home", None)
        if request is None:
            self._home_requested = True
        elif request():
            self._home_requested = True

    def tick_hook(self, min_interval_s: float = 0.25, clock=time.monotonic):
        """A callable for TwinWorker(on_tick=...): the reporter checks for changes a
        few times a second, not at the simulator's 30 Hz."""
        last = [float("-inf")]

        def hook() -> None:
            now = clock()
            if now - last[0] >= min_interval_s:
                last[0] = now
                self._maybe_home()
                self.reporter.tick()

        return hook


def station_settings(config: dict) -> dict:
    s = (config.get("gantry") or {}).get("station") or {}
    return {
        "host": s.get("host", "192.168.1.11"),
        "port": int(s.get("port", 1883)),
        "client_id": s.get("client_id", "tile-gantry"),
        "status_interval_s": float(s.get("status_interval_s", 2.0)),
        "commands_enabled": bool(s.get("commands_enabled", True)),
        "motion_commands_enabled": bool(s.get("motion_commands_enabled", False)),
        "home_on_start": bool(s.get("home_on_start", False)),
    }


def attach_station_link(config: dict, bus, backend, model: CoordinateModel, feeds: Feeds,
                        backend_name: str, clock=time.time, queue=None) -> StationLink:
    """Wire the gantry to `bus`: finals in, placed / status / alarms / commands out.

    `bus` needs publish/subscribe (an InMemoryBus in tests, `PahoFeedBus` for real);
    a bus with `set_will` gets the offline last-will. Call before `bus.start()`.
    """
    st = station_settings(config)
    feed = FeedClient(bus, queue=queue, clock=clock)
    reporter = GantryReporter(bus, None, model, feed.queue, backend=backend, backend_name=backend_name,
                              clock=clock, status_interval_s=st["status_interval_s"])
    driver = create_job_driver(backend, feed.queue, model, feeds, config, on_job_done=reporter.on_job_done)
    reporter.bind(driver)
    commands = GantryCommands(bus, driver, backend, model, reporter,
                              commands_enabled=st["commands_enabled"],
                              motion_commands_enabled=st["motion_commands_enabled"], clock=clock)
    commands.bind(driver)
    if hasattr(bus, "set_will"):
        bus.set_will(TOPIC_STATUS, {"state": "offline", "level": "fail"}, retain=True)
    if hasattr(bus, "on_connect"):
        # A retained status is replaced by whatever the last-will left behind
        # after a drop; say we are back as soon as we are.
        bus.on_connect(lambda: reporter.publish_status(force=True))
    feed.start()
    commands.attach()
    # Start in the jobs mode so queued tiles are picked; jog / pause in the twin
    # (or a `pause` command) takes the gantry back to manual.
    try:
        backend.set_mode("jobs")
    except Exception:  # noqa: BLE001 - a backend with no modes
        pass
    return StationLink(feed, driver, reporter, commands, home_on_start=st["home_on_start"])


def main(argv: Optional[list] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Gantry station service (UNO Q): finals in, outcomes out.")
    parser.add_argument("--host", default=None, help="the Pi's MQTT broker (default gantry.station.host)")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--backend", choices=("mock", "sim", "serial"), default=None,
                        help="override config.yaml `backend`")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    config = load_config()
    if args.backend:
        config["backend"] = args.backend
    st = station_settings(config)
    host, port = args.host or st["host"], args.port or st["port"]

    try:
        backend = create_backend(config)
    except Exception as exc:  # noqa: BLE001 - a missing serial port etc.
        log.error("could not start the %s backend: %s", config.get("backend", "mock"), exc)
        return 2
    model = CoordinateModel.from_config(config)
    bus = PahoFeedBus(host, port, client_id=st["client_id"])
    link = attach_station_link(config, bus, backend, model, Feeds.from_config(config),
                               config.get("backend", "mock"))
    worker = TwinWorker(backend, SharedState(), tick_hz=config["dashboard"]["tick_hz"], driver=link.driver,
                        on_tick=link.tick_hook())
    bus.start()
    worker.start()
    log.info("gantry station up: backend=%s broker=%s:%s commands=%s motion_commands=%s",
             config.get("backend", "mock"), host, port, st["commands_enabled"], st["motion_commands_enabled"])

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: stop.set())
        except (ValueError, OSError):  # not the main thread / unsupported on this platform
            pass
    try:
        while not stop.wait(0.5):
            pass
    finally:
        worker.stop()
        link.reporter.close()
        time.sleep(0.3)  # let the retained "offline" leave before the socket closes
        bus.stop()
        log.info("gantry station stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
