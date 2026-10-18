"""Laptop run of the encoder_smoke_test GUI with NO board: a simulated sketch
emits the same 'ENC ...' status lines and obeys move/stop/scan/zero, so the
real parser, pass/fail logic and page can be exercised.

    python sim_run.py [--fault none|dead_y2|crossed|noisy_z] [--port 5071]

Simulates what the sketch should do, not what the board does -- it proves the
Python side and GUI, not the firmware or the wiring. Move speed is shortened
(MOVE_LEG_S per leg) so a test takes seconds.
"""

from __future__ import annotations

import argparse
import random
import threading
import time

from bridge_backend import SharedState, load_config
from dashboard import create_app
from enc_protocol import parse_status_line

MOVE_LEG_S = 2.0
SETTLE_S = 0.5
AXIS_CHANNELS = [[0], [1, 2], [3]]


class SimEncBackend:
    def __init__(self, config: dict, state: SharedState, fault: str):
        self._state, self._fault = state, fault
        self._poll = config["bridge"]["poll_interval_s"]
        self._lock = threading.Lock()
        self._pos = [0.0] * 4            # running position, degrees
        self._raw_origin = [random.uniform(0, 360) for _ in range(4)]
        self._errors = [0] * 4
        self._fails = [0] * 4
        self._fwd = [0.0] * 4
        self._net = [0.0] * 4
        self._start = [0.0] * 4
        self._seq = 0
        self._axis = -1
        self._st = 0
        self._phase_t0 = 0.0
        self._phase = "idle"
        self._scan = None
        self._running = False

    # ---- backend interface (same as BridgeEncBackend) ----
    def start(self):
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._running = False

    def move(self, axis: int):
        if not 0 <= axis < 3:
            raise ValueError("axis must be 0..2")
        with self._lock:
            if self._phase != "idle":
                return
            self._axis, self._st, self._phase, self._phase_t0 = axis, 1, "fwd", time.monotonic()
            self._start = list(self._pos)
            self._fails = [0] * 4
            self._fwd = [0.0] * 4
            self._net = [0.0] * 4

    def halt(self):
        with self._lock:
            if self._phase != "idle":
                self._phase, self._st = "idle", 3

    def scan(self):
        self._scan = [1, 1, 0 if self._fault == "dead_y2" else 1, 1, 2, 0, 0, 0]
        if self._fault == "dead_y2":
            self._scan[2] = 0
        self._scan[1] = 1

    def mux_reset(self):
        pass

    def zero(self):
        with self._lock:
            self._pos = [0.0] * 4

    # ---- simulated sketch ----
    def _driven(self):
        return AXIS_CHANNELS[self._axis] if self._axis >= 0 else []

    def _tick(self):
        now = time.monotonic()
        dt = now - self._phase_t0
        if self._phase in ("fwd", "rev"):
            frac = min(dt / MOVE_LEG_S, 1.0)
            sign = 1 if self._phase == "fwd" else -1
            base = 360.0 * frac if self._phase == "fwd" else 360.0 * (1 - frac)
            for ch in self._driven():
                target = self._target_channel(ch)
                if target is None:
                    continue
                self._pos[target] = self._start[target] + base * (-1 if ch == 2 else 1) + random.uniform(-0.4, 0.4)
            if self._fault == "noisy_z" and 3 in self._driven() and random.random() < 0.05:
                self._fails[3] += 1
                self._errors[3] += 1
            if frac >= 1.0:
                if self._phase == "fwd":
                    self._fwd = [self._pos[c] - self._start[c] for c in range(4)]
                self._phase, self._phase_t0 = ("settle1" if self._phase == "fwd" else "settle2"), now
        elif self._phase in ("settle1", "settle2") and dt >= SETTLE_S:
            if self._phase == "settle1":
                self._phase, self._phase_t0 = "rev", now
            else:
                self._net = [self._pos[c] - self._start[c] for c in range(4)]
                self._phase, self._st = "idle", 2
                self._seq += 1

    def _target_channel(self, ch: int):
        if self._fault == "dead_y2" and ch == 2:
            return None                      # motor turns, encoder does not follow
        if self._fault == "crossed" and ch == 1:
            return 3                         # Y move shows up on Z's encoder
        return ch

    def _status_line(self) -> str:
        parts = [f"ENC ST={self._st} AX={self._axis} SEQ={self._seq} MUX=1 DRV={1 if self._phase != 'idle' else 0}"]
        for n in range(4):
            raw = int(((self._raw_origin[n] + self._pos[n]) % 360.0) / 360.0 * 4096) % 4096
            parts.append(f"A{n}={raw} P{n}={int(self._pos[n] * 10)} S{n}=32 E{n}={self._errors[n]} "
                         f"F{n}={int(self._fwd[n] * 10)} N{n}={int(self._net[n] * 10)} M{n}={self._fails[n]}")
        parts.append("SC=" + ("-" if self._scan is None else ",".join(str(v) for v in self._scan)))
        return " ".join(parts)

    def _loop(self):
        while self._running:
            with self._lock:
                self._tick()
                line = self._status_line()
            status = parse_status_line(line)
            if status is not None:
                self._state.update(status)
            time.sleep(self._poll)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fault", default="none", choices=["none", "dead_y2", "crossed", "noisy_z"])
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()
    config = load_config()
    state = SharedState(config)
    backend = SimEncBackend(config, state, args.fault)
    backend.start()
    app = create_app(state, backend, config)
    app.run(host="127.0.0.1", port=args.port or config["dashboard"]["port"])


if __name__ == "__main__":
    main()
