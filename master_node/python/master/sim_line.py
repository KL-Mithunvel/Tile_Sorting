"""A sim-backed line for the master's pages (`python -m master.live_master --sim`).

`demo.py`'s DemoLine PRETENDS to be the station: it publishes plausible messages
and obeys commands the way its author thought the line would. This runs the
real thing on the laptop instead:

    REAL    fusion.StationCoordinator   ledger, final grade, outbox/acks, health,
                                        line stop/start, feed gate, alarms
    REAL    pick_place FeedClient -> FinalQueue -> JobRunner -> GantrySession
                                        over the protocol's SimulatedMcu (the
                                        executable spec of the firmware), plus
                                        the reporter (placed / status / alarms)
                                        and the remote-command handler
    FAKE    camera (a graded tile every few seconds), the Nano (belt percent,
            striker enable) and the microphone (a good tap, shortly after)

so what the 3D page, Troubleshoot and Analysis show is what the station logic
and the gantry runner actually do - including things nobody scripted, e.g. what
the gantry does with a "manual" (Hold) destination.

Dev tool. The master normally never imports the station's packages (it runs on
another machine); this module adds them to sys.path itself, only when `--sim` is
asked for, and nothing else in `master/` imports it. Still SIMULATED: no firmware,
no timing, no vacuum, no real broker is exercised.
"""

from __future__ import annotations

import random
import sys
import threading
import time
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
GRADES = ("Grade A", "Grade B", "Reject")
GRADE_WEIGHTS = (7, 2, 1)


def _import_station_packages() -> None:
    for rel in ("acoustic_node/python", "pick_place_node/python"):
        p = str(REPO_ROOT / rel)
        if p not in sys.path:
            sys.path.append(p)


class SimLine:
    def __init__(self, bus, interval_s: float = 6.0, transit_s: float = 2.5,
                 gantry_speed_mm_s: float = 400.0, real_feeds: bool = False, home_on_start: bool = True,
                 seed=None):
        """`real_feeds=False`: the simulated MCU moves at `gantry_speed_mm_s` whatever each command's F
        word says, so the demo looks alive. `real_feeds=True` honours the configured feed rates; at
        today's placeholder config one tile then takes ~22 s, i.e. the gantry is the bottleneck of a
        line that grades a tile every few seconds (a finding about the placeholders, not the machine)."""
        _import_station_packages()
        import yaml
        from fusion.coordinator import OK, StationCoordinator
        from fusion.store import TileStore
        from pick_place.coordinate_model import CoordinateModel
        from pick_place.gantry_station import attach_station_link
        from pick_place.mock_state import load_config as load_gantry_config
        from pick_place.sequencer import Feeds
        from pick_place.worker import SharedState, TwinWorker

        self._bus = bus
        self._interval, self._transit = interval_s, transit_s
        self._rng = random.Random(seed)
        self._stop = threading.Event()
        self._lock = threading.Lock()

        # ---- the Pi: the real station coordinator, on the station's own config ----
        with open(REPO_ROOT / "acoustic_node/python/acoustic/config.yaml", encoding="utf-8") as f:
            station_cfg = yaml.safe_load(f)["station"]
        self.pi = StationCoordinator(station_cfg, bus, TileStore(":memory:"))
        self.pi.start()
        self.pi.set_health("mic", OK, "listening (simulated)")

        # ---- the UNO Q: the real feed client + job runner over the simulated MCU ----
        gcfg = load_gantry_config()
        from pick_place.fake_mcu import LoopbackLink, SimulatedMcu
        from pick_place.gantry_backend import SerialGantryBackend
        from pick_place.gantry_session import GantrySession

        gcfg["backend"] = "sim"
        st = gcfg.setdefault("gantry", {}).setdefault("station", {})
        st["home_on_start"] = bool(home_on_start)     # simulated machine: homing is safe
        ws = gcfg["workspace"]
        self.mcu = SimulatedMcu(
            envelope=((ws["x_min_mm"], ws["x_max_mm"]), (ws["y_min_mm"], ws["y_max_mm"]),
                      (ws["z_min_mm"], ws["z_max_mm"])),
            speed_mm_s=float(gantry_speed_mm_s), honor_feed=bool(real_feeds))
        jog = float(((gcfg.get("gantry") or {}).get("feed_mm_min") or {}).get("approach_z", 1500.0))
        self.backend = SerialGantryBackend(GantrySession(LoopbackLink(self.mcu, autotick=True)), jog_feed_mm_min=jog)
        self.model = CoordinateModel.from_config(gcfg)
        self.link = attach_station_link(gcfg, bus, self.backend, self.model, Feeds.from_config(gcfg),
                                        "sim", clock=time.time)
        self._worker = TwinWorker(self.backend, SharedState(), tick_hz=gcfg["dashboard"]["tick_hz"],
                                  driver=self.link.driver, on_tick=self.link.tick_hook())

        # ---- stand-ins: the Nano and the striker/microphone ----
        self.belt_pct = 60.0
        self.strike_enabled = True
        bus.subscribe("station/belt", self._on_belt)
        bus.subscribe("station/strike", self._on_strike)

        self._n = 0
        self._last_tile = 0.0
        self._pending_taps: list[float] = []     # due times
        self._last_beat = 0.0
        self._thread = threading.Thread(target=self._loop, daemon=True, name="sim-line")

    # ---- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._worker.start()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)
        self._worker.stop()
        try:
            self.link.reporter.close()
        except Exception:  # noqa: BLE001
            pass

    # ---- the Nano ------------------------------------------------------------

    @staticmethod
    def _json(payload: bytes) -> dict:
        import json
        try:
            d = json.loads(payload)
        except ValueError:
            return {}
        return d if isinstance(d, dict) else {}

    def _on_belt(self, topic: str, payload: bytes) -> None:
        pct = self._json(payload).get("percent")
        if isinstance(pct, (int, float)) and not isinstance(pct, bool) and 0 <= pct <= 100:
            with self._lock:
                self.belt_pct = float(pct)

    def _on_strike(self, topic: str, payload: bytes) -> None:
        en = self._json(payload).get("enabled")
        if isinstance(en, bool):
            with self._lock:
                self.strike_enabled = en

    # ---- the loop ----------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            try:
                self._step(now)
            except Exception:  # noqa: BLE001 - a demo driver must not die on one bad tick
                import logging
                logging.getLogger(__name__).exception("sim line step failed")
            self._stop.wait(0.2)

    def _step(self, now: float) -> None:
        with self._lock:
            belt, strike = self.belt_pct, self.strike_enabled
        if now - self._last_beat >= 1.0:
            self._last_beat = now
            self._bus.publish("station/nano/status",
                              {"state": "online", "link": True, "belt_pct": belt, "ts": now}, qos=0, retain=True)
            self._bus.publish("station/camera/status",
                              {"source_kind": "camera", "source_status": "running", "tile_count": self._n, "ts": now},
                              qos=0)
        if belt > 0 and now - self._last_tile >= self._interval:
            self._last_tile = now
            self._n += 1
            tid = f"S{self._n:05d}"
            grade = self._rng.choices(GRADES, weights=GRADE_WEIGHTS)[0]
            self._bus.publish(f"tile/{tid}/camera/result", {
                "tile_id": tid, "seq": self._n, "station": "camera", "status": "done", "grade": grade,
                "rule_grade": grade, "crack_detected": grade != "Grade A", "corner_broken": grade == "Reject",
                "ts": now})
            self._pending_taps.append(now + self._transit)
        # A tap only happens while the belt moves and the striker is allowed. A tile the striker
        # never hits is left to the Pi's own no-tap timeout - the real path, not a scripted one.
        while self._pending_taps and now >= self._pending_taps[0] and belt > 0:
            self._pending_taps.pop(0)
            if strike:
                self.pi.on_tap({"valid": True, "grade": None, "confidence": None,
                                "peak_frequency_hz": round(self._rng.gauss(2800, 90), 1)})
        self.pi.tick(now)
