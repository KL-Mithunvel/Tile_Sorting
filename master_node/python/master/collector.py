"""Collects everything the station publishes into one record per tile.

Subscribes to the whole contract (`tile/#`, `station/#`, `machine/alarm`) and
merges the per-station messages that belong to one tile id into one row, kept
in SQLite so the master has its own copy of the data whether or not the Pi's
log is ever fetched. Nothing here changes how the line behaves; the only thing
it can send is a command (see `commands`), and only when the page's controls
are enabled.

Pure apart from the SQLite file: `handle_message()` takes the time, so it is
tested with synthetic messages.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tiles (
    tile_id TEXT PRIMARY KEY,
    first_ts REAL NOT NULL,
    updated_ts REAL NOT NULL,
    camera_json TEXT,
    acoustic_json TEXT,
    final_json TEXT,
    ack_status TEXT
);
CREATE TABLE IF NOT EXISTS alarms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT,
    message TEXT,
    tile_id TEXT
);
"""

_LEAF_TO_COLUMN = {"camera/result": "camera_json", "acoustic/result": "acoustic_json", "final": "final_json"}

CSV_COLUMNS = [
    "tile_id", "first_seen", "camera_grade", "acoustic_grade", "acoustic_counted", "final_grade",
    "destination", "timed_out", "delivery_ack", "reasons",
]


class MasterCollector:
    def __init__(self, path: str | Path = ":memory:", clock: Callable[[], float] = time.time):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._db.commit()
        self.station_status: Optional[dict] = None
        self.station_status_ts: Optional[float] = None
        self.camera_status: Optional[dict] = None
        self.camera_status_ts: Optional[float] = None
        self.nano_status: Optional[dict] = None
        self.nano_status_ts: Optional[float] = None
        self.alarms: deque = deque(maxlen=100)
        self.counters = {"messages": 0, "bad_messages": 0}

    # ---- input -------------------------------------------------------------

    def attach(self, bus) -> None:
        for f in ("tile/#", "station/#", "machine/alarm"):
            bus.subscribe(f, self.handle_message)

    def handle_message(self, topic: str, payload: bytes, now: Optional[float] = None) -> None:
        now = self._clock() if now is None else now
        with self._lock:
            self.counters["messages"] += 1
            try:
                data = json.loads(payload) if payload else {}
            except ValueError:
                data = None
            if not isinstance(data, dict):
                self.counters["bad_messages"] += 1
                return
            parts = topic.split("/")
            if topic == "station/status":
                self.station_status, self.station_status_ts = data, now
            elif topic == "station/camera/status":
                self.camera_status, self.camera_status_ts = data, now
            elif topic == "station/nano/status":
                self.nano_status, self.nano_status_ts = data, now
            elif topic == "machine/alarm":
                self._store_alarm(data, now)
            elif len(parts) >= 3 and parts[0] == "tile":
                self._store_tile(parts[1], "/".join(parts[2:]), data, now)

    def _store_alarm(self, data: dict, now: float) -> None:
        item = {"ts": data.get("ts", now), "level": data.get("level", "alarm"), "kind": data.get("kind"),
                "message": data.get("message"), "tile_id": data.get("tile_id")}
        self.alarms.appendleft(item)
        self._db.execute(
            "INSERT INTO alarms (ts, kind, message, tile_id) VALUES (?,?,?,?)",
            (item["ts"], item["kind"], item["message"], item["tile_id"]),
        )
        self._db.commit()

    def _store_tile(self, tile_id: str, leaf: str, data: dict, now: float) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO tiles (tile_id, first_ts, updated_ts) VALUES (?,?,?)", (tile_id, now, now)
        )
        if leaf in _LEAF_TO_COLUMN:
            col = _LEAF_TO_COLUMN[leaf]
            self._db.execute(
                f"UPDATE tiles SET {col} = ?, updated_ts = ? WHERE tile_id = ?",
                (json.dumps(data, separators=(",", ":")), now, tile_id),
            )
        elif leaf == "ack":
            self._db.execute(
                "UPDATE tiles SET ack_status = ?, updated_ts = ? WHERE tile_id = ?",
                (str(data.get("status", "ok")), now, tile_id),
            )
        self._db.commit()

    # ---- output ------------------------------------------------------------

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        camera = json.loads(r["camera_json"]) if r["camera_json"] else {}
        acoustic = json.loads(r["acoustic_json"]) if r["acoustic_json"] else {}
        final = json.loads(r["final_json"]) if r["final_json"] else {}
        return {
            "tile_id": r["tile_id"],
            "first_ts": r["first_ts"],
            "camera_grade": camera.get("grade"),
            "acoustic_grade": acoustic.get("acoustic_grade"),
            "acoustic_status": acoustic.get("status"),
            "peak_frequency_hz": acoustic.get("peak_frequency_hz"),
            "final_grade": final.get("final_grade"),
            "destination": final.get("destination"),
            "acoustic_counted": final.get("acoustic_counted"),
            "timed_out": final.get("timed_out"),
            "reasons": final.get("reasons") or [],
            "ack_status": r["ack_status"],
        }

    def recent(self, limit: int = 30) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM tiles ORDER BY first_ts DESC, rowid DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [self._row(r) for r in rows]

    def tally(self) -> dict:
        with self._lock:
            rows = self._db.execute("SELECT final_json FROM tiles WHERE final_json IS NOT NULL").fetchall()
        out: dict[str, int] = {}
        for r in rows:
            g = json.loads(r["final_json"]).get("final_grade")
            if g:
                out[g] = out.get(g, 0) + 1
        return out

    def snapshot(self, now: Optional[float] = None, recent: int = 30) -> dict:
        now = self._clock() if now is None else now

        def age(ts):
            return None if ts is None else round(now - ts, 1)

        with self._lock:
            status = {
                "station": self.station_status,
                "station_age_s": age(self.station_status_ts),
                "camera": self.camera_status,
                "camera_age_s": age(self.camera_status_ts),
                "nano": self.nano_status,
                "nano_age_s": age(self.nano_status_ts),
                "alarms": list(self.alarms)[:30],
                "counters": dict(self.counters),
            }
        status["tally"] = self.tally()
        status["recent"] = self.recent(recent)
        return status

    def export_csv(self) -> str:
        with self._lock:
            rows = self._db.execute("SELECT * FROM tiles ORDER BY first_ts, rowid").fetchall()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(CSV_COLUMNS)
        for r in rows:
            d = self._row(r)
            w.writerow([
                d["tile_id"], d["first_ts"], d["camera_grade"], d["acoustic_grade"], d["acoustic_counted"],
                d["final_grade"], d["destination"], d["timed_out"], d["ack_status"], " | ".join(d["reasons"]),
            ])
        return buf.getvalue()

    def close(self) -> None:
        with self._lock:
            self._db.close()
