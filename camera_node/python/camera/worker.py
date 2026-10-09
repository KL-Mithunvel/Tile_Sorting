"""Background capture loop: the hardware+glue layer that ties capture.py
(hardware) to the pure segmentation/tracker/line-trigger/pipeline modules, and
publishes results into a thread-safe SharedState for the dashboard to read.

Not unit-tested itself, same as acoustic_node's AudioCapture — it's real
hardware I/O and threading around already-tested pure logic, not logic worth
re-testing on its own (see .claude/CLAUDE.md Development Rule 1).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Optional

import cv2
import numpy as np

from camera.capture import PiCameraCapture, WebcamCapture
from camera.grading_model import TileGradeModel
from camera.pipeline import TileRecord, process_tile
from camera.region_tracker import LineCrossingTracker
from camera.segmentation import TileRegion, locate_tiles
from camera.snapshot import resolve_output_dir, save_tile_snapshot
from camera.tile_tracker import TileTracker

log = logging.getLogger(__name__)

_OVERLAY_OK_COLOR = (0, 200, 0)  # BGR
_OVERLAY_BAD_COLOR = (0, 0, 220)
_LINE_COLOR = (0, 200, 255)  # amber — the armed trigger line
_LINE_FIRED_COLOR = (255, 255, 255)  # flashes white on the frame that fires
_CENTER_COLOR = (255, 120, 0)
_ROI_COLOR = (160, 160, 160)  # grey outline of the belt search area


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
    tiles: list[tuple[TileRegion, Optional[int], Optional[str], Optional[str]]],
    line: Optional[dict] = None,
    fired: bool = False,
    roi: Optional[tuple[int, int, int, int]] = None,
) -> np.ndarray:
    """Best-effort live-view annotation: belt outline, trigger line, and for
    EVERY tile in view its box, centre point, track id and grade. `tiles` is a
    list of (region, track_id, grade, model_grade); grade is None until that
    tile has crossed the line. Not part of the detection logic -- purely
    visual, so it isn't unit-tested (mirrors acoustic_node's plotting.py)."""
    annotated = frame.copy()

    if roi is not None:
        # Thin outline of where tiles are searched for. Display only.
        cv2.rectangle(annotated, (roi[0], roi[1]), (roi[2] - 1, roi[3] - 1), _ROI_COLOR, 1)

    if line is not None and line.get("draw_overlay", True):
        _draw_trigger_line(
            annotated,
            line.get("position", 0.5),
            line.get("orientation", "vertical"),
            fired,
        )

    for region, track_id, grade, model_grade in tiles:
        x, y, w, h = region.bbox
        color = _OVERLAY_BAD_COLOR if grade == "Reject" else _OVERLAY_OK_COLOR
        cv2.rectangle(annotated, (x, y), (x + w, y + h), color, 2)
        _draw_center(annotated, region.center)

        label_y = max(14, y - 8)
        label = grade or ""
        if track_id is not None:
            label = f"#{track_id} {label}".strip()
        if label:
            cv2.putText(annotated, label, (x, label_y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
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
        # Cumulative since start-up, deliberately NOT derived from
        # _recent_tiles: that deque only holds the last N records, so counting
        # it would make the tally shrink once old tiles scroll off.
        self._tally: dict[str, int] = {}
        # PNG, not JPEG: this is shown as "the exact image the model receives",
        # so it must not pick up compression artefacts the model never saw.
        self._last_tile_png: Optional[bytes] = None
        self._station_state = "waiting"
        self._info: dict = {}
        # Per-frame failures the worker survived (see CameraWorker._safe_step).
        # Surfaced so a station that is quietly failing is visible on the
        # dashboard instead of just looking like "no tiles yet".
        self._error_count = 0
        self._last_error: Optional[str] = None

    def record_error(self, message: str) -> None:
        with self._lock:
            self._error_count += 1
            self._last_error = message

    def set_info(self, info: dict) -> None:
        """Static facts about this run (trigger mode, model on/off, line)."""
        with self._lock:
            self._info = dict(info)

    def set_station_state(self, state: str) -> None:
        with self._lock:
            self._station_state = state

    def get_last_tile_png(self) -> Optional[bytes]:
        with self._lock:
            return self._last_tile_png

    def update_frame(self, jpeg_bytes: bytes) -> None:
        with self._lock:
            self._latest_jpeg = jpeg_bytes

    def get_frame(self) -> Optional[bytes]:
        with self._lock:
            return self._latest_jpeg

    def record_tile(self, record: TileRecord, tile_count: int) -> None:
        # Encode outside the lock; it is the slow part.
        ok, buf = cv2.imencode(".png", record.tile_bgr)
        with self._lock:
            self._tile_count = tile_count
            self._recent_tiles.appendleft(record)
            self._tally[record.grade] = self._tally.get(record.grade, 0) + 1
            if ok:
                self._last_tile_png = buf.tobytes()

    def status(self) -> dict:
        with self._lock:
            return {
                "tile_count": self._tile_count,
                "recent_tiles": list(self._recent_tiles),
                "tally": dict(self._tally),
                "station_state": self._station_state,
                "info": dict(self._info),
                "errors": {"count": self._error_count, "last": self._last_error},
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
        self._crossing: Optional[LineCrossingTracker] = None
        if self._mode == "line_crossing":
            self._crossing = LineCrossingTracker(
                line=dict(
                    position=self._line_cfg.get("position", 0.5),
                    orientation=self._line_cfg.get("orientation", "vertical"),
                    direction=self._line_cfg.get("direction", "both"),
                    hysteresis=self._line_cfg.get("hysteresis", 0.02),
                    max_step=self._line_cfg.get("max_step"),
                ),
                match_distance=self._line_cfg.get("match_distance", 0.15),
                max_missed_frames=self._line_cfg.get("max_missed_frames", 3),
            )

        # Loaded once here, not per tile: building an ONNX session takes tens
        # of milliseconds and holds the weights, so rebuilding it per tile
        # would dwarf the inference it exists to run.
        self._model = TileGradeModel.from_config(config)

        self._state.set_info(
            {
                "trigger": self._mode,
                "model_enabled": self._model is not None,
                "line_position": self._line_cfg.get("position")
                if self._mode == "line_crossing"
                else None,
                "line_orientation": self._line_cfg.get("orientation")
                if self._mode == "line_crossing"
                else None,
            }
        )

        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Per-run state the frame step carries from one frame to the next.
        self._seq = 0
        self._best_seen_region: Optional[TileRegion] = None
        # track id (None in departure mode, which has no ids) -> (grade, model
        # grade) for tiles already graded and still in view; drives the labels.
        self._graded: dict[Optional[int], tuple[str, Optional[str]]] = {}
        self._seen_errors: set[str] = set()

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
        while self._running:
            try:
                frame = self._capture.read_frame()
            except StopIteration:
                # A saved video file ran out. That is the end of the input, not
                # a fault: stop cleanly instead of dying with a traceback.
                self._running = False
                break
            except RuntimeError:
                time.sleep(0.01)  # transient capture hiccup; do not spin the CPU
                continue
            self._safe_step(frame)

    def _safe_step(self, frame: np.ndarray) -> None:
        """Run one frame through the pipeline, surviving anything it raises.

        Before this guard, one exception anywhere in segment / grade / snapshot
        killed the capture thread silently: the dashboard stayed up, the last
        frame froze, and nothing said why. A station must keep inspecting the
        next tile. The failure is counted and shown on the dashboard
        (SharedState.record_error), and the full traceback is logged once per
        distinct message so a fault that repeats every frame cannot flood the
        log.
        """
        try:
            self._step(frame)
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see above
            message = f"{type(exc).__name__}: {exc}"
            self._state.record_error(message)
            if message not in self._seen_errors:
                self._seen_errors.add(message)
                log.exception("frame processing failed (first occurrence of this error)")
            # Never leave the station showing "processing" for a tile that failed.
            self._state.set_station_state("waiting")

    def _step(self, frame: np.ndarray) -> None:
        seg_cfg = self._config["segmentation"]
        crack_cfg = self._config["crack_detection"]
        corner_cfg = self._config["corner_detection"]
        snapshot_cfg = self._config["capture_snapshots"]
        jpeg_quality = self._config["dashboard"]["jpeg_quality"]

        regions, belt_roi = locate_tiles(frame, seg_cfg)
        # Largest region: what the presence debounce and departure mode use.
        region = regions[0] if regions else None
        tile_present = region is not None

        # The tracker runs in both modes: it owns the presence debounce
        # and the departure count, independently of what triggers grading.
        departed = self._tracker.process_frame(tile_present)

        # (track id, region) pairs to grade this frame.
        to_process: list[tuple[Optional[int], TileRegion]] = []
        # Everything in view, for the overlay and the station state.
        in_view: list[tuple[TileRegion, Optional[int]]]

        if self._mode == "line_crossing":
            observations = self._crossing.update(regions)
            in_view = [(o.region, o.track_id) for o in observations]
            # Grade THIS frame, for each tile that just crossed. That is the
            # point of the line: every tile is captured at the same place in
            # the frame, so scale, framing and motion blur are comparable
            # between tiles. Each tile has its own identity, so two tiles in
            # frame can no longer be mistaken for one another.
            to_process = [(o.track_id, o.region) for o in observations if o.crossed]
            visible_ids = {o.track_id for o in observations}
            self._graded = {k: v for k, v in self._graded.items() if k in visible_ids}
        else:
            in_view = [(region, None)] if region is not None else []
            if tile_present:
                # Keep the largest-area sighting, not just the latest one:
                # the frame right before departure is often partially
                # exited (or motion-blurred at the frame edge), while the
                # largest-area frame is typically the most centered,
                # fully visible, least-blurred picture of the tile.
                if self._best_seen_region is None or region.area_px > self._best_seen_region.area_px:
                    self._best_seen_region = region
            else:
                self._graded = {}
            if departed and self._best_seen_region is not None:
                to_process = [(None, self._best_seen_region)]

        fired = bool(to_process)

        for track_id, tile_region in to_process:
            self._state.set_station_state("processing")
            self._seq += 1
            record = process_tile(
                seq=self._seq,
                region=tile_region,
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
            self._graded[track_id] = (record.grade, record.model_grade)
            self._best_seen_region = None

        # Station state: a result stays showing while its tile is still on
        # screen; a tile that has not reached the line yet is "in view".
        if not in_view:
            self._state.set_station_state("waiting")
        elif fired:
            self._state.set_station_state("result")
        elif any(tid not in self._graded for _, tid in in_view):
            self._state.set_station_state("in_view")
        else:
            self._state.set_station_state("result")

        tiles = [(r, tid, *self._graded.get(tid, (None, None))) for r, tid in in_view]
        annotated = _draw_overlay(
            frame,
            tiles,
            line=self._line_cfg if self._crossing is not None else None,
            fired=fired,
            roi=belt_roi,
        )
        ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        if ok:
            self._state.update_frame(buf.tobytes())
