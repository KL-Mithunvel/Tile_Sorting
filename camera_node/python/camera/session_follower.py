"""Records the camera's raw video into a data-collection session started elsewhere.

The Pi's acoustic/decision service owns a session (acoustic_node: fusion/session.py). When one
starts it announces it on the bus (`station/session/status`, retained) with the folder; this
follower, in the camera process, then records the raw feed into `<folder>/video/` -- video.mp4,
frames.csv (wall time of every frame) and meta.json -- and stops when the announcement says the
session is over. It answers on `station/session/camera` so the station can show whether the video
is really being written, and waits for nobody: a camera that cannot record (no live camera, disk
full) says `error` and the inspection carries on.

The camera and the acoustic node never import each other; the two topic names and the JSON shape
are the whole contract (tests/test_station_contract.py keeps them in step).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

TOPIC_SESSION_STATUS = "station/session/status"
TOPIC_SESSION_CAMERA = "station/session/camera"


class SessionFollower:
    def __init__(self, controller, publisher, repo_root: Path, config: Optional[dict] = None,
                 clock: Callable[[], float] = time.time):
        self._controller, self._pub = controller, publisher
        self._data_root = (Path(repo_root) / "data").resolve()
        self._config = config
        self._clock = clock
        self.session_id: Optional[str] = None

    def attach(self) -> None:
        self._pub.subscribe(TOPIC_SESSION_STATUS, self._on_status)

    # ---- the announcement --------------------------------------------------------

    def _on_status(self, topic: str, payload: bytes) -> None:
        try:
            msg = json.loads(payload)
            if not isinstance(msg, dict):
                raise ValueError("not an object")
        except ValueError:
            return
        if msg.get("active") and msg.get("id") and msg.get("dir"):
            self.start(str(msg["id"]), str(msg["dir"]))
        else:
            self.stop()

    def start(self, session_id: str, rel_dir: str) -> bool:
        if self.session_id == session_id:
            return True                             # the retained message again after a reconnect
        if self.session_id is not None:
            self.stop()
        try:
            folder = (self._data_root.parent / rel_dir).resolve()
            folder.relative_to(self._data_root)     # only ever inside data/
            video = folder / "video"
            part = 1
            while (video / "meta.json").exists():   # a camera restarted mid-session: a second part
                part += 1
                video = folder / f"video_{part}"
            if self._config is not None:
                self._write_config(folder / "camera_config.yaml")
            self._controller.start_recording(folder=video, frame_log=True)
        except (ValueError, RuntimeError, OSError) as exc:
            log.error("session %s: the camera could not start recording: %s", session_id, exc)
            self._answer("error", session_id, detail=str(exc))
            return False
        self.session_id = session_id
        self._answer("recording", session_id, video=video.name)
        return True

    def stop(self) -> None:
        sid, self.session_id = self.session_id, None
        if sid is None:
            return
        try:
            meta = self._controller.stop_recording()
            self._answer("stopped", sid, frames=meta.get("frames"), fps=meta.get("fps"))
        except (RuntimeError, ValueError) as exc:     # already stopped (a failed disk, a source change)
            self._answer("stopped", sid, detail=str(exc))

    def _write_config(self, path: Path) -> None:
        """The exact thresholds the pipeline ran with, so a later replay of the video can be
        graded identically."""
        import yaml

        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(yaml.safe_dump(self._config, sort_keys=False), encoding="utf-8")

    def _answer(self, state: str, session_id: str, **extra) -> None:
        self._pub.publish_json(TOPIC_SESSION_CAMERA, {"state": state, "id": session_id, "ts": self._clock(), **extra},
                               qos=1, retain=True)
