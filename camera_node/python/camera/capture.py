"""Camera capture — thin hardware wrappers, one per backend.

Mirrors acoustic_node's capture.py split: this file is the hardware-facing
part only (open the device, read a frame, release it). All the actual image
processing lives in segmentation.py / crack_detection.py / corner_detection.py
/ tile_tracker.py, which don't touch a camera and are unit-tested with
synthetic images instead. Not unit-tested itself — real hardware I/O, only
smoke-testable manually (see camera_node/README.md).

Three backends, all with the same start() / read_frame() / stop() /
context-manager shape so CameraWorker and process_video.py don't care which
one they were handed:

  WebcamCapture    — a USB / UVC camera via OpenCV VideoCapture. Works on the
                     dev laptop (Windows) and on the Pi (V4L2).
  PiCameraCapture  — a CSI Raspberry Pi camera module via Picamera2. Pi only;
                     picamera2 is imported lazily so importing this module
                     still works fine on Windows.
  VideoFileCapture — a saved video file, for offline processing.

Pick one with create_capture(config), which honours config.yaml's
camera.backend ("auto" / "usb" / "picamera2"). Nothing here hardcodes a
device, resolution, or control value — all of it comes from config.yaml
(.claude/CLAUDE.md Development Rule 2).

Target hardware (decided 2026-09-20, Automation_Architecture.md §5.2): this
node runs on a Raspberry Pi 5 (16 GB) under Raspberry Pi OS Desktop 64-bit,
not the Arduino UNO Q. See camera_node/README.md and camera_node/deploy/.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np
import yaml


def load_config(path: Optional[Path] = None) -> dict:
    if path is None:
        path = Path(__file__).parent / "config.yaml"
    with open(path, "r") as f:
        return yaml.safe_load(f)


# OpenCV API preferences, by config.yaml name. "any" lets OpenCV choose, which
# is right on Windows; "v4l2" is the one to pin on the Pi, where OpenCV can
# otherwise pick a GStreamer path that ignores the resolution/FPS requests.
_API_PREFERENCES = {
    "any": cv2.CAP_ANY,
    "v4l2": getattr(cv2, "CAP_V4L2", cv2.CAP_ANY),
    "dshow": getattr(cv2, "CAP_DSHOW", cv2.CAP_ANY),
    "msmf": getattr(cv2, "CAP_MSMF", cv2.CAP_ANY),
}


class WebcamCapture:
    """A live USB / UVC camera, via OpenCV."""

    def __init__(self, config: dict, video_capture_factory=None):
        cam_cfg = config["camera"]
        self.device_index = cam_cfg["device_index"]
        self.frame_width: int = cam_cfg["frame_width"]
        self.frame_height: int = cam_cfg["frame_height"]
        self.target_fps: int = cam_cfg["target_fps"]
        self.fourcc: Optional[str] = cam_cfg.get("fourcc")
        self.buffer_size: Optional[int] = cam_cfg.get("buffer_size")
        self.gain: Optional[float] = cam_cfg.get("gain")
        exposure_cfg = cam_cfg.get("exposure") or {}
        self.exposure_mode: str = str(exposure_cfg.get("mode", "auto")).lower()
        self.exposure_value: Optional[float] = exposure_cfg.get("value")
        if self.exposure_mode not in ("auto", "manual"):
            raise ValueError(f"WebcamCapture: camera.exposure.mode must be 'auto' or 'manual', got '{self.exposure_mode}'")
        if self.exposure_mode == "manual" and self.exposure_value is None:
            raise ValueError("WebcamCapture: camera.exposure.mode 'manual' needs camera.exposure.value")
        if self.fourcc is not None and len(str(self.fourcc)) != 4:
            raise ValueError(f"WebcamCapture: camera.fourcc must be 4 characters (e.g. 'MJPG'), got '{self.fourcc}'")

        api_name = str(cam_cfg.get("api_preference", "any")).lower()
        if api_name not in _API_PREFERENCES:
            raise ValueError(
                f"WebcamCapture: unknown camera.api_preference '{api_name}' — "
                f"expected one of {sorted(_API_PREFERENCES)}"
            )
        self.api_name = api_name
        self.api_preference = _API_PREFERENCES[api_name]

        self._factory = video_capture_factory or cv2.VideoCapture
        self._cap = None
        # What the driver ACTUALLY gave us after start(); asking is not getting.
        self.settings: dict = {}

    def _auto_exposure_codes(self) -> Optional[tuple[float, float]]:
        """(manual, auto) values for CAP_PROP_AUTO_EXPOSURE, which the backends do
        not agree on. DirectShow uses 0.25 / 0.75, V4L2 uses 1 / 3. Other backends
        (Media Foundation) are not known to honour either, so no guess is made."""
        api = self.api_name
        if api == "any":
            api = "dshow" if sys.platform == "win32" else "v4l2"
        return {"dshow": (0.25, 0.75), "v4l2": (1.0, 3.0)}.get(api)

    def start(self) -> None:
        self._cap = self._factory(self.device_index, self.api_preference)
        if not self._cap.isOpened():
            raise RuntimeError(f"WebcamCapture: could not open device index {self.device_index}")
        cap = self._cap
        # Order matters: the pixel format first, because the driver offers a different
        # set of resolutions and frame rates for each (uncompressed YUY2 tops out far
        # below 30 fps at 1080p on many webcams; MJPG usually does not).
        if self.fourcc:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*str(self.fourcc)))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.frame_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.frame_height)
        cap.set(cv2.CAP_PROP_FPS, self.target_fps)
        if self.buffer_size is not None:
            # A deep driver queue means every frame is already old when it is read.
            cap.set(cv2.CAP_PROP_BUFFERSIZE, self.buffer_size)
        if self.gain is not None:
            cap.set(cv2.CAP_PROP_GAIN, self.gain)

        exposure_note = None
        codes = self._auto_exposure_codes()
        if self.exposure_mode == "manual":
            if codes is None:
                exposure_note = (
                    f"manual exposure was requested but api_preference '{self.api_name}' is not known to honour it; "
                    "use 'dshow' (Windows) or 'v4l2' (Linux)"
                )
            else:
                cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, codes[0])
                cap.set(cv2.CAP_PROP_EXPOSURE, self.exposure_value)
        self.settings = self._read_back(exposure_note)

    def _read_back(self, note: Optional[str] = None) -> dict:
        cap = self._cap
        v = int(cap.get(cv2.CAP_PROP_FOURCC) or 0)
        return {
            "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            # Many drivers report -1 or 0 here; the number is the driver's claim, not a measurement.
            "fps_reported": cap.get(cv2.CAP_PROP_FPS),
            "fourcc": "".join(chr((v >> 8 * i) & 0xFF) for i in range(4)) if v > 0 else None,
            "exposure": cap.get(cv2.CAP_PROP_EXPOSURE),
            "auto_exposure": cap.get(cv2.CAP_PROP_AUTO_EXPOSURE),
            "exposure_mode_requested": self.exposure_mode,
            "note": note,
        }

    def read_frame(self) -> np.ndarray:
        """Blocks until the next frame is available. Raises RuntimeError on a
        read failure (device disconnected, etc.)."""
        if self._cap is None:
            raise RuntimeError("WebcamCapture: call start() before read_frame()")
        ok, frame = self._cap.read()
        if not ok:
            raise RuntimeError("WebcamCapture: failed to read a frame")
        return frame

    def stop(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def __enter__(self) -> "WebcamCapture":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()


class PiCameraCapture:
    """A CSI Raspberry Pi camera module, via Picamera2 (Raspberry Pi only).

    Same start()/read_frame()/stop() contract as WebcamCapture, and returns
    BGR frames like it does, so everything downstream is unchanged.

    Two things here are easy to get wrong and are therefore config-driven, not
    hardcoded:

    1. **Pixel format.** libcamera names formats in packing order, not channel
       order, so Picamera2's "RGB888" hands back an array whose channels are
       already B, G, R — i.e. exactly what OpenCV wants, with no conversion.
       That is the default (picamera2.format: "RGB888", swap_rb: false). If
       colours come out inverted on the bench, flip swap_rb rather than
       editing this file — a wrong channel order would silently wreck
       segmentation's HSV thresholds.
    2. **Auto-exposure / auto-white-balance.** Left on, the sensor re-exposes
       per tile and the HSV range calibrated in config.yaml stops meaning
       anything. picamera2.controls is passed straight to libcamera, so the
       station can lock ExposureTime/AnalogueGain/ColourGains there once the
       rig's lighting is fixed. See camera_node/README.md.

    NOT YET RUN ON HARDWARE — the Pi 5 decision is dated 2026-09-20 and no
    CSI module has been attached yet. Treat this class as untested.
    """

    def __init__(self, config: dict):
        cam_cfg = config["camera"]
        pi_cfg = cam_cfg.get("picamera2", {}) or {}

        self.camera_num: int = pi_cfg.get("camera_num", 0)
        self.frame_width: int = cam_cfg["frame_width"]
        self.frame_height: int = cam_cfg["frame_height"]
        self.target_fps: int = cam_cfg["target_fps"]
        self.pixel_format: str = pi_cfg.get("format", "RGB888")
        self.swap_rb: bool = bool(pi_cfg.get("swap_rb", False))
        self.controls: dict[str, Any] = dict(pi_cfg.get("controls", {}) or {})

        self._picam = None

    @staticmethod
    def is_available() -> bool:
        """True if picamera2 imports and libcamera reports at least one
        camera. Used by create_capture()'s "auto" backend."""
        try:
            from picamera2 import Picamera2  # type: ignore
        except Exception:
            return False
        try:
            return len(Picamera2.global_camera_info()) > 0
        except Exception:
            return False

    def start(self) -> None:
        try:
            from picamera2 import Picamera2  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "PiCameraCapture: picamera2 is not installed. On Raspberry Pi OS install "
                "it with 'sudo apt install -y python3-picamera2' and create the venv with "
                "--system-site-packages (see camera_node/deploy/README.md); it is not "
                "available on Windows. Set camera.backend to 'usb' to use a USB webcam "
                "instead."
            ) from exc

        self._picam = Picamera2(camera_num=self.camera_num)
        video_config = self._picam.create_video_configuration(
            main={"size": (self.frame_width, self.frame_height), "format": self.pixel_format}
        )
        self._picam.configure(video_config)

        controls: dict[str, Any] = dict(self.controls)
        # FrameDurationLimits is in microseconds; derive it from the same
        # target_fps the USB path uses so the two backends behave alike.
        if "FrameDurationLimits" not in controls and self.target_fps:
            frame_us = int(1_000_000 / self.target_fps)
            controls["FrameDurationLimits"] = (frame_us, frame_us)
        if controls:
            self._picam.set_controls(controls)

        self._picam.start()

    def read_frame(self) -> np.ndarray:
        if self._picam is None:
            raise RuntimeError("PiCameraCapture: call start() before read_frame()")
        frame = self._picam.capture_array()
        if frame is None:
            raise RuntimeError("PiCameraCapture: failed to read a frame")
        if frame.ndim == 3 and frame.shape[2] == 4:  # XBGR8888 and friends
            frame = frame[:, :, :3]
        if self.swap_rb:
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        return np.ascontiguousarray(frame)

    def stop(self) -> None:
        if self._picam is not None:
            try:
                self._picam.stop()
            finally:
                self._picam.close()
                self._picam = None

    def __enter__(self) -> "PiCameraCapture":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()


class VideoFileCapture:
    """Reads frames from a saved video file instead of a live device — same
    start()/read_frame()/stop() shape as WebcamCapture, for offline
    processing of recorded conveyor footage (see camera/process_video.py).

    Unlike WebcamCapture, read_frame() raises StopIteration at end-of-file
    instead of blocking forever — a file has a defined end, a live device
    doesn't."""

    def __init__(self, video_path: str):
        self.video_path = video_path
        self._cap: Optional[cv2.VideoCapture] = None

    def start(self) -> None:
        self._cap = cv2.VideoCapture(self.video_path)
        if not self._cap.isOpened():
            raise RuntimeError(f"VideoFileCapture: could not open video file '{self.video_path}'")

    def read_frame(self) -> np.ndarray:
        if self._cap is None:
            raise RuntimeError("VideoFileCapture: call start() before read_frame()")
        ok, frame = self._cap.read()
        if not ok:
            raise StopIteration
        return frame

    def stop(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def __enter__(self) -> "VideoFileCapture":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()


class PacedVideoCapture(VideoFileCapture):
    """A saved video played back like a live camera: at its own frame rate, so
    the dashboard shows it moving in real time instead of racing through it,
    with optional looping and pause.

    Same start()/read_frame()/stop() contract as every other backend. Differs
    from VideoFileCapture, which is for offline batch processing and hands
    frames out as fast as the pipeline can take them.

    `loop=False` ends with StopIteration like a plain file; `loop=True` seeks
    back to frame 0 instead. While paused, read_frame() keeps returning the
    current frame at the playback rate (so the worker's loop keeps ticking and
    the feed holds still) rather than blocking.
    """

    def __init__(self, video_path: str, loop: bool = False, realtime: bool = True):
        super().__init__(video_path)
        self.loop = loop
        self.realtime = realtime
        self.paused = False
        self.fps: float = 0.0
        self.frame_count: int = 0
        self.position: int = 0  # index of the frame returned by the last read
        self._frame_interval = 0.0
        self._next_deadline = 0.0
        self._last_frame: Optional[np.ndarray] = None

    def start(self) -> None:
        super().start()
        self.fps = float(self._cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if not 1.0 <= self.fps <= 240.0:
            self.fps = 30.0  # container did not say; a sane default beats 0 or 1000
        self.frame_count = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self._frame_interval = 1.0 / self.fps
        self._next_deadline = _now()
        self.position = 0
        self._last_frame = None

    def read_frame(self) -> np.ndarray:
        if self._cap is None:
            raise RuntimeError("PacedVideoCapture: call start() before read_frame()")

        if self.paused and self._last_frame is not None:
            self._wait_for_next_tick()
            return self._last_frame

        try:
            frame = super().read_frame()
        except StopIteration:
            if not self.loop:
                raise
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            self.position = 0
            frame = super().read_frame()  # an empty file raises StopIteration: fine
        else:
            self.position = int(self._cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1

        self._last_frame = frame
        self._wait_for_next_tick()
        return frame

    def _wait_for_next_tick(self) -> None:
        if not self.realtime:
            return
        self._next_deadline += self._frame_interval
        delay = self._next_deadline - _now()
        if delay > 0:
            _sleep(delay)
        elif delay < -1.0:
            # Fell more than a second behind (a slow frame, a debugger stop):
            # resynchronise instead of fast-forwarding to catch up.
            self._next_deadline = _now()


def _now() -> float:
    return time.monotonic()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def create_capture(config: dict):
    """Build the live capture backend named by config.yaml's camera.backend.

    "usb"        -> WebcamCapture
    "picamera2"  -> PiCameraCapture
    "auto"       -> PiCameraCapture if a CSI camera is actually present,
                    otherwise WebcamCapture. This is what lets the same
                    config run on the dev laptop and on the Pi unchanged.
    """
    backend = str(config["camera"].get("backend", "auto")).lower()

    if backend == "usb":
        return WebcamCapture(config)
    if backend == "picamera2":
        return PiCameraCapture(config)
    if backend == "auto":
        if PiCameraCapture.is_available():
            return PiCameraCapture(config)
        return WebcamCapture(config)

    raise ValueError(
        f"create_capture: unknown camera.backend '{backend}' — expected 'auto', 'usb', "
        "or 'picamera2'"
    )


def list_cameras(max_usb_index: int = 8, skip_usb=()) -> dict:
    """Enumerate what this machine can actually open, for --list-devices.

    USB detection works by trying to open each index, which is the only
    portable way with OpenCV — it is slow-ish and prints backend warnings on
    some systems. CSI detection asks libcamera directly.

    `skip_usb` lists indices NOT to probe. Probing a camera that is already
    streaming is not harmless: on Windows the second open takes the device away
    and the live capture starts failing every read (seen on the real webcam: a
    scan triggered by loading the dashboard froze the running feed). The caller
    passes the index it has open and lists that camera itself.
    """
    skip = set(skip_usb)
    usb: list[dict] = []
    for index in range(max_usb_index):
        if index in skip:
            continue
        cap = cv2.VideoCapture(index)
        try:
            if cap.isOpened():
                usb.append(
                    {
                        "index": index,
                        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                        "fps": cap.get(cv2.CAP_PROP_FPS),
                    }
                )
        finally:
            cap.release()

    csi: list[dict] = []
    try:
        from picamera2 import Picamera2  # type: ignore

        for num, info in enumerate(Picamera2.global_camera_info()):
            csi.append({"camera_num": num, "info": info})
    except Exception:
        pass  # not a Pi, or picamera2 not installed — nothing to report

    return {"usb": usb, "csi": csi}
