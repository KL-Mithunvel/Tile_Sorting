"""The acoustic station service: microphone -> tap features -> fusion.

Hardware + threads around already-tested pure parts (`acoustic.features`,
`fusion.*`), same split as camera/worker.py. Three threads:

- audio callback (PortAudio's, inside AudioCapture) — fills the clip queue;
- analysis — takes a finished clip, measures it, hands the result to the
  coordinator (which binds it to the waiting tile and decides);
- tick — expiry of tiles that never got a tap, ack retries, heartbeat, and the
  microphone health check.

The microphone is allowed to be missing: the service stays up, reports the mic
as FAILED (the whole GUI turns to FAULT) and retries every few seconds. A
station that exits instead cannot show its operator why it stopped.

The capture object is injectable (anything with start/stop/get_clip/
notify_tap/last_rms/last_block_monotonic), so the whole service runs in tests
and in `--demo` with no audio hardware.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable, Optional

from acoustic.features import analyze_clip, grade_from_reference, spectrum_preview, waveform_preview
from fusion.blobs import BlobStore, EvidenceRecorder
from fusion.coordinator import FAIL, OK, StationCoordinator
from fusion.store import TileStore
from fusion.transport import Bus

log = logging.getLogger(__name__)


def default_capture_factory(config: dict):
    from acoustic.capture import AudioCapture  # lazy: needs PortAudio

    return AudioCapture(config)


class StationService:
    _MIC_RETRY_S = 5.0
    _MIC_SILENT_S = 2.0

    def __init__(
        self,
        config: dict,
        bus: Bus,
        store: TileStore,
        capture_factory: Callable[[dict], object] = default_capture_factory,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        blobs: Optional[BlobStore] = None,
    ):
        self._config = config
        self._station_cfg = config.get("station") or {}
        self._bus, self._store = bus, store
        self._capture_factory = capture_factory
        self._clock, self._mono = clock, monotonic

        self.trigger_mode: str = (config.get("trigger") or {}).get("mode", "rms")
        trig = config.get("hardware_trigger" if self.trigger_mode != "rms" else "trigger") or {}
        self._pre_trigger_ms = float(trig.get("pre_trigger_ms", 100))
        self._analysis_cfg = self._station_cfg.get("analysis") or {}
        self._reference = self._station_cfg.get("reference")
        self._sample_rate = int((config.get("audio") or {}).get("sample_rate", 44100))

        # Pictures and tap recordings kept beside the database (None = numbers only).
        self.blobs = blobs
        keep_audio = bool(((self._station_cfg.get("store") or {}).get("keep_audio", True)))
        self._evidence = EvidenceRecorder(blobs, keep_audio=keep_audio) if blobs is not None else None
        self.coordinator = StationCoordinator(
            self._station_cfg, bus, store, clock=clock, on_external_tap=self._external_tap,
            evidence=self._evidence,
        )

        self._capture = None
        self._mic_error: Optional[str] = "not started"
        self._next_mic_attempt = 0.0
        self._lock = threading.RLock()
        self._last_tap: Optional[dict] = None
        self._running = False
        self._threads: list[threading.Thread] = []

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> None:
        self.coordinator.start()
        self.coordinator.set_health("mic", FAIL, "not started")
        self._bus.start()
        self._open_capture()
        self._running = True
        for target, name in ((self._analysis_loop, "analysis"), (self._tick_loop, "tick")):
            t = threading.Thread(target=target, name=f"station-{name}", daemon=True)
            t.start()
            self._threads.append(t)

    def shutdown(self) -> None:
        self._running = False
        for t in self._threads:
            t.join(timeout=2.0)
        with self._lock:
            cap, self._capture = self._capture, None
        if cap is not None:
            try:
                cap.stop()
            except Exception:  # noqa: BLE001
                log.exception("could not stop the audio capture cleanly")
        self._bus.stop()

    def _open_capture(self) -> bool:
        try:
            cap = self._capture_factory(self._config)
            cap.start()
        except Exception as exc:  # noqa: BLE001 - no mic / wrong device / no PortAudio
            self._mic_error = f"{type(exc).__name__}: {exc}"
            self.coordinator.set_health("mic", FAIL, self._mic_error)
            log.error("microphone could not be opened: %s", self._mic_error)
            return False
        with self._lock:
            self._capture = cap
        self._mic_error = None
        self.coordinator.set_health("mic", OK, "listening")
        return True

    def set_device(self, device) -> None:
        """Switch microphone (an index or an exact name). Takes effect now."""
        if isinstance(device, str) and device.strip().lstrip("-").isdigit():
            device = int(device)
        with self._lock:
            old, self._capture = self._capture, None
        if old is not None:
            try:
                old.stop()
            except Exception:  # noqa: BLE001
                log.exception("could not stop the old capture")
        self._config.setdefault("audio", {})["device"] = device
        if not self._open_capture():
            raise RuntimeError(f"could not open microphone {device!r}: {self._mic_error}")

    def list_devices(self) -> list[dict]:
        try:
            from acoustic.capture import list_input_devices

            return [
                {"index": i, "name": d["name"], "channels": d["max_input_channels"]}
                for i, d in enumerate(list_input_devices())
            ]
        except Exception:  # noqa: BLE001
            return []

    # ---- tap input -------------------------------------------------------

    def _external_tap(self) -> None:
        cap = self._capture
        if cap is None:
            raise RuntimeError("the microphone is not open")
        cap.notify_tap()

    def simulate_tap(self) -> None:
        """Start a capture as if the striker had just fired. Only meaningful in
        trigger.mode hardware/simulated; in rms mode a clip starts by itself
        when the sound arrives."""
        if self.trigger_mode == "rms":
            raise RuntimeError("trigger.mode is 'rms': make a sound near the microphone instead")
        self._external_tap()

    # ---- threads ---------------------------------------------------------

    def _analysis_loop(self) -> None:
        while self._running:
            cap = self._capture
            if cap is None:
                time.sleep(0.2)
                continue
            try:
                clip = cap.get_clip(timeout=0.5)
            except queue.Empty:
                continue
            except Exception:  # noqa: BLE001
                log.exception("audio capture failed while waiting for a clip")
                time.sleep(0.5)
                continue
            try:
                self.handle_clip(clip)
            except Exception:  # noqa: BLE001 - one bad clip must not stop the station
                log.exception("tap analysis failed")

    def handle_clip(self, clip) -> Optional[str]:
        """Measure one clip and give it to the coordinator. Returns the tile id
        it was bound to (None: orphan, or ignored as not a strike)."""
        features = analyze_clip(
            clip,
            self._sample_rate,
            self._pre_trigger_ms,
            **{k: v for k, v in self._analysis_cfg.items() if k in ("min_peak", "min_snr_db", "min_freq_hz", "max_freq_hz")},
        )
        grade, confidence = grade_from_reference(features, self._reference)
        result = {**features, "grade": grade, "confidence": confidence}
        preview = {
            "ts": self._clock(),
            "features": result,
            "waveform": waveform_preview(clip),
            "spectrum": spectrum_preview(clip, self._sample_rate),
            "sample_rate": self._sample_rate,
            "tile_id": None,
        }
        # In rms mode the microphone itself decided "something happened", so a
        # clip that is not a plausible strike (a door slam, belt noise) must
        # NOT use up a tile's tap slot — that would shift every later tile by
        # one. With a hardware trigger the strike certainly happened, so an
        # unusable recording still consumes the slot (and the tile goes on
        # camera grade alone).
        if not features["valid"] and self.trigger_mode == "rms":
            self.coordinator.note_ignored_tap(features.get("reason_invalid") or "not a strike")
            with self._lock:
                self._last_tap = preview
            return None
        event = self.coordinator.pop_tap_event()
        tile_present = event.get("tile_present") if event else None
        tile_id = self.coordinator.on_tap(result, tile_present=tile_present)
        preview["tile_id"] = tile_id
        with self._lock:
            self._last_tap = preview
        if tile_id and self._evidence is not None:
            self._evidence.tap(tile_id, clip, self._sample_rate, {k: v for k, v in preview.items() if k != "tile_id"})
        return tile_id

    def _tick_loop(self) -> None:
        while self._running:
            try:
                self._check_mic()
                self.coordinator.tick()
            except Exception:  # noqa: BLE001
                log.exception("station tick failed")
            time.sleep(0.25)

    def _check_mic(self) -> None:
        cap = self._capture
        now = self._mono()
        if cap is None:
            if now >= self._next_mic_attempt:
                self._next_mic_attempt = now + self._MIC_RETRY_S
                self._open_capture()
            return
        last = getattr(cap, "last_block_monotonic", None)
        if last is None or now - last > self._MIC_SILENT_S:
            age = "never" if last is None else f"{now - last:.0f} s ago"
            self.coordinator.set_health("mic", FAIL, f"no audio blocks (last {age})")
        else:
            self.coordinator.set_health("mic", OK, f"listening, level {getattr(cap, 'last_rms', 0.0):.4f}")

    # ---- reporting -------------------------------------------------------

    def level(self) -> dict:
        cap = self._capture
        thr = (self._config.get("trigger") or {}).get("rms_threshold")
        return {
            "rms": float(getattr(cap, "last_rms", 0.0)) if cap is not None else None,
            "rms_threshold": thr if self.trigger_mode == "rms" else None,
            "mode": self.trigger_mode,
            "device": (self._config.get("audio") or {}).get("device"),
            "open": cap is not None,
            "error": self._mic_error,
        }

    @property
    def store(self) -> TileStore:
        return self._store

    def tile(self, tile_id: str) -> Optional[dict]:
        """One tile's stored record (None if unknown)."""
        return self._store.get(tile_id)

    def last_tap(self) -> Optional[dict]:
        with self._lock:
            return dict(self._last_tap) if self._last_tap else None

    def status(self, recent: int = 15) -> dict:
        snap = self.coordinator.snapshot(recent=recent)
        snap["mic"] = self.level()
        tap = self.last_tap()
        snap["last_tap"] = (
            {"ts": tap["ts"], "tile_id": tap["tile_id"], "valid": tap["features"].get("valid"),
             "peak_frequency_hz": tap["features"].get("peak_frequency_hz")}
            if tap else None
        )
        return snap
