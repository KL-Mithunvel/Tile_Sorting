"""Background capture loop: the hardware+glue layer that ties capture.py
(hardware) to the pure segmentation/tracker/pipeline modules, and publishes
results into a thread-safe SharedState for the dashboard to read.

Not unit-tested itself, same as acoustic_node's AudioCapture — it's real
hardware I/O and threading around already-tested pure logic, not logic worth
re-testing on its own (see .CLAUDE/CLAUDE.md Development Rule 1).
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Optional

import cv2
import numpy as np

from camera.capture import WebcamCapture
from camera.pipeline import TileRecord, process_tile
from camera.segmentation import TileRegion, segment_tile
from camera.tile_tracker import TileTracker

_OVERLAY_OK_COLOR = (0, 200, 0)  # BGR
_OVERLAY_BAD_COLOR = (0, 0, 220)


def _draw_overlay(frame: np.ndarray, region: Optional[TileRegion], grade: Optional[str]) -> np.ndarray:
    """Best-effort live-view annotation. Not part of the detection logic —
    purely visual, so it isn't unit-tested (mirrors acoustic_node's
    plotting.py, which is also display-only)."""
    if region is None:
        return frame
    x, y, w, h = region.bbox
    color = _OVERLAY_BAD_COLOR if grade == "Reject" else _OVERLAY_OK_COLOR
    annotated = frame.copy()
    cv2.rectangle(annotated, (x, y), (x + w, y + h), color, 2)
    if grade:
        cv2.putText(annotated, grade, (x, max(0, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    return annotated


class SharedState:
    """Thread-safe latest-value store the dashboard reads from."""

    def __init__(self, max_recent_tiles: int = 50):
        self._lock = threading.Lock()
        self._latest_jpeg: Optional[bytes] = None
        self._tile_count = 0
        self._recent_tiles: deque[TileRecord] = deque(maxlen=max_recent_tiles)

    def update_frame(self, jpeg_bytes: bytes) -> None:
        with self._lock:
            self._latest_jpeg = jpeg_bytes

    def get_frame(self) -> Optional[bytes]:
        with self._lock:
            return self._latest_jpeg

    def record_tile(self, record: TileRecord, tile_count: int) -> None:
        with self._lock:
            self._tile_count = tile_count
            self._recent_tiles.appendleft(record)

    def status(self) -> dict:
        with self._lock:
            return {
                "tile_count": self._tile_count,
                "recent_tiles": list(self._recent_tiles),
            }


class CameraWorker:
    def __init__(self, capture: WebcamCapture, config: dict, state: SharedState):
        self._capture = capture
        self._config = config
        self._state = state
        self._tracker = TileTracker(
            min_present_frames=config["tile_tracker"]["min_present_frames"],
            min_absent_frames=config["tile_tracker"]["min_absent_frames"],
        )
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._capture.start()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._capture.stop()

    def _loop(self) -> None:
        seg_cfg = self._config["segmentation"]
        crack_cfg = self._config["crack_detection"]
        corner_cfg = self._config["corner_detection"]
        jpeg_quality = self._config["dashboard"]["jpeg_quality"]

        seq = 0
        last_seen_region: Optional[TileRegion] = None

        while self._running:
            try:
                frame = self._capture.read_frame()
            except RuntimeError:
                continue

            region = segment_tile(
                frame,
                hsv_lower=tuple(seg_cfg["hsv_lower"]),
                hsv_upper=tuple(seg_cfg["hsv_upper"]),
                min_area_px=seg_cfg["min_tile_area_px"],
                morph_kernel_size=seg_cfg["morph_kernel_size"],
            )
            tile_present = region is not None
            if tile_present:
                last_seen_region = region

            departed = self._tracker.process_frame(tile_present)

            grade_for_overlay = None
            if departed and last_seen_region is not None:
                seq += 1
                record = process_tile(
                    seq=seq,
                    region=last_seen_region,
                    canny_low=crack_cfg["canny_low"],
                    canny_high=crack_cfg["canny_high"],
                    min_crack_length_px=crack_cfg["min_crack_length_px"],
                    min_aspect_ratio=crack_cfg["min_aspect_ratio"],
                    minor_severity_max_length_px=crack_cfg["minor_severity_max_length_px"],
                    blur_kernel_size=crack_cfg["blur_kernel_size"],
                    min_fill_ratio=corner_cfg["min_fill_ratio"],
                )
                self._state.record_tile(record, self._tracker.tile_count)
                grade_for_overlay = record.grade
                last_seen_region = None

            annotated = _draw_overlay(frame, region, grade_for_overlay)
            ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
            if ok:
                self._state.update_frame(buf.tobytes())
