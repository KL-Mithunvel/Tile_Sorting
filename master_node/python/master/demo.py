"""A pretend production line, so the master's pages can be run and looked at on
a laptop with no Pi, broker or hardware (`python -m master.live_master --demo`).

It speaks the station's message contract on an in-memory bus: heartbeats,
graded tiles, sound results, final grades, acks, now and then an alarm - and
it obeys the master's commands, so the Control panel visibly does something.
Everything here is synthetic. It is not evidence about the real line, and
nothing in it is used in production.
"""

from __future__ import annotations

import random
import threading
import time

GRADES = ("Grade A", "Grade B", "Reject")
BINS = {"Grade A": "bin_1", "Grade B": "bin_2", "Grade C": "bin_3", "Reject": "bin_reject", "Hold": "bin_hold"}


class DemoLine(threading.Thread):
    def __init__(self, bus, interval_s: float = 4.0, transit_s: float = 2.5, fault_every_s: float = 50.0,
                 fault_for_s: float = 9.0, seed=None):
        super().__init__(daemon=True, name="demo-line")
        self._bus = bus
        self._interval, self._transit = interval_s, transit_s
        self._fault_every, self._fault_for = fault_every_s, fault_for_s
        self._rng = random.Random(seed)
        self._halt = threading.Event()
        self._lock = threading.Lock()
        # what the commands change
        self.hold_all = False
        self.vote_enabled = False
        self.strike_enabled = True
        self.belt_pct = 60.0
        self.line_stopped = False
        self._saved: dict = {}
        self.tally: dict[str, int] = {}
        self._n = 0
        self._awaiting: list[dict] = []
        self._camera_fault_until = 0.0
        self._last_fault = time.time()
        self._last_tile = 0.0
        self._last_beat = 0.0
        bus.subscribe("station/cmd", self._on_cmd)
        bus.subscribe("station/belt", self._on_belt)
        bus.subscribe("station/strike", self._on_strike)

    def stop(self) -> None:
        self._halt.set()

    # ---- commands from the master -----------------------------------------

    @staticmethod
    def _json(payload: bytes) -> dict:
        import json
        try:
            d = json.loads(payload)
        except ValueError:
            return {}
        return d if isinstance(d, dict) else {}

    def _on_cmd(self, topic: str, payload: bytes) -> None:
        cmd = self._json(payload)
        with self._lock:
            name = cmd.get("cmd")
            if name == "hold_all":
                self.hold_all = bool(cmd.get("enabled"))
            elif name == "vote":
                self.vote_enabled = bool(cmd.get("enabled"))
            elif name == "clear_alarms":
                pass  # the master's own record is what it shows; nothing to reset here
            elif name == "line":
                self._line(cmd.get("state"), cmd.get("belt_percent"))

    def _line(self, state, pct) -> None:
        """Same semantics as the real coordinator's set_line (lock already held)."""
        if state == "stop":
            if not self.line_stopped:
                self._saved = {"hold_all": self.hold_all, "belt": self.belt_pct if self.belt_pct > 0 else None}
                self.line_stopped = True
            self.hold_all, self.belt_pct, self.strike_enabled = True, 0.0, False
        elif state == "start":
            pct = pct if pct is not None else self._saved.get("belt")
            if pct is None or isinstance(pct, bool) or not 0 < pct <= 100:
                return
            self.hold_all = bool(self._saved.get("hold_all", False))
            self.line_stopped, self.belt_pct, self.strike_enabled = False, float(pct), True
            self._saved = {}

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

    # ---- the line ---------------------------------------------------------

    def run(self) -> None:
        while not self._halt.is_set():
            now = time.time()
            with self._lock:
                self._step(now)
            self._halt.wait(0.25)

    def _step(self, now: float) -> None:
        faulty = now < self._camera_fault_until
        if not faulty and now - self._last_fault >= self._fault_every:
            self._last_fault = now
            self._camera_fault_until = now + self._fault_for
            faulty = True
            self._bus.publish("machine/alarm", {
                "ts": now, "level": "alarm", "kind": "camera_silent",
                "message": "demo: the camera service stopped sending heartbeats", "tile_id": None}, qos=1)

        belt_running = self.belt_pct > 0
        if not faulty:
            self._bus.publish("station/camera/status", {
                "source_kind": "camera", "source_status": "running", "tile_count": self._n, "ts": now}, qos=0)
        self._bus.publish("station/nano/status", {
            "state": "online", "link": True, "belt_pct": self.belt_pct, "ts": now}, qos=0)

        if belt_running and not faulty and now - self._last_tile >= self._interval:
            self._last_tile = now
            self._n += 1
            tid = f"DEMO-{self._n:05d}"
            grade = self._rng.choices(GRADES, weights=[7, 2, 1])[0]
            self._bus.publish(f"tile/{tid}/camera/result", {
                "tile_id": tid, "seq": self._n, "station": "camera", "status": "done",
                "grade": grade, "rule_grade": grade, "ts": now})
            self._awaiting.append({"tile_id": tid, "camera": grade, "due": now + self._transit})

        while self._awaiting and now >= self._awaiting[0]["due"]:
            t = self._awaiting.pop(0)
            self._finish(t, now)

        if now - self._last_beat >= 1.0:
            self._last_beat = now
            self._beat(now, faulty)

    def _finish(self, t: dict, now: float) -> None:
        tid, cam = t["tile_id"], t["camera"]
        if self.strike_enabled:
            sound = cam if cam != "Reject" else "Reject"
            self._bus.publish(f"tile/{tid}/acoustic/result", {
                "tile_id": tid, "station": "acoustic", "status": "done",
                "acoustic_grade": sound, "peak_frequency_hz": round(self._rng.gauss(2800, 90), 1), "ts": now})
            reasons = [f"camera: {cam}", "sound: " + ("counted" if self.vote_enabled else "not counted")]
            final = cam
        else:
            reasons, final = [f"camera: {cam}", "striker inhibited"], cam
        if self.hold_all:
            final, reasons = "Hold", ["hold-all is on"]
        self.tally[final] = self.tally.get(final, 0) + 1
        self._bus.publish(f"tile/{tid}/final", {
            "tile_id": tid, "station": "decision", "final_grade": final, "destination": BINS.get(final),
            "camera_grade": cam, "acoustic_counted": self.vote_enabled and self.strike_enabled,
            "timed_out": False, "reasons": reasons, "ts": now})
        self._bus.publish(f"tile/{tid}/ack", {"status": "ok"})

    def _beat(self, now: float, camera_faulty: bool) -> None:
        health = {
            "camera": ({"level": "fail", "detail": f"silent for {self._fault_for:.0f} s (demo fault)"}
                       if camera_faulty else {"level": "ok", "detail": f"{self._n} tiles"}),
            "mic": {"level": "ok", "detail": "synthetic microphone"},
            "nano": {"level": "ok", "detail": f"belt {self.belt_pct:.0f} %"},
            "unoq": {"level": "ok", "detail": "demo acknowledgements"},
        }
        if camera_faulty:
            state, detail = "fault", "camera: demo fault"
        elif self.line_stopped:
            state, detail = "line_stopped", "line stopped (soft stop from the master - not an e-stop)"
        elif self.hold_all:
            state, detail = "hold_all", "every tile is being held for a person"
        elif self._awaiting:
            state, detail = "awaiting_tap", f"{len(self._awaiting)} tile(s) waiting for a tap"
        else:
            state, detail = "waiting", "waiting for a tile"
        self._bus.publish("station/status", {
            "now": now, "state": state, "state_detail": detail, "health": health,
            "awaiting_count": len(self._awaiting),
            "settings": {"vote_enabled": self.vote_enabled, "hold_all": self.hold_all,
                         "line": "stopped" if self.line_stopped else "running",
                         "strike_enabled": self.strike_enabled},
            "tally": dict(self.tally), "counters": {}}, qos=0, retain=True)
