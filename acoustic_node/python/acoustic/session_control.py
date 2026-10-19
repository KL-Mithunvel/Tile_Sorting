"""Runs data-collection sessions on the station and drives the striker by hand.

Glue between the pure `fusion.session.SessionRecorder` (the folder, the files) and the
station: it listens to the bus so the session records everything the station says, tells the
camera service (a separate process) to record its video into the same folder, and sends the
manual striker commands that the bridge turns into Nano serial lines.

Three jobs, all optional -- a station that never starts a session behaves exactly as before:

  * session start / stop, and the labels a following strike will carry;
  * `striker_*`: auto (ToF) / manual mode, arm, retract, release, one full cycle;
  * `audio_sink`: the microphone's blocks, for the continuous recording.

The camera is told over the bus (`station/session/status`, retained) and answers on
`station/session/camera`; both services run on the Pi and see the same folder.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Callable, Optional

from fusion.session import SessionRecorder, mark_interrupted
from fusion.transport import (
    CAMERA_RESULT_FILTER,
    TOPIC_NANO_STATUS,
    TOPIC_RETRACT,
    TOPIC_SESSION_STATUS,
    TOPIC_STRIKE,
    TOPIC_STRIKER_CMD,
    TOPIC_TAP,
    Bus,
    tile_id_from_topic,
)

log = logging.getLogger(__name__)

TOPIC_SESSION_CAMERA = "station/session/camera"
LOG_FILTERS = ("station/#", "tile/#", "machine/#")


class SessionController:
    def __init__(
        self,
        bus: Bus,
        recorder: SessionRecorder,
        data_root: Path,
        sample_rate: int = 44100,
        config_snapshot: Optional[Callable[[], dict]] = None,
        clock: Callable[[], float] = time.time,
        camera_wait_s: float = 15.0,
    ):
        self._bus, self.recorder = bus, recorder
        self._data_root = Path(data_root)
        self._rate = int(sample_rate)
        self._config_snapshot = config_snapshot
        self._clock = clock
        self._camera_wait_s = float(camera_wait_s)
        self.camera: dict = {}              # the camera service's last answer
        self.nano: dict = {}                # the bridge's last retained status
        self._interrupted: list = []

    # ---- wiring -------------------------------------------------------------------

    def attach(self) -> None:
        self._interrupted = mark_interrupted(self.recorder.root)
        if self._interrupted:
            log.warning("sessions that never stopped cleanly (sealed as interrupted): %s", self._interrupted)
        for f in LOG_FILTERS:
            self._bus.subscribe(f, self._on_any)
        # A fresh start has no session; clear a retained "active" left by a crash so the
        # camera does not start recording into a folder nobody is feeding.
        self._publish_status()

    def audio_sink(self, block) -> None:
        self.recorder.add_audio(block)

    @property
    def interrupted(self) -> list:
        return list(self._interrupted)

    # ---- bus --------------------------------------------------------------------------

    def _on_any(self, topic: str, payload: bytes) -> None:
        if topic == TOPIC_NANO_STATUS:
            try:
                self.nano = json.loads(payload)
            except ValueError:
                pass
        elif topic == TOPIC_SESSION_CAMERA:
            try:
                self.camera = json.loads(payload)
            except ValueError:
                pass
        if not self.recorder.active:
            return
        self.recorder.log_event(topic, payload)
        try:
            if topic in (TOPIC_TAP, TOPIC_RETRACT):
                d = json.loads(payload)
                self.recorder.note_striker(
                    "strike" if topic == TOPIC_TAP else "retract",
                    d.get("source") or "tof", d.get("nano_ms"), d.get("seq"), t=d.get("ts"),
                    tile_present=d.get("tile_present"), tof_mm=d.get("tof_mm"),
                )
            elif topic.startswith("tile/") and topic.endswith("/camera/result"):
                tile_id = tile_id_from_topic(topic)
                snap = (json.loads(payload) or {}).get("snapshot_path")
                if tile_id and snap:
                    self.recorder.keep_tile_picture(tile_id, snap, self._data_root)
        except Exception:  # noqa: BLE001 - recording must never disturb the station
            log.exception("session could not record %s", topic)

    def bind_tile(self, tile_id: Optional[str]) -> None:
        if tile_id and self.recorder.active:
            self.recorder.bind_tile(tile_id)

    # ---- session ----------------------------------------------------------------------

    def start(self, run_type: str = "tile", note: str = "", labels: Optional[dict] = None,
              mic_ok: bool = True, allow_no_audio: bool = False) -> dict:
        if not mic_ok and not allow_no_audio:
            raise RuntimeError("the microphone is not open; a session without audio is not started "
                               "(fix the microphone, or start with allow_no_audio)")
        cfg = self._config_snapshot() if self._config_snapshot else {}
        status = self.recorder.start(run_type=run_type, note=note, labels=labels, sample_rate=self._rate,
                                     config=cfg, extra={"audio_available": bool(mic_ok)})
        self._publish_status()
        return status

    def stop(self) -> dict:
        if not self.recorder.active:
            raise RuntimeError("no session is running")
        sid = self.recorder.id
        self._bus.publish(TOPIC_SESSION_STATUS, {"active": False, "id": sid, "stopping": True}, qos=1, retain=True)
        try:
            return self.recorder.stop(camera_wait=self._wait_for_camera)
        finally:
            self._publish_status()

    def _wait_for_camera(self, session_dir: Path) -> None:
        """The camera finishes its file a moment after the stop message. Seal the manifest only
        once video/meta.json says complete -- unless the camera never started recording."""
        if self.camera.get("state") != "recording" or self.camera.get("id") != session_dir.name:
            return
        meta = session_dir / "video" / "meta.json"
        deadline = time.monotonic() + self._camera_wait_s
        while time.monotonic() < deadline:
            try:
                if json.loads(meta.read_text(encoding="utf-8")).get("status") != "recording":
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.2)
        log.warning("the camera's video for %s was still being written; re-seal with `fusion.session seal`",
                    session_dir.name)

    def _publish_status(self) -> None:
        st = self.recorder.status()
        body = {"active": False} if not st.get("active") else {
            "active": True, "id": st["id"], "dir": st["dir"], "run_type": st.get("run_type"), "ts": self._clock()}
        self._bus.publish(TOPIC_SESSION_STATUS, body, qos=1, retain=True)

    def status(self) -> dict:
        st = self.recorder.status()
        st["camera"] = dict(self.camera) if st.get("active") else {}
        st["nano"] = {k: self.nano.get(k) for k in ("link", "manual", "retracted", "fw", "tof1_ok", "tof1_mm",
                                                       "armed", "nano_state", "left_ms", "must_leave", "hold_pct")}
        return st

    # ---- the striker, by hand -------------------------------------------------------------

    def striker_mode(self, manual: bool) -> None:
        self._bus.publish(TOPIC_STRIKER_CMD, {"action": "mode", "manual": bool(manual)}, qos=1)

    def striker_arm(self, enabled: bool) -> None:
        """Allow / inhibit the striker (STRIKE 1/0): every strike, manual or not, needs it."""
        self._bus.publish(TOPIC_STRIKE, {"enabled": bool(enabled)}, qos=1)

    def striker_hold(self, percent: float) -> None:
        """Coil duty (%) while the striker is held up. Lower = cooler coil, weaker hold (too low drops it)."""
        percent = float(percent)
        if not 0 <= percent <= 100:
            raise ValueError("hold percent must be 0-100")
        self._bus.publish(TOPIC_STRIKER_CMD, {"action": "hold", "percent": percent}, qos=1)

    def striker_action(self, action: str) -> None:
        if action not in ("retract", "release", "cycle"):
            raise ValueError("action must be retract, release or cycle")
        self._bus.publish(TOPIC_STRIKER_CMD, {"action": action}, qos=1)
