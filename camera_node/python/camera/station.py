"""The camera station's control surface: choose where video comes from, open a
recorded file, start/stop recording.

One object the dashboard's routes and the CLI both drive, so neither reaches
into the worker's internals. It owns a `CameraWorker` and the knowledge of what
can be opened:

  * a live camera by index (USB / UVC via OpenCV -- e.g. a webcam plugged into
    the laptop) or by CSI camera number (Raspberry Pi);
  * a saved video: one of the dashboard's own recordings, or any video under
    data/ (replayed at its own frame rate, optionally looping).

Nothing here touches pixels. Capture backends are injectable so the whole thing
is testable with fake cameras.
"""

from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Callable, Optional

from camera.capture import (
    LatestFrameCapture,
    PacedVideoCapture,
    PiCameraCapture,
    WebcamCapture,
    create_capture,
    list_cameras,
)
from camera.sources import (
    allowed_roots,
    list_media_files,
    list_recordings,
    recordings_root,
    resolve_media_path,
)
from camera.worker import CameraWorker, SharedState


class StationController:
    def __init__(
        self,
        config: dict,
        state: SharedState,
        lister: Callable[[], dict] = list_cameras,
        usb_factory: Callable[[dict], object] = WebcamCapture,
        csi_factory: Callable[[dict], object] = PiCameraCapture,
        file_factory: Callable[..., object] = PacedVideoCapture,
        publisher=None,
    ):
        self._config = config
        self._state = state
        self._lister = lister
        self._usb_factory = usb_factory
        self._csi_factory = csi_factory
        self._file_factory = file_factory
        self.worker = CameraWorker(None, config, state, publisher=publisher)

        self._scan_lock = threading.Lock()
        self._cameras: dict = {"usb": [], "csi": []}
        self._scanning = False
        self._scanned = False

    # ---- starting up -----------------------------------------------------

    def start(self, device: Optional[int] = None, video: Optional[str] = None, loop: bool = False) -> None:
        """Open the initial source: `video` if given, else a camera -- `device`
        (a USB index) if given, else whatever config.yaml's camera block says.
        Raises RuntimeError / ValueError if it cannot be opened; the caller
        decides whether that is fatal (headless) or just "pick a source" (the
        dashboard)."""
        if video is not None:
            self.open_file(video, loop=loop)
        elif device is not None:
            self.open_camera(device)
        else:
            capture = create_capture(self._config)
            csi = isinstance(capture, PiCameraCapture)
            capture = LatestFrameCapture(capture)  # live camera: always the newest frame, never a queue
            cam_cfg = self._config["camera"]
            index = cam_cfg.get("device_index", 0)
            if csi:
                index = (cam_cfg.get("picamera2") or {}).get("camera_num", 0)
            self.worker.switch_source(
                capture,
                {"kind": "camera", "label": f"{'CSI camera' if csi else 'Camera'} {index}", "index": index, "csi": csi},
            )

    def shutdown(self) -> None:
        self.worker.stop()

    # ---- choosing a source ----------------------------------------------

    def open_camera(self, index: int, csi: bool = False) -> dict:
        """Switch to a live camera. `index` is the USB device index, or the CSI
        camera number when `csi` is true."""
        index = int(index)
        if index < 0:
            raise ValueError(f"camera index must be >= 0, got {index}")
        cfg = copy.deepcopy(self._config)
        if csi:
            cfg["camera"].setdefault("picamera2", {})["camera_num"] = index
            capture = LatestFrameCapture(self._csi_factory(cfg))
            source = {"kind": "camera", "label": f"CSI camera {index}", "index": index, "csi": True}
        else:
            cfg["camera"]["device_index"] = index
            cfg["camera"]["backend"] = "usb"
            capture = LatestFrameCapture(self._usb_factory(cfg))
            source = {"kind": "camera", "label": f"Camera {index}", "index": index, "csi": False}
        self.worker.switch_source(capture, source)
        return source

    def open_file(self, path: str, loop: bool = False, realtime: bool = True) -> dict:
        """Switch to a saved video. Only files under data/ may be opened (the
        dashboard is reachable over the network); anything else raises
        ValueError saying why."""
        resolved = resolve_media_path(path, allowed_roots(self._config))
        capture = self._file_factory(str(resolved), loop=loop, realtime=realtime)
        in_recordings = recordings_root(self._config).resolve() in resolved.parents
        label = resolved.parent.name if in_recordings else resolved.name
        source = {"kind": "file", "label": label, "path": str(resolved), "recording": in_recordings}
        self.worker.switch_source(capture, source)
        return source

    def open_recording(self, recording_id: str, loop: bool = False) -> dict:
        for row in list_recordings(recordings_root(self._config)):
            if row["id"] == recording_id:
                return self.open_file(row["path"], loop=loop)
        raise ValueError(f"no recording named {recording_id!r}")

    # ---- playback controls (file sources) -------------------------------

    def _player(self):
        capture = self.worker.capture
        if capture is None or not hasattr(capture, "paused"):
            raise ValueError("playback controls only apply when a file is open")
        return capture

    def set_paused(self, paused: bool) -> None:
        self._player().paused = bool(paused)

    def set_loop(self, loop: bool) -> None:
        self._player().loop = bool(loop)

    # ---- recording -------------------------------------------------------

    def start_recording(self, folder=None, frame_log: bool = False) -> dict:
        return self.worker.start_recording(folder=folder, frame_log=frame_log)

    def stop_recording(self) -> dict:
        return self.worker.stop_recording()

    # ---- live labelling --------------------------------------------------

    def start_labelling(self, label=None, tier=None) -> dict:
        return self.worker.start_labelling(batch_label=label, batch_tier=tier)

    def stop_labelling(self) -> dict:
        return self.worker.stop_labelling()

    def label_batch(self, **fields) -> dict:
        return self.worker.set_label_batch(**fields)

    def label_tile(self, seq=None, **fields) -> dict:
        return self.worker.label_tile(seq, **fields)

    # ---- what can be opened ---------------------------------------------

    def scan_cameras(self, wait: bool = False) -> None:
        """Look for cameras. Probing means opening each index in turn, which
        takes seconds and can briefly light the camera's LED, so it runs on its
        own thread and only when asked (page load, or the Rescan button) --
        never on every poll. `wait=True` blocks until it is done (tests)."""
        with self._scan_lock:
            if self._scanning:
                return
            self._scanning = True

        # Never probe the camera that is streaming right now: opening it a second
        # time breaks the live feed on Windows (see capture.list_cameras).
        source = self.worker.source
        in_use = (
            {source["index"]}
            if self.worker.capture is not None and source.get("kind") == "camera"
            and not source.get("csi") and source.get("index") is not None
            else set()
        )

        def run() -> None:
            try:
                found = self._lister(skip_usb=in_use)
            except Exception:  # noqa: BLE001 - a probe that crashes must not take the page down
                found = {"usb": [], "csi": []}
            with self._scan_lock:
                self._cameras = found
                self._scanning = False
                self._scanned = True

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        if wait:
            thread.join()

    def sources(self) -> dict:
        """Everything the Source panel offers. Cameras come from the last scan,
        plus the camera currently in use: on Windows a camera that is already
        open cannot be opened again to probe it, so a scan would otherwise
        omit exactly the one that is working."""
        with self._scan_lock:
            usb = [dict(c) for c in self._cameras.get("usb", [])]
            csi = [dict(c) for c in self._cameras.get("csi", [])]
            scanning, scanned = self._scanning, self._scanned

        source = self.worker.source
        if self.worker.capture is not None and source.get("kind") == "camera":
            idx = source.get("index")
            bucket = csi if source.get("csi") else usb
            key = "camera_num" if source.get("csi") else "index"
            if idx is not None and not any(c.get(key) == idx for c in bucket):
                bucket.append({key: idx, "in_use": True})
                bucket.sort(key=lambda c: c.get(key, 0))

        return {
            "cameras": {"usb": usb, "csi": csi},
            "scanning": scanning,
            "scanned": scanned,
            "recordings": list_recordings(recordings_root(self._config)),
            "files": list_media_files(self._config),
            "source": source,
        }
