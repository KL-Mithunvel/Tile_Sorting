"""A clip is paired with ITS strike event by a token carried through the capture, not by arrival order.

Before 2026-10-19 the station kept the strike events in a queue and gave each clip the oldest one. But the hardware
trigger refuses a tap that arrives while it is capturing or cooling down (1.0 s), so an event could have no clip;
the next clip then took the stale event, and a real tile strike could be labelled `rest` or `safety` (or the other way
round) and lose its tile. The real HardwareTapDetector is used here, fed with synthetic audio.
"""

import queue
import time

import numpy as np
import pytest

from acoustic.demo import synthetic_strike
from acoustic.hardware_trigger import HardwareTapDetector
from acoustic.station import StationService
from fusion.store import TileStore
from fusion.transport import InMemoryBus

SR = 44100
BLOCK = 1323                                    # 30 ms


def blocks(signal):
    for i in range(0, len(signal) - BLOCK + 1, BLOCK):
        yield signal[i:i + BLOCK].astype(np.float32)


def quiet(seconds):
    return np.random.default_rng(1).normal(0, 0.0005, int(seconds * SR))


class DetectorCapture:
    """A capture with the real detector in it, fed by the test instead of a microphone."""

    def __init__(self, config=None):
        self.sample_rate = SR
        self.trigger_mode = "hardware"
        self.block_sink = None
        self.last_rms = 0.001
        self.last_block_monotonic = time.monotonic()
        self.d = HardwareTapDetector(SR, BLOCK, pre_trigger_ms=100, capture_duration_s=0.5, cooldown_s=1.0)
        self.q = queue.Queue()

    def start(self):
        pass

    def stop(self):
        pass

    def notify_tap(self, token=None):
        self.d.notify_tap(token)

    def feed(self, signal):
        for b in blocks(signal):
            self.last_block_monotonic = time.monotonic()
            clip = self.d.process_block(b)
            if clip is not None:
                self.q.put(clip)

    def get_clip_with_token(self, timeout=None):
        clip = self.q.get(timeout=timeout)
        return clip, self.d.pop_clip_token()

    def pop_dropped_tokens(self):
        return self.d.pop_dropped_tokens()


CONFIG = {
    "audio": {"sample_rate": SR, "device": None},
    "trigger": {"mode": "hardware", "rms_threshold": 0.02, "pre_trigger_ms": 100},
    "hardware_trigger": {"pre_trigger_ms": 100},
    "station": {
        "ledger": {"max_wait_s": 30},
        "final": {"vote_enabled": False, "min_confidence": 0.6, "bins": {"Grade A": "bin_1", "Reject": "bin_4", "Hold": "manual"}},
        "unoq": {"require_ack": False},
    },
}


def make():
    bus, store = InMemoryBus(), TileStore(":memory:")
    svc = StationService(dict(CONFIG), bus, store, capture_factory=DetectorCapture)
    svc.coordinator.start()
    svc._open_capture()
    bus.publish("station/camera/status", {"source_kind": "camera", "source_status": "running"}, qos=0)
    return svc, bus, svc._capture


def tap(bus, source="tof", present=True):
    bus.publish("station/tap", {"seq": 1, "tile_present": present, "tof_mm": 50, "nano_ms": 1, "source": source, "ts": time.time()})


def camera(bus, tid):
    bus.publish(f"tile/{tid}/camera/result", {"tile_id": tid, "grade": "Grade A", "seq": 1, "snapshot_path": None})


def process(svc, cap):
    """What the station's analysis thread does, until nothing is left."""
    out = []
    while True:
        step = svc.analysis_step(cap, timeout=0.01)
        out += [t for t in step if t]
        if not step and cap.q.empty() and not svc._dropped_pending:
            return out


def test_detector_hands_each_clip_the_token_of_the_tap_that_started_it():
    d = HardwareTapDetector(SR, BLOCK, 100, 0.5, 1.0)
    for b in blocks(quiet(0.3)):
        d.process_block(b)
    d.notify_tap("A")
    clip = None
    for b in blocks(synthetic_strike(SR)):
        clip = d.process_block(b) if clip is None else clip
    assert clip is not None and d.pop_clip_token() == "A" and d.pop_dropped_tokens() == []


def test_a_tap_during_a_capture_or_during_the_cooldown_is_reported_as_dropped_not_silently_lost():
    d = HardwareTapDetector(SR, BLOCK, 100, 0.5, 1.0)
    for b in blocks(quiet(0.3)):
        d.process_block(b)
    d.notify_tap("A")
    bs = list(blocks(synthetic_strike(SR)))
    d.process_block(bs[0])
    d.notify_tap("B")                                    # while A's capture is running
    clip = None
    for b in bs[1:]:
        out = d.process_block(b)
        clip = out if out is not None else clip
    assert clip is not None
    d.notify_tap("C")                                    # during the 1 s cooldown
    d.process_block(next(blocks(quiet(0.1))))
    assert d.pop_clip_token() == "A"
    assert d.pop_dropped_tokens() == ["B", "C"]
    assert d.pop_dropped_tokens() == []                  # draining clears them


def test_two_taps_before_the_next_audio_block_make_one_clip_and_one_dropped_token():
    d = HardwareTapDetector(SR, BLOCK, 100, 0.5, 1.0)
    d.notify_tap("A")
    d.notify_tap("B")
    d.process_block(next(blocks(quiet(0.1))))
    assert d.pop_dropped_tokens() == ["B"]


def test_a_tile_strike_after_a_refused_tap_is_still_paired_with_its_own_tile_and_event():
    svc, bus, cap = make()
    camera(bus, "T1")
    camera(bus, "T2")
    cap.feed(quiet(0.3))
    tap(bus, "tof")                                      # E1: a real tile strike
    cap.feed(synthetic_strike(SR))
    cap.feed(quiet(0.5))                                 # still cooling down
    tap(bus, "safety", present=False)                    # E2: refused by the detector (no clip)
    cap.feed(quiet(0.2))
    cap.feed(quiet(1.5))                                 # cooldown over
    tap(bus, "tof")                                      # E3: the next tile
    cap.feed(synthetic_strike(SR))
    cap.feed(quiet(0.3))
    bound = process(svc, cap)
    assert bound == ["T1", "T2"]                         # the old queue gave E3's clip to E2 and left T2 waiting
    finals = {f["tile_id"]: f for f in bus.messages("tile/+/final")}
    assert set(finals) == {"T1", "T2"}
    assert svc.coordinator.snapshot()["counters"].get("safety_strikes") == 1       # E2 was counted, not lost


def test_a_tile_strike_the_microphone_could_not_record_still_uses_its_tap_slot():
    svc, bus, cap = make()
    camera(bus, "T1")
    camera(bus, "T2")
    cap.feed(quiet(0.3))
    tap(bus, "tof")
    cap.feed(synthetic_strike(SR))
    cap.feed(quiet(0.3))
    tap(bus, "tof")                                      # refused: the detector is cooling down
    cap.feed(quiet(0.3))
    bound = process(svc, cap)
    assert bound == ["T1", "T2"]                         # T1 from its clip, then T2 from the strike with no recording
    snap = svc.coordinator.snapshot()
    finals = {f["tile_id"]: f for f in bus.messages("tile/+/final")}
    assert set(finals) == {"T1", "T2"}                   # T2 went on camera grade alone, not left waiting for a timeout
    assert finals["T2"]["acoustic_counted"] is False
    assert snap["awaiting_count"] == 0


def test_legacy_callers_without_an_id_still_get_the_oldest_event():
    svc, bus, cap = make()
    camera(bus, "T1")
    tap(bus, "tof")
    assert svc.handle_clip(synthetic_strike(SR)) == "T1"          # no event_id: the oldest unpaired event


def test_pop_tap_event_by_id_and_expiry():
    svc, bus, cap = make()
    tap(bus, "manual")
    tap(bus, "tof")
    ids = sorted(svc.coordinator._tap_events)
    assert svc.coordinator.pop_tap_event(ids[1])["source"] == "tof"          # out of order is fine
    assert svc.coordinator.pop_tap_event(ids[1]) is None                      # and only once
    assert svc.coordinator.pop_tap_event(ids[0])["source"] == "manual"
    assert svc.coordinator.pop_tap_event() is None
