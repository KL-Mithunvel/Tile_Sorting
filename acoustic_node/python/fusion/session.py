"""The session "bag": everything one data-collection run produced, in one folder.

    data/sessions/2026-10-19_16-05-12/
        manifest.json     what this is: labels, config, git commit, every file's size + SHA-256
        events.jsonl      every message on the station bus, one JSON line each, with a timestamp
                          (camera results, taps, Nano status, grades, alarms, session commands)
        strikes.json      the labelled striker events: each `retract` (coil energised) and each
                          `strike` (coil released), with why it happened and the operator's labels
        audio/            the microphone, continuous, in short WAV chunks (a crash loses one chunk)
            audio_0001.wav ...   + chunks.jsonl (wall-clock start and sample count of each)
        video/            the camera's raw feed (written by the camera service)
            video.mp4, frames.csv (wall time of every frame), meta.json
        clips/            derived: one short labelled WAV per event + clips.csv   (rebuildable)
        tiles/            derived: the camera's picture of each tile that was graded

Raw streams (audio, video, events) are never edited. Clips and labels are derived from them, so a
different window or a corrected label is a re-run, not a re-collection.

Time: every record carries wall-clock `t` (seconds since the epoch, this machine's clock) and the
audio chunks say when their first sample was taken, so audio, video and events line up. They agree
to the accuracy of that clock and of the audio device's start-up (tens of ms), NOT to the sample.

Standard library + numpy. The clock, the disk and the git lookup are injectable so it is tested
with synthetic time.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import queue
import re
import shutil
import subprocess
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np

log = logging.getLogger(__name__)

SCHEMA = 1
MANIFEST = "manifest.json"
EVENTS = "events.jsonl"
STRIKES = "strikes.json"
SUBDIRS = ("audio", "video", "clips", "tiles")

# What an operator can say about a strike. Free text is allowed in `note` and `tile_ref`.
CONDITIONS = ("tile", "no_tile", "ambient")
DEFECTS = ("fine", "chipped", "cracked", "both", "unsure")
LABEL_FIELDS = ("condition", "defect", "tier", "tile_ref", "note")

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _now_dirname(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d_%H-%M-%S")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _write_json_atomic(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def git_commit(repo_root: Path) -> Optional[str]:
    """Best effort: the commit the code was at (+ '-dirty' if it has local changes)."""
    try:
        sha = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        if sha.returncode != 0:
            return None
        dirty = subprocess.run(["git", "-C", str(repo_root), "status", "--porcelain"],
                               capture_output=True, text=True, timeout=5)
        return sha.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "")
    except Exception:  # noqa: BLE001 - no git on the Pi is fine
        return None


class AudioChunkWriter:
    """Continuous mono audio as consecutive WAV files of ~chunk_s seconds.

    16-bit PCM (the same as the per-tile WAVs). Every finished chunk is described in
    `chunks.jsonl` (file, wall time of its first sample, samples), so the stream can be
    re-assembled and any wall-clock instant found. Not thread-safe: one writer thread."""

    def __init__(self, folder: Path, sample_rate: int, chunk_s: float = 30.0):
        self.folder, self.sample_rate = Path(folder), int(sample_rate)
        self.chunk_samples = max(1, int(chunk_s * sample_rate))
        self.index = 0
        self.total_samples = 0
        self._w: Optional[wave.Wave_write] = None
        self._name = ""
        self._chunk_t0 = 0.0
        self._chunk_first = 0
        self._chunk_n = 0
        self.chunks: list[dict] = []

    def write(self, block, wall_start: float) -> None:
        """`block`: float samples in [-1, 1]; `wall_start`: when its first sample was taken."""
        pcm = (np.clip(np.asarray(block, dtype=np.float64), -1.0, 1.0) * 32767.0).astype("<i2")
        pos = 0
        while pos < len(pcm):
            if self._w is None:
                self._open(wall_start + pos / self.sample_rate)
            take = min(len(pcm) - pos, self.chunk_samples - self._chunk_n)
            self._w.writeframes(pcm[pos:pos + take].tobytes())
            self._chunk_n += take
            self.total_samples += take
            pos += take
            if self._chunk_n >= self.chunk_samples:
                self._close()

    def _open(self, wall_t0: float) -> None:
        self.index += 1
        self._name = f"audio_{self.index:04d}.wav"
        self._w = wave.open(str(self.folder / self._name), "wb")
        self._w.setnchannels(1)
        self._w.setsampwidth(2)
        self._w.setframerate(self.sample_rate)
        self._chunk_t0, self._chunk_first, self._chunk_n = wall_t0, self.total_samples, 0

    def _close(self) -> None:
        if self._w is None:
            return
        self._w.close()
        self._w = None
        rec = {"file": f"audio/{self._name}", "start_wall": self._chunk_t0,
               "start_sample": self._chunk_first, "samples": self._chunk_n, "sample_rate": self.sample_rate}
        self.chunks.append(rec)
        with open(self.folder / "chunks.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def close(self) -> None:
        self._close()


class SessionRecorder:
    """One session at a time. start() / stop() bracket it; the rest are cheap, thread-safe
    calls from the audio callback, the bus thread and the GUI."""

    def __init__(
        self,
        root: Path,
        repo_root: Optional[Path] = None,
        chunk_s: float = 30.0,
        min_free_gb: float = 2.0,
        stop_free_gb: float = 0.5,
        clip_pre_s: float = 0.3,
        clip_post_s: float = 1.2,
        clock: Callable[[], float] = time.time,
        free_bytes: Callable[[Path], int] = lambda p: shutil.disk_usage(p).free,
        git: Callable[[Path], Optional[str]] = git_commit,
        queue_blocks: int = 4000,
    ):
        self.root = Path(root)
        self.repo_root = Path(repo_root) if repo_root else self.root
        self.chunk_s, self.clip_pre_s, self.clip_post_s = float(chunk_s), float(clip_pre_s), float(clip_post_s)
        self.min_free, self.stop_free = float(min_free_gb) * 1024 ** 3, float(stop_free_gb) * 1024 ** 3
        self._clock, self._free, self._git = clock, free_bytes, git
        self._queue_blocks = int(queue_blocks)

        self._lock = threading.RLock()
        self._events_lock = threading.Lock()
        self.dir: Optional[Path] = None
        self.id: Optional[str] = None
        self._manifest: dict = {}
        self._events_f = None
        self._audio_q: "queue.Queue" = queue.Queue(maxsize=self._queue_blocks)
        self._audio_thread: Optional[threading.Thread] = None
        self._audio: Optional[AudioChunkWriter] = None
        self._audio_rate = 0
        self._strikes: list[dict] = []
        self._labels: dict = {k: None for k in LABEL_FIELDS}
        self._labels["condition"] = "tile"
        self._n = {"strike": 0, "retract": 0, "ambient": 0, "bus_events": 0, "dropped_audio_blocks": 0}
        self._last_retract: Optional[dict] = None
        self._started = 0.0
        self.low_disk = False

    # ---- state ------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self.dir is not None

    def status(self) -> dict:
        with self._lock:
            if self.dir is None:
                return {"active": False}
            try:
                free = int(self._free(self.root))
            except OSError:
                free = None
            return {
                "active": True, "id": self.id, "dir": self._rel(self.dir),
                "started": self._started, "elapsed_s": round(self._clock() - self._started, 1),
                "run_type": self._manifest.get("run_type"), "note": self._manifest.get("note"),
                "labels": dict(self._labels), "counts": dict(self._n),
                "audio_s": round((self._audio.total_samples / self._audio_rate), 1) if self._audio else 0.0,
                "free_gb": round(free / 1024 ** 3, 2) if free is not None else None, "low_disk": self.low_disk,
            }

    def _rel(self, p: Path) -> str:
        try:
            return p.resolve().relative_to(self.repo_root.resolve()).as_posix()
        except ValueError:
            return str(p)

    # ---- lifecycle --------------------------------------------------------------

    def start(self, run_type: str = "tile", note: str = "", labels: Optional[dict] = None,
              sample_rate: int = 44100, config: Optional[dict] = None, extra: Optional[dict] = None) -> dict:
        with self._lock:
            if self.dir is not None:
                raise RuntimeError(f"session {self.id} is already running")
            self.root.mkdir(parents=True, exist_ok=True)
            free = int(self._free(self.root))
            if free < self.min_free:
                raise RuntimeError(f"only {free / 1024 ** 3:.1f} GB free; a session needs at least "
                                   f"{self.min_free / 1024 ** 3:.1f} GB (station.session.min_free_gb)")
            t = self._clock()
            base = _now_dirname(t)
            d, k = self.root / base, 1
            while d.exists():
                k += 1
                d = self.root / f"{base}_{k}"
            for sub in SUBDIRS:
                (d / sub).mkdir(parents=True)
            self.dir, self.id, self._started = d, d.name, t
            self._strikes, self._last_retract, self.low_disk = [], None, False
            self._n = {k2: 0 for k2 in self._n}
            self._labels = {k2: None for k2 in LABEL_FIELDS}
            self._labels["condition"] = "tile"
            self.set_labels(**(labels or {}))
            self._audio_rate = int(sample_rate)
            self._audio = AudioChunkWriter(d / "audio", sample_rate, self.chunk_s)
            self._audio_q = queue.Queue(maxsize=self._queue_blocks)
            self._events_f = open(d / EVENTS, "a", encoding="utf-8")
            self._manifest = {
                "schema": SCHEMA, "id": d.name, "status": "recording",
                "started": t, "started_iso": datetime.fromtimestamp(t).isoformat(timespec="seconds"),
                "run_type": run_type, "note": note, "initial_labels": dict(self._labels),
                "code_commit": self._git(self.repo_root),
                "audio": {"sample_rate": int(sample_rate), "channels": 1, "format": "int16 PCM WAV",
                          "chunk_s": self.chunk_s},
                "time_note": "t is this machine's wall clock; audio/video/events agree to tens of ms, not to the sample",
                "config": config or {}, **(extra or {}),
            }
            _write_json_atomic(d / MANIFEST, self._manifest)
            self._audio_thread = threading.Thread(target=self._audio_loop, name="session-audio", daemon=True)
            self._audio_thread.start()
        self.log_event("session/start", {"id": d.name, "run_type": run_type, "note": note})
        return self.status()

    def stop(self, extract: bool = True, camera_wait: Optional[Callable[[Path], None]] = None) -> dict:
        """Close the streams, cut the clips, and write the final manifest. `camera_wait(dir)`
        (optional) is called before the manifest is sealed, to give the camera service time to
        finish its video file."""
        with self._lock:
            if self.dir is None:
                raise RuntimeError("no session is running")
            d, sid = self.dir, self.id
            self.log_event("session/stop", {"id": sid})
            thread = self._audio_thread
            self._audio_q.put(None)
        if thread is not None:
            thread.join(timeout=30)
        with self._lock:
            if self._audio is not None:
                self._audio.close()
            chunks = list(self._audio.chunks) if self._audio else []
            samples = self._audio.total_samples if self._audio else 0
            with self._events_lock:
                if self._events_f is not None:
                    self._events_f.close()
                    self._events_f = None
            manifest = dict(self._manifest)
            manifest["audio"] = {**manifest["audio"], "chunks": len(chunks), "samples": samples,
                                 "dropped_blocks": self._n["dropped_audio_blocks"]}
            manifest["counts"] = dict(self._n)
            manifest["stopped"] = self._clock()
            manifest["duration_s"] = round(manifest["stopped"] - self._started, 2)
            self._write_strikes()
            self.dir = self.id = None
            self._audio = None
        if camera_wait is not None:
            try:
                camera_wait(d)
            except Exception:  # noqa: BLE001
                log.exception("waiting for the camera's video failed")
        if extract:
            try:
                extract_clips(d, self.clip_pre_s, self.clip_post_s)
            except Exception:  # noqa: BLE001 - clips are derived and rebuildable; the raw data is safe
                log.exception("could not cut the clips for %s", sid)
        manifest["status"] = "complete"
        seal_manifest(d, manifest)
        return manifest

    # ---- the three input streams ------------------------------------------------

    def add_audio(self, block, wall_end: Optional[float] = None) -> None:
        """From the audio callback: must never block. `wall_end` = when the block's last sample was taken."""
        if self.dir is None:
            return
        wall_end = self._clock() if wall_end is None else wall_end
        try:
            self._audio_q.put_nowait((np.array(block, dtype=np.float32, copy=True), wall_end))
        except queue.Full:
            self._n["dropped_audio_blocks"] += 1

    def _audio_loop(self) -> None:
        last_check = 0.0
        while True:
            item = self._audio_q.get()
            if item is None:
                return
            block, wall_end = item
            try:
                self._audio.write(block, wall_end - len(block) / self._audio_rate)
            except Exception:  # noqa: BLE001 - disk full etc.
                log.exception("session audio write failed")
                self._n["dropped_audio_blocks"] += 1
            now = time.monotonic()
            if now - last_check > 5.0:
                last_check = now
                try:
                    if int(self._free(self.root)) < self.stop_free:
                        self.low_disk = True
                except OSError:
                    pass

    def log_event(self, topic: str, payload) -> None:
        """One record in events.jsonl. `payload` is a dict, JSON text/bytes, or anything."""
        if isinstance(payload, (bytes, bytearray)):
            try:
                payload = json.loads(payload)
            except ValueError:
                payload = payload.decode("utf-8", errors="replace")
        elif isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                pass
        line = json.dumps({"t": self._clock(), "topic": topic, "payload": payload}, separators=(",", ":"), default=str)
        with self._events_lock:
            if self._events_f is None:
                return
            self._events_f.write(line + "\n")
            self._events_f.flush()
            self._n["bus_events"] += 1

    # ---- labelled striker events --------------------------------------------------

    def set_labels(self, **fields) -> dict:
        """The labels every following event gets, until changed (like the camera's batch verdict)."""
        with self._lock:
            for k, v in fields.items():
                if k not in LABEL_FIELDS:
                    raise ValueError(f"unknown label {k!r}; use {LABEL_FIELDS}")
                if v in ("", None):
                    v = None
                elif k == "condition" and v not in CONDITIONS:
                    raise ValueError(f"condition must be one of {CONDITIONS}")
                elif k == "defect" and v not in DEFECTS:
                    raise ValueError(f"defect must be one of {DEFECTS}")
                self._labels[k] = v
            return dict(self._labels)

    def note_striker(self, kind: str, source: str, nano_ms: Optional[int], seq: Optional[int],
                     t: Optional[float] = None, tile_present=None, tof_mm=None) -> dict:
        """A `retract` or `strike` from the Nano. Gets the current labels."""
        if kind not in ("retract", "strike"):
            raise ValueError(kind)
        with self._lock:
            if self.dir is None:
                raise RuntimeError("no session is running")
            self._n[kind] += 1
            ev = {
                "id": f"{'R' if kind == 'retract' else 'S'}{self._n[kind]:04d}", "kind": kind, "source": source,
                "t": self._clock() if t is None else t, "nano_ms": nano_ms, "nano_seq": seq,
                "tile_present": tile_present, "tof_mm": tof_mm, "cycle": None, "tile_id": None,
                "labels": dict(self._labels),
            }
            if kind == "retract":
                self._last_retract = ev
            else:
                lr = self._last_retract
                if lr is not None and lr.get("cycle") is None and ev["t"] - lr["t"] < 6 * 3600.0:   # a parked striker can wait a long time
                    ev["cycle"] = lr["id"]            # the strike that ends this retraction
                    lr["cycle"] = ev["id"]
                    if nano_ms is not None and lr.get("nano_ms") is not None:
                        ev["retract_to_strike_ms"] = (nano_ms - lr["nano_ms"]) & 0xFFFFFFFF
            self._strikes.append(ev)
            self._write_strikes()
            return dict(ev)

    def mark(self, kind: str = "ambient", **fields) -> dict:
        """A labelled moment with no striker involved (room noise, a note). `ambient` gets a clip."""
        if kind not in ("ambient", "note"):
            raise ValueError("kind must be ambient or note")
        with self._lock:
            if self.dir is None:
                raise RuntimeError("no session is running")
            if kind == "ambient":
                self._n["ambient"] += 1
            ev = {"id": f"A{self._n['ambient']:04d}" if kind == "ambient" else f"N{len(self._strikes) + 1:04d}",
                  "kind": kind, "source": "operator", "t": self._clock(), "labels": {**self._labels, **fields},
                  "cycle": None, "tile_id": None}
            self._strikes.append(ev)
            self._write_strikes()
            return dict(ev)

    def bind_tile(self, tile_id: str) -> Optional[str]:
        """The station paired the oldest not-yet-paired ToF strike with this tile."""
        with self._lock:
            for ev in self._strikes:
                if ev["kind"] == "strike" and ev["source"] == "tof" and ev["tile_id"] is None:
                    ev["tile_id"] = tile_id
                    if ev.get("cycle"):
                        for r in self._strikes:
                            if r["id"] == ev["cycle"]:
                                r["tile_id"] = tile_id
                    self._write_strikes()
                    return ev["id"]
        return None

    def relabel(self, event_id: str, **fields) -> dict:
        with self._lock:
            ev = next((e for e in self._strikes if e["id"] == event_id), None)
            if ev is None:
                raise ValueError(f"no event {event_id!r} in this session")
            old = dict(self._labels)
            try:
                new = self.set_labels(**fields)
            finally:
                self._labels = old
            ev["labels"] = {**ev["labels"], **{k: new[k] for k in fields}}
            self._write_strikes()
            return dict(ev)

    def events(self, last: int = 20) -> list[dict]:
        with self._lock:
            return [dict(e) for e in self._strikes[-last:]]

    def _write_strikes(self) -> None:
        if self.dir is not None:
            _write_json_atomic(self.dir / STRIKES, self._strikes)

    # ---- the camera's pictures ------------------------------------------------------

    def keep_tile_picture(self, tile_id: str, source: str | Path, data_root: Path) -> Optional[str]:
        """Copy the camera's picture of a graded tile into tiles/. Only from under data_root."""
        with self._lock:
            if self.dir is None:
                return None
            d = self.dir
        try:
            src = Path(source).resolve()
            src.relative_to(Path(data_root).resolve())
            dest = d / "tiles" / f"{_SAFE.sub('_', tile_id)[:96]}{src.suffix.lower()}"
            shutil.copyfile(src, dest)
            return dest.name
        except (OSError, ValueError):
            return None


# ---- after the fact (pure file work, also usable from the command line) ------------------


def read_audio_index(session_dir: Path) -> list[dict]:
    idx = Path(session_dir) / "audio" / "chunks.jsonl"
    if not idx.is_file():
        return []
    out = []
    for line in idx.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass            # a torn last line after a crash
    return out


def read_audio_window(session_dir: Path, chunks: list[dict], t_start: float, t_end: float):
    """float32 samples covering [t_start, t_end] wall time, zero-padded where no audio exists;
    returns (samples, sample_rate). Chunks are consecutive but the wall clock may drift a little
    between them, so each chunk is placed by its own start time."""
    if not chunks:
        return np.zeros(0, dtype=np.float32), 0
    sr = int(chunks[0]["sample_rate"])
    n = max(0, int(round((t_end - t_start) * sr)))
    out = np.zeros(n, dtype=np.float32)
    for c in chunks:
        c_start, c_end = c["start_wall"], c["start_wall"] + c["samples"] / sr
        if c_end <= t_start or c_start >= t_end:
            continue
        path = Path(session_dir) / c["file"]
        if not path.is_file():
            continue
        with wave.open(str(path), "rb") as w:
            first = max(0, int(round((t_start - c_start) * sr)))
            w.setpos(min(first, w.getnframes()))
            take = min(w.getnframes() - first, int(round((t_end - max(t_start, c_start)) * sr)))
            if take <= 0:
                continue
            data = np.frombuffer(w.readframes(take), dtype="<i2").astype(np.float32) / 32767.0
        dst = int(round((max(t_start, c_start) - t_start) * sr))
        out[dst:dst + len(data)] = data[: max(0, n - dst)]
    return out, sr


def extract_clips(session_dir: Path, pre_s: float = 0.3, post_s: float = 1.2) -> list[dict]:
    """Cut one labelled WAV per event in strikes.json from the continuous audio, write
    clips/*.wav + clips/clips.csv. Re-runnable with a different window. Returns the rows."""
    d = Path(session_dir)
    strikes_path = d / STRIKES
    if not strikes_path.is_file():
        return []
    events = json.loads(strikes_path.read_text(encoding="utf-8"))
    chunks = read_audio_index(d)
    session_id = d.name
    rows = []
    (d / "clips").mkdir(exist_ok=True)
    for ev in events:
        if ev["kind"] not in ("retract", "strike", "ambient"):
            continue
        t0 = ev["t"] - (0.0 if ev["kind"] == "ambient" else pre_s)
        t1 = ev["t"] + (post_s if ev["kind"] != "ambient" else pre_s + post_s)
        samples, sr = read_audio_window(d, chunks, t0, t1)
        if sr == 0 or len(samples) == 0:
            continue
        name = f"{ev['id']}_{ev['kind']}.wav"
        pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
        with wave.open(str(d / "clips" / name), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(pcm.tobytes())
        lab = ev.get("labels") or {}
        rows.append({
            "session": session_id, "clip": f"clips/{name}", "event_id": ev["id"], "kind": ev["kind"],
            "source": ev.get("source"), "cycle": ev.get("cycle"), "tile_id": ev.get("tile_id"),
            "condition": lab.get("condition"), "defect": lab.get("defect"), "tier": lab.get("tier"),
            "tile_ref": lab.get("tile_ref"), "note": lab.get("note"),
            "t_wall": ev["t"], "event_offset_s": round(ev["t"] - t0, 4), "sample_rate": sr,
            "tile_present": ev.get("tile_present"), "retract_to_strike_ms": ev.get("retract_to_strike_ms"),
        })
    with open(d / "clips" / "clips.csv", "w", newline="", encoding="utf-8") as f:
        cols = list(rows[0].keys()) if rows else ["session", "clip", "event_id", "kind"]
        wr = csv.DictWriter(f, fieldnames=cols)
        wr.writeheader()
        wr.writerows(rows)
    return rows


def seal_manifest(session_dir: Path, manifest: Optional[dict] = None) -> dict:
    """Hash every file in the session and write manifest.json. Safe to call again later (e.g.
    after the camera's video finished, or clips were re-cut): it only ever recomputes."""
    d = Path(session_dir)
    m = manifest if manifest is not None else json.loads((d / MANIFEST).read_text(encoding="utf-8"))
    files = []
    for p in sorted(d.rglob("*")):
        if p.is_file() and p.name != MANIFEST and not p.name.endswith(".part"):
            files.append({"path": p.relative_to(d).as_posix(), "size": p.stat().st_size, "sha256": _sha256(p)})
    m["files"] = files
    m["total_bytes"] = sum(f["size"] for f in files)
    vm = d / "video" / "meta.json"
    if vm.is_file():
        try:
            m["video"] = json.loads(vm.read_text(encoding="utf-8"))
        except ValueError:
            m["video"] = {"status": "unreadable meta.json"}
    elif "video" not in m:
        m["video"] = None
    _write_json_atomic(d / MANIFEST, m)
    return m


def verify_session(session_dir: Path) -> list[str]:
    """Problems found re-checking every file against the manifest (empty list = intact)."""
    d = Path(session_dir)
    try:
        m = json.loads((d / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"manifest unreadable: {exc}"]
    bad = []
    for f in m.get("files", []):
        p = d / f["path"]
        if not p.is_file():
            bad.append(f"missing {f['path']}")
        elif p.stat().st_size != f["size"] or _sha256(p) != f["sha256"]:
            bad.append(f"changed {f['path']}")
    return bad


def mark_interrupted(root: Path) -> list[str]:
    """A session folder still saying `recording` belongs to a run that never stopped cleanly
    (power cut, crash). Seal what is on disk and say so. Returns the ids fixed."""
    fixed = []
    for d in sorted(Path(root).glob("*/")):
        mp = d / MANIFEST
        if not mp.is_file():
            continue
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if m.get("status") != "recording":
            continue
        m["status"] = "interrupted"
        m["audio"] = {**m.get("audio", {}), "chunks_found": len(read_audio_index(d))}
        try:
            extract_clips(d)
        except Exception:  # noqa: BLE001
            log.exception("could not cut clips for interrupted session %s", d.name)
        seal_manifest(d, m)
        fixed.append(d.name)
    return fixed


def list_sessions(root: Path) -> list[dict]:
    """Newest first: id, status, size, a few facts. Reads manifests only."""
    out = []
    for d in sorted(Path(root).glob("*/"), reverse=True):
        mp = d / MANIFEST
        if not mp.is_file():
            continue
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
        except ValueError:
            continue
        out.append({
            "id": d.name, "status": m.get("status"), "started_iso": m.get("started_iso"),
            "duration_s": m.get("duration_s"), "run_type": m.get("run_type"), "note": m.get("note"),
            "total_bytes": m.get("total_bytes"), "files": len(m.get("files", [])),
            "counts": m.get("counts"), "has_video": bool(m.get("video")),
        })
    return out


def session_file(root: Path, session_id: str, rel: str) -> Optional[Path]:
    """A file inside a session, or None. Refuses anything that resolves outside the session."""
    base = (Path(root) / session_id).resolve()
    try:
        base.relative_to(Path(root).resolve())
        p = (base / rel).resolve()
        p.relative_to(base)
    except (OSError, ValueError):
        return None
    return p if p.is_file() else None


def main(argv=None) -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Work on a recorded session folder.")
    ap.add_argument("cmd", choices=["verify", "clips", "seal", "list"])
    ap.add_argument("path", help="a session folder (or, for `list`, the sessions root)")
    ap.add_argument("--pre", type=float, default=0.3)
    ap.add_argument("--post", type=float, default=1.2)
    a = ap.parse_args(argv)
    p = Path(a.path)
    if a.cmd == "list":
        for s in list_sessions(p):
            print(s)
    elif a.cmd == "verify":
        problems = verify_session(p)
        print("intact" if not problems else "\n".join(problems))
        raise SystemExit(1 if problems else 0)
    elif a.cmd == "clips":
        print(f"{len(extract_clips(p, a.pre, a.post))} clips")
        seal_manifest(p)
    else:
        seal_manifest(p)
        print("sealed")


if __name__ == "__main__":
    main()
