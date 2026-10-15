"""The station's own record of every tile, in SQLite.

The master PC is optional (the line runs without it), so the Pi keeps the data
itself: what the camera said, what the tap said, what was decided, where it
went, and whether the UNO Q acknowledged it. The master can read it over MQTT
or an export; nothing here depends on it.

One row per tile, updated as the tile moves through its states. JSON blobs hold
the two stations' full results so a later analysis is not limited to the
columns chosen today. Standard library only.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tiles (
    tile_id        TEXT PRIMARY KEY,
    seq            INTEGER,
    created_ts     REAL NOT NULL,
    updated_ts     REAL NOT NULL,
    state          TEXT NOT NULL,
    camera_grade   TEXT,
    camera_json    TEXT,
    acoustic_grade TEXT,
    acoustic_json  TEXT,
    acoustic_counted INTEGER NOT NULL DEFAULT 0,
    final_grade    TEXT,
    destination    TEXT,
    reasons_json   TEXT,
    delivery       TEXT NOT NULL DEFAULT 'pending',
    attempts       INTEGER NOT NULL DEFAULT 0,
    ack_ts         REAL,
    transit_s      REAL,
    placement      TEXT,
    placement_reason TEXT,
    tile_in_cup    INTEGER,
    placed_ts      REAL
);
CREATE INDEX IF NOT EXISTS tiles_created ON tiles (created_ts);

-- Append-only history: every change to a tile, every alarm, every health
-- change. `seq` is the sync cursor (the master asks for "everything after N").
CREATE TABLE IF NOT EXISTS events (
    seq       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    kind      TEXT NOT NULL,
    tile_id   TEXT,
    data_json TEXT
);
CREATE INDEX IF NOT EXISTS events_tile ON events (tile_id);

-- Evidence files kept beside the database (tile picture, tap audio, ...).
CREATE TABLE IF NOT EXISTS blobs (
    tile_id    TEXT NOT NULL,
    name       TEXT NOT NULL,
    rel_path   TEXT NOT NULL,
    sha256     TEXT NOT NULL,
    size       INTEGER NOT NULL,
    mime       TEXT,
    created_ts REAL NOT NULL,
    pruned     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (tile_id, name)
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

_JSON_FIELDS = {"camera_json", "acoustic_json", "reasons_json"}
_COLUMNS = {
    "seq", "created_ts", "updated_ts", "state", "camera_grade", "camera_json",
    "acoustic_grade", "acoustic_json", "acoustic_counted", "final_grade",
    "destination", "reasons_json", "delivery", "attempts", "ack_ts", "transit_s",
    "placement", "placement_reason", "tile_in_cup", "placed_ts",
}


class TileStore:
    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(_SCHEMA)
            # A database written before a column existed: add it rather than
            # refuse to start (the station's record must survive an upgrade).
            have = {r["name"] for r in self._db.execute("PRAGMA table_info(tiles)")}
            for column, kind in (("transit_s", "REAL"), ("placement", "TEXT"), ("placement_reason", "TEXT"),
                                 ("tile_in_cup", "INTEGER"), ("placed_ts", "REAL")):
                if column not in have:
                    self._db.execute(f"ALTER TABLE tiles ADD COLUMN {column} {kind}")
            if self._db.execute("SELECT value FROM meta WHERE key = 'store_id'").fetchone() is None:
                # Names this database. A master that sees a different id knows the
                # Pi's record was replaced and its cursor means nothing any more.
                self._db.execute("INSERT INTO meta (key, value) VALUES ('store_id', ?)", (uuid.uuid4().hex,))
            self._db.commit()

    @property
    def store_id(self) -> str:
        with self._lock:
            return self._db.execute("SELECT value FROM meta WHERE key = 'store_id'").fetchone()["value"]

    def upsert(self, tile_id: str, now: float, **fields) -> None:
        """Create the row if new, then set the given columns. Dict values for
        the *_json columns are serialised here."""
        unknown = set(fields) - _COLUMNS
        if unknown:
            raise ValueError(f"unknown tile column(s): {sorted(unknown)}")
        event_data = dict(fields)
        for name in _JSON_FIELDS & set(fields):
            if fields[name] is not None and not isinstance(fields[name], str):
                fields[name] = json.dumps(fields[name], separators=(",", ":"), default=str)
        if "acoustic_counted" in fields:
            fields["acoustic_counted"] = int(bool(fields["acoustic_counted"]))
        if fields.get("tile_in_cup") is not None:
            fields["tile_in_cup"] = int(bool(fields["tile_in_cup"]))
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO tiles (tile_id, created_ts, updated_ts, state) VALUES (?,?,?,?)",
                (tile_id, now, now, fields.get("state", "awaiting_tap")),
            )
            fields["updated_ts"] = now
            cols = ", ".join(f"{c} = ?" for c in fields)
            self._db.execute(f"UPDATE tiles SET {cols} WHERE tile_id = ?", (*fields.values(), tile_id))
            self._db.execute(
                "INSERT INTO events (ts, kind, tile_id, data_json) VALUES (?,?,?,?)",
                (now, "tile", tile_id, json.dumps(event_data, separators=(",", ":"), default=str)),
            )
            self._db.commit()

    # ---- history, sync, evidence -------------------------------------------

    def _append_locked(self, kind: str, now: float, tile_id: Optional[str], data: dict) -> int:
        cur = self._db.execute(
            "INSERT INTO events (ts, kind, tile_id, data_json) VALUES (?,?,?,?)",
            (now, kind, tile_id, json.dumps(data, separators=(",", ":"), default=str)),
        )
        return int(cur.lastrowid)

    def add_event(self, kind: str, now: float, tile_id: Optional[str] = None, data: Optional[dict] = None) -> int:
        """Append a non-tile event (alarm, health change, note). Returns its seq."""
        with self._lock:
            seq = self._append_locked(kind, now, tile_id, data or {})
            self._db.commit()
            return seq

    @staticmethod
    def _event(r: sqlite3.Row) -> dict:
        return {"seq": r["seq"], "ts": r["ts"], "kind": r["kind"], "tile_id": r["tile_id"],
                "data": json.loads(r["data_json"]) if r["data_json"] else {}}

    def events_after(self, seq: int = 0, limit: int = 500) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT seq, ts, kind, tile_id, data_json FROM events WHERE seq > ? ORDER BY seq LIMIT ?",
                (int(seq), int(limit)),
            ).fetchall()
        return [self._event(r) for r in rows]

    def events_for(self, tile_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT seq, ts, kind, tile_id, data_json FROM events WHERE tile_id = ? ORDER BY seq", (tile_id,)
            ).fetchall()
        return [self._event(r) for r in rows]

    def head_seq(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM events").fetchone()["m"])

    def rows_by_ids(self, tile_ids: list[str]) -> list[dict]:
        out: list[dict] = []
        with self._lock:
            for i in range(0, len(tile_ids), 500):
                chunk = list(tile_ids[i:i + 500])
                marks = ",".join("?" * len(chunk))
                out += [_row(r) for r in self._db.execute(
                    f"SELECT * FROM tiles WHERE tile_id IN ({marks})", chunk).fetchall()]
        return out

    def add_blob(self, tile_id: str, name: str, rel_path: str, sha256: str, size: int, now: float,
                 mime: Optional[str] = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO blobs (tile_id, name, rel_path, sha256, size, mime, created_ts, pruned) "
                "VALUES (?,?,?,?,?,?,?,0) ON CONFLICT(tile_id, name) DO UPDATE SET rel_path=excluded.rel_path, "
                "sha256=excluded.sha256, size=excluded.size, mime=excluded.mime, created_ts=excluded.created_ts, "
                "pruned=0",
                (tile_id, name, rel_path, sha256, int(size), mime, now),
            )
            self._append_locked("blob", now, tile_id, {"name": name, "sha256": sha256, "size": int(size)})
            self._db.commit()

    def blobs_for(self, tile_id: str) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(
                "SELECT * FROM blobs WHERE tile_id = ? ORDER BY name", (tile_id,)).fetchall()]

    def get_blob(self, tile_id: str, name: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM blobs WHERE tile_id = ? AND name = ?", (tile_id, name)).fetchone()
        return dict(row) if row else None

    def live_blobs_oldest_first(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(
                "SELECT * FROM blobs WHERE pruned = 0 ORDER BY created_ts, tile_id, name").fetchall()]

    def mark_blob_pruned(self, tile_id: str, name: str, now: float) -> None:
        with self._lock:
            self._db.execute("UPDATE blobs SET pruned = 1 WHERE tile_id = ? AND name = ?", (tile_id, name))
            self._append_locked("blob_pruned", now, tile_id, {"name": name})
            self._db.commit()

    def manifest(self) -> dict:
        """What this store holds, for a master to check its copy against."""
        with self._lock:
            tiles = self._db.execute("SELECT COUNT(*) AS n FROM tiles").fetchone()["n"]
            live = self._db.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS b FROM blobs WHERE pruned = 0").fetchone()
            gone = self._db.execute("SELECT COUNT(*) AS n FROM blobs WHERE pruned = 1").fetchone()["n"]
            head = self._db.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM events").fetchone()["m"]
            sid = self._db.execute("SELECT value FROM meta WHERE key = 'store_id'").fetchone()["value"]
        return {"store_id": sid, "head_seq": int(head), "tiles": tiles, "blobs_live": live["n"],
                "blob_bytes": live["b"], "blobs_pruned": gone}

    def get(self, tile_id: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute("SELECT * FROM tiles WHERE tile_id = ?", (tile_id,)).fetchone()
        return _row(row) if row else None

    def recent(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM tiles ORDER BY created_ts DESC, rowid DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [_row(r) for r in rows]

    def tally(self) -> dict:
        """Final-grade counts over everything stored (survives restarts)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT final_grade, COUNT(*) AS n FROM tiles WHERE final_grade IS NOT NULL GROUP BY final_grade"
            ).fetchall()
        return {r["final_grade"]: r["n"] for r in rows}

    def placement_tally(self) -> dict:
        """placed / failed / overdue counts over everything stored."""
        with self._lock:
            rows = self._db.execute(
                "SELECT placement, COUNT(*) AS n FROM tiles WHERE placement IS NOT NULL GROUP BY placement"
            ).fetchall()
        return {r["placement"]: r["n"] for r in rows}

    def recent_transits(self, limit: int = 200) -> list[float]:
        """Camera -> tap times of the most recent tiles that had one (survives
        a restart, so the suggestion does not start from nothing)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT transit_s FROM tiles WHERE transit_s IS NOT NULL ORDER BY created_ts DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [r["transit_s"] for r in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()


def _row(row: sqlite3.Row) -> dict:
    out = dict(row)
    for name in _JSON_FIELDS:
        raw = out.get(name)
        out[name.removesuffix("_json")] = json.loads(raw) if raw else None
        del out[name]
    out["acoustic_counted"] = bool(out["acoustic_counted"])
    if out.get("tile_in_cup") is not None:
        out["tile_in_cup"] = bool(out["tile_in_cup"])
    return out
