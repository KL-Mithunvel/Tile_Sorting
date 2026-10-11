"""Record the raw camera feed to disk so it can be replayed later.

Frames are saved exactly as the camera delivered them (not the annotated
dashboard view), so a recording replayed through the pipeline gives the result
the live run would have. Each recording is its own folder named by the date and
time it started -- see sources.py for the layout.

Two things here are easy to get wrong:

* **The frame rate.** A webcam asked for 15 fps commonly delivers anything from
  7 to 30, and a video file needs its rate stated up front. Writing the nominal
  rate makes every replay play too fast or too slow, and the paced player
  (capture.PacedVideoCapture) trusts the file. So the first `warmup_frames`
  arrivals are buffered, the real rate is measured from their timestamps
  (`estimate_fps`, pure and tested), and only then is the file opened.
* **The codec.** "mp4v" is tried first (plays everywhere), then MJPG in an .avi
  (every OpenCV build can write it). Which one worked is recorded in meta.json.

Hardware/file I/O around a small pure core, like the rest of this package.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Sequence

import cv2
import numpy as np

from camera.sources import META_NAME, new_recording_dir

# (fourcc, container extension), best first.
_CODECS = (("mp4v", ".mp4"), ("MJPG", ".avi"))
_MIN_FPS, _MAX_FPS = 1.0, 120.0


def estimate_fps(timestamps: Sequence[float], fallback: float) -> tuple[float, bool]:
    """(fps, measured) from frame arrival times in seconds.

    `measured` is False when there were too few frames, or they all arrived at
    the same instant, to say anything -- the fallback is used and recorded as
    such. The result is clamped to a plausible range so one stalled frame can't
    produce a 0.2 fps file.
    """
    if len(timestamps) >= 2:
        span = timestamps[-1] - timestamps[0]
        if span > 0:
            fps = (len(timestamps) - 1) / span
            return max(_MIN_FPS, min(_MAX_FPS, fps)), True
    return float(fallback), False


class Recorder:
    """Writes frames into a new date-time-named folder under `root`.

        rec = Recorder(root, source_label="camera 0")
        rec.start()
        for frame in frames:
            rec.write(frame)
        meta = rec.stop()          # dict that was also saved as meta.json
    """

    def __init__(
        self,
        root: Path,
        source_label: str,
        warmup_frames: int = 10,
        fallback_fps: float = 15.0,
        now_fn: Callable[[], datetime] = datetime.now,
        clock: Callable[[], float] = time.monotonic,
    ):
        if warmup_frames < 2:
            raise ValueError(f"Recorder: warmup_frames must be >= 2 to measure a rate, got {warmup_frames}")
        self.root = Path(root)
        self.source_label = source_label
        self.warmup_frames = int(warmup_frames)
        self.fallback_fps = float(fallback_fps)
        self._now_fn = now_fn
        self._clock = clock

        self.folder: Optional[Path] = None
        self._writer: Optional[cv2.VideoWriter] = None
        self._video_path: Optional[Path] = None
        self._codec: Optional[str] = None
        self._size: Optional[tuple[int, int]] = None  # (width, height) of the first frame
        self._fps = 0.0
        self._fps_measured = False
        self._buffer: list[tuple[float, np.ndarray]] = []
        self._frames = 0
        self._resized = 0
        self._started_at: Optional[datetime] = None
        self._t0 = 0.0
        self._t_last = 0.0
        self._finished = False

    @property
    def active(self) -> bool:
        return self.folder is not None and not self._finished

    @property
    def frames(self) -> int:
        return self._frames

    @property
    def elapsed_s(self) -> float:
        """Wall-clock seconds since start(); what the dashboard's REC timer shows."""
        return (self._clock() - self._t0) if self._started_at else 0.0

    def start(self) -> Path:
        if self.folder is not None:
            raise RuntimeError("Recorder: already started")
        self._started_at = self._now_fn()
        self.folder = new_recording_dir(self.root, self._started_at)
        self._t0 = self._clock()
        self._write_meta({"status": "recording"})
        return self.folder

    def write(self, frame: np.ndarray) -> None:
        if not self.active:
            raise RuntimeError("Recorder: write() while not recording")
        now = self._clock()
        self._t_last = now
        self._frames += 1

        if self._writer is None:
            self._buffer.append((now, frame))
            if len(self._buffer) >= self.warmup_frames:
                self._open_writer_and_flush()
            return
        self._write_frame(frame)

    def stop(self) -> dict:
        """Finish the file, write meta.json, and return the metadata. Safe to
        call once; a recording that captured no frames leaves no folder behind."""
        if not self.active:
            raise RuntimeError("Recorder: stop() while not recording")
        try:
            if self._writer is None and self._buffer:
                self._open_writer_and_flush()  # stopped during warmup: use what we have
            if self._writer is not None:
                self._writer.release()
                self._writer = None
        finally:
            self._finished = True

        if self._frames == 0:
            self._discard_empty()
            return {"status": "empty", "frames": 0}

        meta = self._meta("complete")
        self._write_meta(meta)
        return meta

    # ---- internals ------------------------------------------------------

    def _open_writer_and_flush(self) -> None:
        first = self._buffer[0][1]
        height, width = first.shape[:2]
        self._size = (width, height)
        self._fps, self._fps_measured = estimate_fps([t for t, _ in self._buffer], self.fallback_fps)

        for fourcc, ext in _CODECS:
            path = self.folder / f"video{ext}"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*fourcc), self._fps, self._size)
            if writer.isOpened():
                self._writer, self._video_path, self._codec = writer, path, fourcc
                break
            writer.release()
            if path.exists():
                path.unlink()  # a failed open can leave an empty stub behind
        if self._writer is None:
            raise RuntimeError(
                f"Recorder: no video codec could open a writer ({[c for c, _ in _CODECS]}). "
                "This OpenCV build cannot write video."
            )

        for _, buffered in self._buffer:
            self._write_frame(buffered)
        self._buffer = []

    def _write_frame(self, frame: np.ndarray) -> None:
        h, w = frame.shape[:2]
        if (w, h) != self._size:
            # The source changed resolution mid-recording; a video has one size.
            frame = cv2.resize(frame, self._size, interpolation=cv2.INTER_AREA)
            self._resized += 1
        self._writer.write(frame)

    def _meta(self, status: str) -> dict:
        width, height = self._size or (0, 0)
        fps = self._fps or self.fallback_fps
        return {
            "status": status,
            "source": self.source_label,
            "started_at": self._started_at.isoformat(timespec="seconds"),
            "ended_at": self._now_fn().isoformat(timespec="seconds"),
            "frames": self._frames,
            "fps": round(fps, 3),
            "fps_measured": self._fps_measured,
            "duration_s": round(self._frames / fps, 3) if fps else None,
            "wall_seconds": round(self._t_last - self._t0, 3),
            "width": width,
            "height": height,
            "codec": self._codec,
            "video": self._video_path.name if self._video_path else None,
            "frames_resized": self._resized,
        }

    def _write_meta(self, meta: dict) -> None:
        base = {"source": self.source_label, "started_at": self._started_at.isoformat(timespec="seconds")}
        with open(self.folder / META_NAME, "w", encoding="utf-8") as f:
            json.dump({**base, **meta}, f, indent=2)

    def _discard_empty(self) -> None:
        try:
            for child in self.folder.iterdir():
                child.unlink()
            self.folder.rmdir()
        except OSError:
            pass
