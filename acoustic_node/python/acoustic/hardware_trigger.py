"""Pre-trigger ring buffer keyed on an external tap event, not an RMS crossing.

Companion to acoustic/capture.py's TriggerDetector: same shape (feed it audio
blocks, get a finished clip back), but the moment of capture is decided by
whoever calls `notify_tap()` — the acoustic station's ToF-triggered ball-drop
tap (see tap_sequencer.py / acoustic_node/sketch/sketch.ino), not by the audio
signal crossing a threshold. Used when acoustic/config.yaml's trigger.mode is
"hardware" (real ToF + solenoids) or "simulated" (dev-machine, no hardware —
see acoustic/live_monitor.py). TriggerDetector (RMS-based) remains the mode for
plain mic-only dev-machine testing with no ToF/solenoid hardware attached at
all.

Pure logic, no I/O — unit-testable with synthetic blocks the same way
TriggerDetector is.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from typing import Optional

import numpy as np


class HardwareTapDetector:
    """Call `process_block()` once per incoming audio block (same contract as
    TriggerDetector), and `notify_tap()` the instant a tap event is known to
    have fired. `notify_tap()` is expected to be called from a different
    thread than `process_block()` (e.g. a bridge-polling loop vs. the audio
    callback thread) — a lock guards the handoff.
    """

    def __init__(
        self,
        sample_rate: int,
        block_size: int,
        pre_trigger_ms: float,
        capture_duration_s: float,
        cooldown_s: float,
    ):
        self._capture_samples_target = int(capture_duration_s * sample_rate)

        pre_trigger_blocks = max(1, math.ceil((pre_trigger_ms / 1000.0) * sample_rate / block_size))
        cooldown_blocks = math.ceil((cooldown_s * sample_rate) / block_size)

        self._pretrigger: deque[np.ndarray] = deque(maxlen=pre_trigger_blocks)
        self._cooldown_blocks = cooldown_blocks
        self._cooldown_remaining = 0

        self._capturing = False
        self._capture_blocks: list[np.ndarray] = []

        self._lock = threading.Lock()
        self._tap_pending = False

    @property
    def is_capturing(self) -> bool:
        return self._capturing

    def notify_tap(self) -> None:
        with self._lock:
            self._tap_pending = True

    def _consume_tap_pending(self) -> bool:
        with self._lock:
            pending = self._tap_pending
            self._tap_pending = False
        return pending

    def process_block(self, block: np.ndarray) -> Optional[np.ndarray]:
        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1

        tap_pending = self._consume_tap_pending()

        if not self._capturing:
            self._pretrigger.append(block)
            if tap_pending and self._cooldown_remaining == 0:
                self._capturing = True
                self._capture_blocks = list(self._pretrigger)
            return None

        self._capture_blocks.append(block)
        total_samples = sum(b.size for b in self._capture_blocks)
        if total_samples < self._capture_samples_target:
            return None

        clip = np.concatenate(self._capture_blocks)[: self._capture_samples_target]
        self._capturing = False
        self._capture_blocks = []
        self._pretrigger.clear()
        self._cooldown_remaining = self._cooldown_blocks
        return clip
