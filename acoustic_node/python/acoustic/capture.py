"""Microphone capture with a rolling RMS trigger.

Split into two pieces on purpose:

- `TriggerDetector` is pure logic: feed it blocks of audio, it hands back a
  finished clip when a trigger fires. It never touches `sounddevice`, so it
  can be unit-tested with synthetic blocks on any machine.
- `AudioCapture` is the hardware wrapper: opens the mic via `sounddevice` and
  feeds real audio blocks into a `TriggerDetector`, one callback at a time.

This mirrors the dev-machine-first rule in CLAUDE-COMMON.md — the part that
needs real hardware is as thin as possible around the part that doesn't.
"""

from __future__ import annotations

import math
import queue
from collections import deque
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd
import yaml

from acoustic.signal_processing import compute_rms


def load_config(path: Optional[Path] = None) -> dict:
    if path is None:
        path = Path(__file__).parent / "config.yaml"
    with open(path, "r") as f:
        return yaml.safe_load(f)


def list_input_devices() -> list[dict]:
    """Return sounddevice's device table, input-capable devices only."""
    devices = sd.query_devices()
    return [d for d in devices if d["max_input_channels"] > 0]


class TriggerDetector:
    """Stateful RMS-threshold trigger with a pre-trigger buffer.

    Call `process_block()` once per incoming audio block. Returns the
    finished clip (as a 1-D float32 numpy array) the moment a capture
    completes, otherwise returns None.
    """

    def __init__(
        self,
        sample_rate: int,
        block_size: int,
        rms_threshold: float,
        pre_trigger_ms: float,
        capture_duration_s: float,
        cooldown_s: float,
    ):
        self._rms_threshold = rms_threshold
        self._capture_samples_target = int(capture_duration_s * sample_rate)

        pre_trigger_blocks = max(1, math.ceil((pre_trigger_ms / 1000.0) * sample_rate / block_size))
        cooldown_blocks = math.ceil((cooldown_s * sample_rate) / block_size)

        self._pretrigger: deque[np.ndarray] = deque(maxlen=pre_trigger_blocks)
        self._cooldown_blocks = cooldown_blocks
        self._cooldown_remaining = 0

        self._capturing = False
        self._capture_blocks: list[np.ndarray] = []

    @property
    def is_capturing(self) -> bool:
        return self._capturing

    def process_block(self, block: np.ndarray) -> Optional[np.ndarray]:
        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1

        rms = compute_rms(block)

        if not self._capturing:
            self._pretrigger.append(block)
            if rms >= self._rms_threshold and self._cooldown_remaining == 0:
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


class AudioCapture:
    """Opens the microphone and pushes triggered clips onto a queue."""

    def __init__(self, config: dict):
        audio_cfg = config["audio"]
        trigger_cfg = config["trigger"]

        self.sample_rate: int = audio_cfg["sample_rate"]
        self.device = audio_cfg["device"]
        self.channels: int = audio_cfg["channels"]
        self.block_size: int = max(1, int(audio_cfg["block_size_ms"] / 1000.0 * self.sample_rate))

        self._detector = TriggerDetector(
            sample_rate=self.sample_rate,
            block_size=self.block_size,
            rms_threshold=trigger_cfg["rms_threshold"],
            pre_trigger_ms=trigger_cfg["pre_trigger_ms"],
            capture_duration_s=trigger_cfg["capture_duration_s"],
            cooldown_s=trigger_cfg["cooldown_s"],
        )

        self.clip_queue: "queue.Queue[np.ndarray]" = queue.Queue()
        self._stream: Optional[sd.InputStream] = None
        self.last_rms: float = 0.0

    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            print(f"[acoustic.capture] stream status: {status}")

        block = indata[:, 0].copy() if indata.ndim > 1 else indata.copy()
        self.last_rms = compute_rms(block)

        clip = self._detector.process_block(block)
        if clip is not None:
            self.clip_queue.put(clip)

    def start(self) -> None:
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            device=self.device,
            blocksize=self.block_size,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def get_clip(self, timeout: Optional[float] = None) -> np.ndarray:
        """Block until a triggered clip is available."""
        return self.clip_queue.get(timeout=timeout)

    def __enter__(self) -> "AudioCapture":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()
