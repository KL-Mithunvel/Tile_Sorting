"""Background capture loop: the hardware+glue layer that ties capture.py
(hardware) to the pure segmentation/tracker/line-trigger/pipeline modules, and
publishes results into a thread-safe SharedState for the dashboard to read.

Not unit-tested itself, same as acoustic_node's AudioCapture — it's real
hardware I/O and threading around already-tested pure logic, not logic worth
re-testing on its own (see .claude/CLAUDE.md Development Rule 1).
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Optional

import cv2
import numpy as np

from camera.capture import PiCameraCapture, WebcamCapture
from camera.grading_model import TileGradeModel
from camera.line_trigger import LineCrossingDetector
from camera.pipeline import TileRecord, process_tile
from camera.segmentation import TileRegion, segment_tile
from camera.snapshot import resolve_output_dir, save_tile_snapshot
from camera.tile_tracker import TileTracker

_OVERLAY_OK_COLOR = (0, 200, 0)  # BGR
_OVERLAY_BAD_COLOR = (0, 0, 220)
_LINE_COLOR = (0, 200, 255)  # amber — the armed trigger line
_LINE_FIRED_COLOR = (255, 255, 255)  # flashes white on the frame that fires
_CENTER_COLOR = (255, 120, 0)


def _draw_trigger_line(
    frame: np.ndarray, position: float, orientation: str, fired: bool
) -> None:
    """Draw the trigger line in place. Display only — the detector works in
    normalized coordinates and never sees pixels."""
    h, w = frame.shape[:2]
    color = _LINE_FIRED_COLOR if fired else _LINE_COLOR
    thickness = 3 if fired else 2
    if orientation == "vertical":
        x = int(position * w)
        cv2.line(frame, (x, 0), (x, h), color, thickness)
    else:
        y = int(position * h)
        cv2.line(frame, (0, y), (w, y), color, thickness)


def _draw_center(frame: np.ndarray, center: tuple[float, float]) -> None:
    """Filled dot plus a small crosshair — the dot alone is hard to pick out
    against a mid-brown tile, which is most of the frame."""
    cx, cy = int(center[0]), int(center[1])
    cv2.circle(frame, (cx, cy), 5, _CENTER_COLOR, -1)
    cv2.line(frame, (cx - 12, cy), (cx + 12, cy), _CENTER_COLOR, 1)
    cv2.line(frame, (cx, cy - 12), (cx, cy + 12), _CENTER_COLOR, 1)


def _draw_overlay(
    frame: np.ndarray,
    region: Optional[TileRegion],
    grade: Optional[str] = None,
    model_grade: Optional[str] = None,
    line: Optional[dict] = None,
    fired: bool = False,
) -> np.ndarray:
    """Best-effort live-view annotation: trigger line, tile bounding box, the
    box's centre point, and the last grade. Not part of the detection logic —
    purely visual, so it isn't unit-tested (mirrors acoustic_node's
    plotting.py, which is also display-only)."""
    annotated = frame.copy()

    if line is not None and line.get("draw_overlay", True):
        _draw_trigger_line(
            annotated,
            line.get("position", 0.5),
            line.get("orientation", "vertical"),
            fired,
        )

    if region is None:
        return annotated

    x, y, w, h = region.bbox
    color = _OVERLAY_BAD_COLOR if grade == "Reject" else _OVERLAY_OK_COLOR
    cv2.rectangle(annotated, (x, y), (x + w, y + h), color, 2)
    _draw_center(annotated, region.center)

    label_y = max(14, y - 8)
    if grade:
        cv2.putText(annotated, grade, (x, label_y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    if model_grade:
        cv2.putText(
            annotated,
            f"model: {model_grade}",
            (x, label_y + 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            _CENTER_COLOR,
            2,
        )
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
    """Owns the per-frame pipeline on its own background thread.

    `capture` is any object with capture.py's start()/read_frame()/stop()
    contract — WebcamCapture on a USB camera, PiCameraCapture on the Pi
    station's CSI module. The worker never asks which; it only ever wants a
    BGR frame back, which both backends guarantee.
    """

    def __init__(
        self,
        capture: WebcamCapture | PiCameraCapture,
        config: dict,
        state: SharedState,
    ):
        self._capture = capture
        self._config = config
        self._state = state
        self._tracker = TileTracker(
            min_present_frames=config["tile_tracker"]["min_present_frames"],
            min_absent_frames=config["tile_tracker"]["min_absent_frames"],
        )

        self._mode = config.get("processing_trigger", "line_crossing")
        if self._mode not in ("line_crossing", "departure"):
            raise ValueError(
                f"processing_trigger must be 'line_crossing' or 'departure', "
                f"got {self._mode!r}"
            )

        self._line_cfg = config.get("trigger_line") or {}
        self._crossing: Optional[LineCrossingDetector] = None
        if self._mode == "line_crossing":
            self._crossing = LineCrossingDetector(
                position=self._line_cfg.get("position", 0.5),
                orientation=self._line_cfg.get("orientation", "vertical"),
                direction=self._line_cfg.get("direction", "both"),
                hysteresis=self._line_cfg.get("hysteresis", 0.02),
            )

        # Loaded once here, not per tile: building an ONNX session takes tens
        # of milliseconds and holds the weights, so rebuilding it per tile
        # would dwarf the inference it exists to run.
        self._model = TileGradeModel.from_config(config)

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

    def join(self) -> None:
        """Block until the capture thread exits. Only used by the headless
        (--no-dashboard) path, where there's no Flask server to block on
        instead — the dashboard path blocks in app.run()."""
        while self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=0.5)

    def _loop(self) -> None:
        seg_cfg = self._config["segmentation"]
        crack_cfg = self._config["crack_detection"]
        corner_cfg = self._config["corner_detection"]
        snapshot_cfg = self._config["capture_snapshots"]
        jpeg_quality = self._config["dashboard"]["jpeg_quality"]

        seq = 0
        best_seen_region: Optional[TileRegion] = None
        last_grade: Optional[str] = None
        last_model_grade: Optional[str] = None

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

            # The tracker runs in both modes: it owns the presence debounce
            # and the departure count, independently of what triggers grading.
            departed = self._tracker.process_frame(tile_present)

            region_to_process: Optional[TileRegion] = None

            if self._mode == "line_crossing":
                event = self._crossing.update(
                    region.center_normalized if region is not None else None
                )
                if event and region is not None:
                    # Grade THIS frame. That is the point of the line: every
                    # tile is captured at the same place in the frame, so
                    # scale, framing and motion blur are comparable between
                    # tiles in a way "whatever the last frame was" is not.
                    region_to_process = region
            else:
                if tile_present:
                    # Keep the largest-area sighting, not just the latest one:
                    # the frame right before departure is often partially
                    # exited (or motion-blurred at the frame edge), while the
                    # largest-area frame is typically the most centered,
                    # fully visible, least-blurred picture of the tile.
                    if best_seen_region is None or region.area_px > best_seen_region.area_px:
                        best_seen_region = region
                if departed and best_seen_region is not None:
                    region_to_process = best_seen_region

            fired = region_to_process is not None

            if fired:
                seq += 1
                record = process_tile(
                    seq=seq,
                    region=region_to_process,
                    canny_low=crack_cfg["canny_low"],
                    canny_high=crack_cfg["canny_high"],
                    min_crack_length_px=crack_cfg["min_crack_length_px"],
                    min_aspect_ratio=crack_cfg["min_aspect_ratio"],
                    minor_severity_max_length_px=crack_cfg["minor_severity_max_length_px"],
                    blur_kernel_size=crack_cfg["blur_kernel_size"],
                    min_fill_ratio=corner_cfg["min_fill_ratio"],
                    border_margin_px=crack_cfg["border_margin_px"],
                    max_missing_extent_fraction=corner_cfg["max_missing_extent_fraction"],
                    tile_size_inches=corner_cfg.get("tile_size_inches"),
                    grading_model=self._model,
                    trigger=self._mode,
                )
                if snapshot_cfg["enabled"]:
                    path = save_tile_snapshot(record, resolve_output_dir(self._config))
                    record.snapshot_path = str(path)

                count = (
                    self._crossing.crossing_count
                    if self._mode == "line_crossing"
                    else self._tracker.tile_count
                )
                self._state.record_tile(record, count)
                last_grade = record.grade
                last_model_grade = record.model_grade
                best_seen_region = None

            # Clear the labels once the tile has gone, so a result stays
            # readable while its tile is still on screen but does not get
            # painted onto the next tile's box.
            if not tile_present:
                last_grade = None
                last_model_grade = None

            annotated = _draw_overlay(
                frame,
                region,
                grade=last_grade,
                model_grade=last_model_grade,
                line=self._line_cfg if self._crossing is not None else None,
                fired=fired,
            )
            ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
            if ok:
                self._state.update_frame(buf.tobytes())
