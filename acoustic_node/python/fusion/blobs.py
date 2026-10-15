"""Evidence files the Pi keeps for each tile: the picture the camera graded,
the tap recording, and a small preview of its spectrum.

The database holds the numbers; this holds the things a person (or the master's
AI) needs to look at to check them. Each file is registered in the store's
`blobs` table with a SHA-256, so a master can fetch it and prove it arrived
intact.

Disk is bounded by a budget: when the live files exceed `budget_bytes`, the
OLDEST are deleted first and marked `pruned` (the row and its history stay
forever, only the file goes). A file that has been pruned is reported as gone,
never silently missing.

Standard library only (the WAV is written with `wave`). Takes the clock so it
is tested with synthetic time.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Optional

from fusion.store import TileStore

log = logging.getLogger(__name__)

_SAFE = re.compile(r"[^A-Za-z0-9._-]")

MIME = {".wav": "audio/wav", ".json": "application/json", ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg", ".png": "image/png"}


def _safe(part: str) -> str:
    return _SAFE.sub("_", str(part))[:96] or "_"


class BlobStore:
    def __init__(self, root: str | Path, store: TileStore, budget_bytes: int = 20 * 1024 ** 3,
                 clock: Callable[[], float] = time.time):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._store = store
        self.budget_bytes = int(budget_bytes)
        self._clock = clock
        self._lock = threading.Lock()

    # ---- writing -----------------------------------------------------------

    def _write(self, tile_id: str, name: str, suffix: str, data: bytes) -> dict:
        now = self._clock()
        rel = Path(time.strftime("%Y-%m-%d", time.gmtime(now))) / _safe(tile_id) / f"{_safe(name)}{suffix}"
        path = self.root / rel
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".part")
            tmp.write_bytes(data)
            tmp.replace(path)                       # a half-written file is never registered
            digest = hashlib.sha256(data).hexdigest()
            self._store.add_blob(tile_id, name, rel.as_posix(), digest, len(data), now, MIME.get(suffix.lower()))
        self.prune()
        return {"name": name, "sha256": digest, "size": len(data)}

    def save_bytes(self, tile_id: str, name: str, data: bytes, suffix: str) -> dict:
        return self._write(tile_id, name, suffix, data)

    def save_file(self, tile_id: str, name: str, source: str | Path) -> Optional[dict]:
        """Copy a file (e.g. the camera's snapshot) in, so the Pi's record does
        not depend on someone else's folder. None if it can't be read."""
        try:
            src = Path(source)
            return self._write(tile_id, name, src.suffix.lower() or ".bin", src.read_bytes())
        except OSError as exc:
            log.warning("could not keep %s for %s: %s", source, tile_id, exc)
            return None

    def save_json(self, tile_id: str, name: str, obj: dict) -> dict:
        return self._write(tile_id, name, ".json", json.dumps(obj, separators=(",", ":"), default=str).encode())

    def save_wav(self, tile_id: str, name: str, samples, sample_rate: int) -> dict:
        """`samples`: float array in [-1, 1] (mono). Written as 16-bit PCM."""
        import numpy as np

        pcm = (np.clip(np.asarray(samples, dtype=np.float64), -1.0, 1.0) * 32767.0).astype("<i2")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(int(sample_rate))
            w.writeframes(pcm.tobytes())
        return self._write(tile_id, name, ".wav", buf.getvalue())

    # ---- reading -----------------------------------------------------------

    def path_for(self, tile_id: str, name: str) -> Optional[Path]:
        """The file for a registered, not-pruned blob, or None. Refuses anything
        that resolves outside the root (a corrupt row must not read other files)."""
        row = self._store.get_blob(tile_id, name)
        if not row or row["pruned"]:
            return None
        try:
            p = (self.root / row["rel_path"]).resolve()
            p.relative_to(self.root)
        except (OSError, ValueError):
            return None
        return p if p.is_file() else None

    # ---- budget ------------------------------------------------------------

    def used_bytes(self) -> int:
        return int(self._store.manifest()["blob_bytes"])

    def prune(self) -> int:
        """Delete oldest files until under budget. Returns how many were pruned."""
        if self.used_bytes() <= self.budget_bytes:
            return 0
        n, used = 0, self.used_bytes()
        for row in self._store.live_blobs_oldest_first():
            if used <= self.budget_bytes:
                break
            try:
                (self.root / row["rel_path"]).unlink(missing_ok=True)
            except OSError as exc:
                log.warning("could not delete %s: %s", row["rel_path"], exc)
                continue
            self._store.mark_blob_pruned(row["tile_id"], row["name"], self._clock())
            used -= row["size"]
            n += 1
        return n


class EvidenceRecorder:
    """What the station hands to the blob store, one method per source. Never
    raises: losing a picture must not cost the station a tile's decision."""

    def __init__(self, blobs: BlobStore, keep_audio: bool = True):
        self._blobs = blobs
        self._keep_audio = keep_audio

    def camera_result(self, tile_id: str, camera: dict) -> None:
        path = (camera or {}).get("snapshot_path")
        if not path:
            return
        try:
            self._blobs.save_file(tile_id, "camera", path)
        except Exception:  # noqa: BLE001
            log.exception("could not keep the camera picture for %s", tile_id)

    def tap(self, tile_id: str, clip, sample_rate: int, preview: Optional[dict] = None) -> None:
        try:
            if self._keep_audio and clip is not None:
                self._blobs.save_wav(tile_id, "tap", clip, sample_rate)
            if preview:
                self._blobs.save_json(tile_id, "tap_preview", preview)
        except Exception:  # noqa: BLE001
            log.exception("could not keep the tap recording for %s", tile_id)
