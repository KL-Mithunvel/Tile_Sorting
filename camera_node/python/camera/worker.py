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
from camera.pipeline import TileRecord, grading_options, process_tile
from camera.ratemeter import RateMeter
from camera.recorder import Recorder
from camera.region_tracker import LineCrossingTracker, validate_line_config
from camera.segmentation import TileRegion, locate_tiles
from camera.snapshot import resolve_output_dir, save_tile_snapshot
from camera.sources import recordings_root
from camera.tile_tracker import TileTracker

log = logging.getLogger(__name__)


def _round(v):
    return None if v is None else round(v, 1)

_OVERLAY_OK_COLOR = (0, 200, 0)  # BGR
_OVERLAY_BAD_COLOR = (0, 0, 220)
_OVERLAY_WARN_COLOR = (0, 165, 255)  # amber: Grade B / Grade C
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
        color = (
            _OVERLAY_BAD_COLOR if grade == "Reject"
            else _OVERLAY_WARN_COLOR if grade in ("Grade B", "Grade C")
            else _OVERLAY_OK_COLOR
        )
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
        # Where the video is coming from right now, and what the recorder is
        # doing. Both are shown on the dashboard's Source panel.
        self._source: dict = {"kind": "none", "label": "No source"}
        self._source_status = "none"  # none | running | finished | error
        self._source_detail: Optional[str] = None
        self._playback: Optional[dict] = None  # file sources only
        self._recording: Optional[dict] = None  # set while recording
        self._last_recording: Optional[dict] = None  # the one that just finished
        self._perf: Optional[dict] = None  # frame rates, skipped frames, the camera's real settings

    def record_error(self, message: str) -> None:
        with self._lock:
            self._error_count += 1
            self._last_error = message

    def reset_results(self) -> None:
        """Forget everything measured so far. Called when the video source
        changes: counts and grades from one source must not carry into the
        next, or a replayed file would add to the live camera's tally."""
        with self._lock:
            self._tile_count = 0
            self._recent_tiles.clear()
            self._tally = {}
            self._last_tile_png = None
            self._latest_jpeg = None
            self._station_state = "waiting"
            self._error_count = 0
            self._last_error = None

    def set_source(self, source: dict, status: str = "running", detail: Optional[str] = None) -> None:
        with self._lock:
            self._source = dict(source)
            self._source_status = status
            self._source_detail = detail
            self._playback = None

    def set_source_status(self, status: str, detail: Optional[str] = None) -> None:
        with self._lock:
            self._source_status = status
            self._source_detail = detail

    def set_playback(self, playback: Optional[dict]) -> None:
        with self._lock:
            self._playback = dict(playback) if playback else None

    def set_recording(self, recording: Optional[dict]) -> None:
        with self._lock:
            self._recording = dict(recording) if recording else None

    def set_perf(self, perf: Optional[dict]) -> None:
        with self._lock:
            self._perf = dict(perf) if perf else None

    def set_last_recording(self, meta: Optional[dict]) -> None:
        with self._lock:
            self._last_recording = dict(meta) if meta else None

    def set_info(self, info: dict) -> None:
        """Static facts about this run (trigger mode, model on/off, line)."""
        with self._lock:
            self._info = dict(info)

    def update_info(self, **facts) -> None:
        """Change individual facts in `info` (the trigger line's orientation is
        learned from the belt a few frames after a source opens)."""
        with self._lock:
            self._info.update(facts)

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
                "source": {
                    **self._source,
                    "status": self._source_status,
                    "detail": self._source_detail,
                    "playback": dict(self._playback) if self._playback else None,
                },
                "recording": dict(self._recording) if self._recording else None,
                "last_recording": dict(self._last_recording) if self._last_recording else None,
                "perf": dict(self._perf) if self._perf else None,
            }


class CameraWorker:
    """Owns the per-frame pipeline on its own background thread.

    `capture` is any object with capture.py's start()/read_frame()/stop()
    contract -- WebcamCapture on a USB camera, PiCameraCapture on the Pi
    station's CSI module, PacedVideoCapture for a saved video. The worker never
    asks which; it only ever wants a BGR frame back. It may be None: the
    dashboard can start with no source and be pointed at one later.

    The source can be swapped while running (`switch_source`) and the raw feed
    can be recorded (`start_recording` / `stop_recording`). Both are driven from
    Flask's request threads, so they are serialised by `_control_lock`.
    """

    # How long reads must keep failing before the dashboard says the camera has
    # stopped delivering frames (a USB camera pulled out mid-run), rather than
    # just showing a frozen picture. Time, not a count: with the threaded capture
    # a silent camera makes each failed read wait a second.
    _READ_FAILURE_SECONDS = 3.0
    _PUBLISH_EVERY_S = 0.25

    def __init__(
        self,
        capture: Optional[WebcamCapture | PiCameraCapture],
        config: dict,
        state: SharedState,
        source: Optional[dict] = None,
    ):
        self._capture = capture
        self._config = config
        self._state = state
        self._source = dict(source) if source else {"kind": "camera", "label": "Camera"}
        self._control_lock = threading.RLock()
        self._rec_lock = threading.Lock()  # serialises recorder.write() against stop()
        self._recorder: Optional[Recorder] = None
        self._last_publish = 0.0
        self._read_failed_since: Optional[float] = None
        self._proc_meter = RateMeter(30)
        self._reported_read_failure = False

        self._mode = config.get("processing_trigger", "line_crossing")
        if self._mode not in ("line_crossing", "departure"):
            raise ValueError(
                f"processing_trigger must be 'line_crossing' or 'departure', "
                f"got {self._mode!r}"
            )

        self._line_cfg = config.get("trigger_line") or {}
        if self._mode == "line_crossing":
            validate_line_config(self._line_cfg, config["segmentation"])

        # Loaded once here, not per tile: building an ONNX session takes tens
        # of milliseconds and holds the weights, so rebuilding it per tile
        # would dwarf the inference it exists to run.
        self._model = TileGradeModel.from_config(config)
        # Validated here, at start-up: a tier the map can't translate must stop
        # the station now, not raise on the first tile that gets that tier.
        self._grading_opts = grading_options(config, self._model)

        self._state.set_info(
            {
                "trigger": self._mode,
                "model_enabled": self._model is not None,
                # None when the model is display-only (no model or no tier map).
                "model_min_confidence": self._grading_opts.get("model_min_confidence"),
                "tier_map": self._grading_opts.get("tier_map"),
                "line_position": self._line_cfg.get("position")
                if self._mode == "line_crossing"
                else None,
                # "auto" is resolved from the belt; None until then (see _step).
                "line_orientation": None
                if self._line_cfg.get("orientation", "auto") == "auto"
                else self._line_cfg.get("orientation"),
                "line_orientation_auto": self._line_cfg.get("orientation", "auto") == "auto",
            }
        )

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._reset_run_state()

    def _reset_run_state(self) -> None:
        """Everything the frame step carries from one frame to the next. Reset
        when the source changes: a new video has no tiles in flight, and the
        old one's track ids and counts mean nothing for it."""
        self._tracker = TileTracker(
            min_present_frames=self._config["tile_tracker"]["min_present_frames"],
            min_absent_frames=self._config["tile_tracker"]["min_absent_frames"],
        )
        self._crossing: Optional[LineCrossingTracker] = None
        if self._mode == "line_crossing":
            self._crossing = LineCrossingTracker(
                line=dict(
                    position=self._line_cfg.get("position", 0.5),
                    orientation=self._line_cfg.get("orientation", "auto"),
                    direction=self._line_cfg.get("direction", "both"),
                    hysteresis=self._line_cfg.get("hysteresis", 0.02),
                    max_step=self._line_cfg.get("max_step"),
                ),
                match_distance=self._line_cfg.get("match_distance", 0.15),
                max_missed_frames=self._line_cfg.get("max_missed_frames", 3),
                auto_frames=self._line_cfg.get("auto_frames", 8),
            )
        self._frames_since_reset = 0
        self._orientation_reported: Optional[str] = None
        self._auto_warned = False
        self._proc_meter.reset()
        if self._crossing is not None and not self._crossing.resolved:
            self._state.update_info(line_orientation=None)  # a new source: relearn the belt
        self._seq = 0
        self._best_seen_region: Optional[TileRegion] = None
        # track id (None in departure mode, which has no ids) -> (grade, model
        # grade) for tiles already graded and still in view; drives the labels.
        self._graded: dict[Optional[int], tuple[str, Optional[str]]] = {}
        self._seen_errors: set[str] = set()
        self._read_failed_since = None

    # ---- lifecycle -------------------------------------------------------

    @property
    def capture(self):
        """The current capture backend (None when no source is open). The
        controller uses it to pause / loop a file player."""
        return self._capture

    @property
    def source(self) -> dict:
        return dict(self._source)

    def start(self) -> None:
        with self._control_lock:
            if self._capture is None:
                self._state.set_source({"kind": "none", "label": "No source"}, status="none")
                return
            self._capture.start()  # RuntimeError (no such camera...) propagates, as before
            self._state.set_source(self._source, status="running")
            self._launch()

    def stop(self) -> None:
        with self._control_lock:
            if self._recorder is not None:
                try:
                    self.stop_recording()  # a Ctrl+C mid-recording must still save meta.json
                except Exception:  # noqa: BLE001 - shutting down; nothing useful to do
                    log.exception("could not finalise the recording on shutdown")
            self._halt()
            if self._capture is not None:
                self._capture.stop()

    def join(self) -> None:
        """Block until the capture thread exits. Only used by the headless
        (--no-dashboard) path, where there's no Flask server to block on
        instead -- the dashboard path blocks in app.run()."""
        while self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=0.5)

    def _launch(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _halt(self) -> None:
        self._running = False
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)
            if thread.is_alive():
                raise RuntimeError("the capture thread did not stop (a camera read is stuck)")
        self._thread = None

    def switch_source(self, capture, source: dict) -> None:
        """Replace the video source while running.

        The old source is stopped BEFORE the new one is opened, because opening
        the same USB camera twice fails on Windows. If the new source cannot be
        opened the old one is put back, so a mistyped path or a wrong camera
        index does not leave the station with nothing.

        Counts, tracks and the on-screen results are reset: grades from one
        source must not carry into the next.
        """
        with self._control_lock:
            if self._recorder is not None:
                self.stop_recording()  # one recording is one source
            self._halt()
            old = self._capture
            if old is not None:
                old.stop()
            try:
                capture.start()
            except Exception:
                if old is not None:
                    try:
                        old.start()
                        self._launch()
                        self._state.set_source_status("running")
                    except Exception as restore_exc:  # noqa: BLE001
                        self._state.set_source_status("error", f"previous source could not be restored: {restore_exc}")
                raise
            self._capture = capture
            self._source = dict(source)
            self._reset_run_state()
            self._state.reset_results()
            self._state.set_source(self._source, status="running")
            self._launch()

    # ---- recording -------------------------------------------------------

    @property
    def recording(self) -> bool:
        rec = self._recorder
        return rec is not None and rec.active

    def start_recording(self) -> dict:
        with self._control_lock:
            if self.recording:
                raise RuntimeError("already recording")
            if self._capture is None or self._source.get("kind") != "camera":
                raise ValueError("recording needs a live camera as the source (a file is already a recording)")
            rec_cfg = self._config.get("recording") or {}
            rec = Recorder(
                recordings_root(self._config),
                source_label=self._source.get("label", "camera"),
                warmup_frames=int(rec_cfg.get("warmup_frames", 10)),
                fallback_fps=float(rec_cfg.get("fallback_fps", 15)),
            )
            folder = rec.start()
            with self._rec_lock:
                self._recorder = rec
            self._state.set_last_recording(None)
            self._publish(force=True)
            return {"folder": folder.name}

    def stop_recording(self) -> dict:
        """Finish the recording and return its metadata (also saved as
        meta.json). Raises RuntimeError if nothing is being recorded."""
        with self._control_lock:
            with self._rec_lock:
                rec, self._recorder = self._recorder, None
                if rec is None or not rec.active:
                    raise RuntimeError("not recording")
                folder_name = rec.folder.name
                meta = rec.stop()
            self._state.set_recording(None)
            self._state.set_last_recording({**meta, "folder": folder_name})
            return meta

    def _write_to_recorder(self, frame: np.ndarray) -> None:
        with self._rec_lock:
            rec = self._recorder
            if rec is None or not rec.active:
                return
            try:
                rec.write(frame)
                return
            except Exception as exc:  # noqa: BLE001 - disk full, codec failure...
                failure = f"{type(exc).__name__}: {exc}"
        # A recording failure must not stop the inspection; it ends the
        # recording and says so.
        self._state.record_error(f"recording stopped: {failure}")
        try:
            self.stop_recording()
        except Exception:  # noqa: BLE001
            log.exception("could not finalise a failed recording")

    def _publish(self, force: bool = False) -> None:
        """Push recording / playback progress to the dashboard, at most every
        _PUBLISH_EVERY_S -- the page polls once a second, so per-frame updates
        would only cost lock traffic."""
        now = time.monotonic()
        if not force and now - self._last_publish < self._PUBLISH_EVERY_S:
            return
        self._last_publish = now

        rec = self._recorder
        if rec is not None and rec.active:
            self._state.set_recording(
                {"active": True, "folder": rec.folder.name, "frames": rec.frames, "elapsed_s": round(rec.elapsed_s, 1)}
            )
        cap = self._capture
        if cap is not None and not hasattr(cap, "frame_count"):
            captured = getattr(cap, "frames_captured", None)
            skipped = getattr(cap, "frames_skipped", None)
            self._state.set_perf(
                {
                    "capture_fps": _round(getattr(cap, "capture_fps", None)),
                    "process_fps": _round(self._proc_meter.rate()),
                    "skipped_pct": round(100.0 * skipped / captured, 1) if captured else None,
                    "camera": getattr(cap, "settings", None) or None,
                }
            )
        if cap is not None and hasattr(cap, "frame_count"):
            self._state.set_playback(
                {
                    "position": cap.position,
                    "frame_count": cap.frame_count,
                    "fps": round(cap.fps, 2),
                    "paused": cap.paused,
                    "loop": cap.loop,
                }
            )

    # ---- the loop --------------------------------------------------------

    def _loop(self) -> None:
        capture = self._capture
        while self._running:
            try:
                frame = capture.read_frame()
            except StopIteration:
                # A saved video file ran out. That is the end of the input, not
                # a fault: stop cleanly and say so.
                self._running = False
                self._publish(force=True)
                self._state.set_source_status("finished")
                break
            except RuntimeError:
                now = time.monotonic()
                if self._read_failed_since is None:
                    self._read_failed_since = now
                elif now - self._read_failed_since >= self._READ_FAILURE_SECONDS and not self._reported_read_failure:
                    self._reported_read_failure = True
                    self._state.set_source_status("error", "the camera has stopped delivering frames")
                time.sleep(0.01)  # transient capture hiccup; do not spin the CPU
                continue
            if self._reported_read_failure:
                self._state.set_source_status("running")  # it came back
                self._reported_read_failure = False
            self._read_failed_since = None
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

        The raw frame is recorded FIRST and outside the guarded step: a frame
        the pipeline chokes on is exactly the one worth having on disk to
        replay.
        """
        self._write_to_recorder(frame)
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
        self._proc_meter.tick(time.monotonic())
        self._publish()

    # How long to wait for the belt's direction before saying it cannot be read.
    _AUTO_ORIENTATION_PATIENCE_FRAMES = 90

    def _report_line_orientation(self) -> None:
        """Tell the dashboard which way the line ended up, once; and if the
        belt's direction still has not been read after a few seconds, say so
        instead of leaving a station that silently never fires."""
        self._frames_since_reset += 1
        crossing = self._crossing
        if crossing.resolved:
            if crossing.orientation != self._orientation_reported:
                self._orientation_reported = crossing.orientation
                self._state.update_info(line_orientation=crossing.orientation)
        elif self._frames_since_reset >= self._AUTO_ORIENTATION_PATIENCE_FRAMES and not self._auto_warned:
            self._auto_warned = True
            self._state.record_error(
                "could not read the belt's direction, so the trigger line is not active: "
                "set trigger_line.orientation to 'vertical' or 'horizontal' in config.yaml"
            )

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
            observations = self._crossing.update(regions, belt_axis=getattr(belt_roi, "axis", None))
            self._report_line_orientation()
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
                **self._grading_opts,
            )
            record.track_id = track_id
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
            line=(
                {**self._line_cfg, "orientation": self._crossing.orientation}
                if self._crossing is not None and self._crossing.resolved
                else None
            ),
            fired=fired,
            roi=belt_roi,
        )
        # The browser feed is for looking at, not measuring: send it at a width a WiFi link
        # can carry at the camera's frame rate. Detection ran on the full-size frame above.
        stream_w = self._config["dashboard"].get("stream_width")
        if stream_w and annotated.shape[1] > stream_w:
            scale = stream_w / annotated.shape[1]
            annotated = cv2.resize(annotated, (int(stream_w), int(annotated.shape[0] * scale)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        if ok:
            self._state.update_frame(buf.tobytes())
