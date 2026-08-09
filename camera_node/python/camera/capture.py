"""Webcam capture — thin OpenCV wrapper.

Mirrors acoustic_node's capture.py split: this file is the hardware-facing
part only (open the device, read a frame, release it). All the actual image
processing lives in segmentation.py / crack_detection.py / corner_detection.py
/ tile_tracker.py, which don't touch a camera and are unit-tested with
synthetic images instead. Not unit-tested itself — real hardware I/O, only
smoke-testable manually (see camera_node/README.md).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import yaml


def load_config(path: Optional[Path] = None) -> dict:
    if path is None:
        path = Path(__file__).parent / "config.yaml"
    with open(path, "r") as f:
        return yaml.safe_load(f)


class WebcamCapture:
    def __init__(self, config: dict):
        cam_cfg = config["camera"]
        self.device_index = cam_cfg["device_index"]
        self.frame_width: int = cam_cfg["frame_width"]
        self.frame_height: int = cam_cfg["frame_height"]
        self.target_fps: int = cam_cfg["target_fps"]

        self._cap: Optional[cv2.VideoCapture] = None

    def start(self) -> None:
        self._cap = cv2.VideoCapture(self.device_index)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.frame_width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.frame_height)
        self._cap.set(cv2.CAP_PROP_FPS, self.target_fps)
        if not self._cap.isOpened():
            raise RuntimeError(f"WebcamCapture: could not open device index {self.device_index}")

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
