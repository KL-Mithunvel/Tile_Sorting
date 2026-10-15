"""The master's copy of the Pi's own record (`fusion/store.py`).

The live MQTT collector (`collector.py`) only sees what was published while the
master was running. This is the other half: a faithful copy of what the Pi's
database holds - every tile row, every event, and the evidence files - pulled
through `sync.py`. They are kept apart on purpose: this one is the Pi's truth
as of the last sync, never a merge with what the master heard.

Everything is idempotent (upsert by tile id / event seq), so a sync that is
interrupted and re-run, or run twice, ends in the same state. Standard library
only; takes no clock because nothing here is time-dependent.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Iterable, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pi_tiles (
    tile_id    TEXT PRIMARY KEY,
    created_ts REAL NOT NULL,
    updated_ts REAL NOT NULL,
    row_json   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS pi_tiles_created ON pi_tiles (created_ts);
CREATE TABLE IF NOT EXISTS pi_events (
    seq       INTEGER PRIMARY KEY,
    ts        REAL NOT NULL,
    kind      TEXT NOT NULL,
    tile_id   TEXT,
    data_json TEXT
);
CREATE INDEX IF NOT EXISTS pi_events_tile ON pi_events (tile_id);
CREATE INDEX IF NOT EXISTS pi_events_kind ON pi_events (kind, ts);
CREATE TABLE IF NOT EXISTS pi_blobs (
    tile_id TEXT NOT NULL,
    name    TEXT NOT NULL,
    sha256  TEXT NOT NULL,
    size    INTEGER NOT NULL,
    mime    TEXT,
    status  TEXT NOT NULL,          -- missing | ok | pruned | corrupt
    path    TEXT,
    PRIMARY KEY (tile_id, name)
);
CREATE TABLE IF NOT EXISTS sync_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class PiMirror:
    def __init__(self, path: str | Path = ":memory:", blob_dir: Optional[str | Path] = None):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.blob_dir = Path(blob_dir).resolve() if blob_dir else None
        if self.blob_dir:
            self.blob_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()

    # ---- cursor / identity ---------------------------------------------------

    def _get(self, key: str) -> Optional[str]:
        row = self._db.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def _set(self, key: str, value) -> None:
        self._db.execute(
            "INSERT INTO sync_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))

    def cursor(self) -> int:
        with self._lock:
            return int(self._get("cursor") or 0)

    def store_id(self) -> Optional[str]:
        with self._lock:
            return self._get("store_id")

    def set_cursor(self, seq: int) -> None:
        with self._lock:
            self._set("cursor", int(seq))
            self._db.commit()

    def adopt_store(self, store_id: str) -> bool:
        """Remember which Pi database this copy follows. Returns True if it was
        a DIFFERENT one than before (the Pi's record was replaced): the old
        event cursor and events are dropped because their numbers mean nothing
        against the new database. Tile rows and files are kept - their ids are
        globally unique, and what they say is still what happened."""
        with self._lock:
            old = self._get("store_id")
            changed = old is not None and old != store_id
            if changed:
                self._db.execute("DELETE FROM pi_events")
                self._set("cursor", 0)
            self._set("store_id", store_id)
            self._db.commit()
            return changed

    # ---- applying what was pulled -------------------------------------------

    def apply_events(self, events: Iterable[dict]) -> int:
        n = 0
        with self._lock:
            for e in events:
                cur = self._db.execute(
                    "INSERT OR IGNORE INTO pi_events (seq, ts, kind, tile_id, data_json) VALUES (?,?,?,?,?)",
                    (int(e["seq"]), float(e["ts"]), e["kind"], e.get("tile_id"),
                     json.dumps(e.get("data") or {}, separators=(",", ":"))),
                )
                n += cur.rowcount
            self._db.commit()
        return n

    def apply_tiles(self, rows: Iterable[dict]) -> int:
        n = 0
        with self._lock:
            for r in rows:
                blobs = r.pop("blobs", None) or []
                self._db.execute(
                    "INSERT INTO pi_tiles (tile_id, created_ts, updated_ts, row_json) VALUES (?,?,?,?) "
                    "ON CONFLICT(tile_id) DO UPDATE SET created_ts = excluded.created_ts, "
                    "updated_ts = excluded.updated_ts, row_json = excluded.row_json",
                    (r["tile_id"], float(r["created_ts"]), float(r["updated_ts"]),
                     json.dumps(r, separators=(",", ":"), default=str)),
                )
                for b in blobs:
                    have = self._db.execute(
                        "SELECT sha256, status FROM pi_blobs WHERE tile_id = ? AND name = ?",
                        (r["tile_id"], b["name"])).fetchone()
                    if b.get("pruned"):
                        status = "ok" if (have and have["status"] == "ok" and have["sha256"] == b["sha256"]) \
                            else "pruned"
                    elif have and have["sha256"] == b["sha256"] and have["status"] == "ok":
                        status = "ok"                         # already fetched, unchanged
                    else:
                        status = "missing"
                    self._db.execute(
                        "INSERT INTO pi_blobs (tile_id, name, sha256, size, mime, status, path) "
                        "VALUES (?,?,?,?,?,?,NULL) ON CONFLICT(tile_id, name) DO UPDATE SET "
                        "sha256 = excluded.sha256, size = excluded.size, mime = excluded.mime, "
                        "status = CASE WHEN excluded.status = 'ok' THEN pi_blobs.status ELSE excluded.status END",
                        (r["tile_id"], b["name"], b["sha256"], int(b["size"]), b.get("mime"), status),
                    )
                n += 1
            self._db.commit()
        return n

    # ---- evidence files -----------------------------------------------------

    def missing_blobs(self, limit: int = 200) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(
                "SELECT tile_id, name, sha256, size FROM pi_blobs WHERE status = 'missing' LIMIT ?",
                (int(limit),)).fetchall()]

    def blob_destination(self, tile_id: str, name: str, suffix: str) -> Optional[Path]:
        if self.blob_dir is None:
            return None
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in f"{tile_id}__{name}{suffix}")
        return self.blob_dir / safe

    def set_blob(self, tile_id: str, name: str, status: str, path: Optional[str] = None) -> None:
        with self._lock:
            self._db.execute("UPDATE pi_blobs SET status = ?, path = ? WHERE tile_id = ? AND name = ?",
                             (status, path, tile_id, name))
            self._db.commit()

    def blob_path(self, tile_id: str, name: str) -> Optional[Path]:
        with self._lock:
            row = self._db.execute(
                "SELECT status, path FROM pi_blobs WHERE tile_id = ? AND name = ?", (tile_id, name)).fetchone()
        if not row or row["status"] != "ok" or not row["path"] or self.blob_dir is None:
            return None
        try:
            p = Path(row["path"]).resolve()
            p.relative_to(self.blob_dir)
        except (OSError, ValueError):
            return None
        return p if p.is_file() else None

    # ---- reading ------------------------------------------------------------

    @staticmethod
    def _tile(r: sqlite3.Row) -> dict:
        return json.loads(r["row_json"])

    def tiles(self, since_ts: Optional[float] = None, limit: int = 100000, newest_first: bool = False) -> list[dict]:
        q, args = "SELECT row_json FROM pi_tiles", ()
        if since_ts is not None:
            q, args = q + " WHERE created_ts >= ?", (since_ts,)
        q += " ORDER BY created_ts DESC, tile_id DESC" if newest_first else " ORDER BY created_ts, tile_id"
        with self._lock:
            rows = self._db.execute(q + " LIMIT ?", (*args, int(limit))).fetchall()
        return [self._tile(r) for r in rows]

    def tile(self, tile_id: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute("SELECT row_json FROM pi_tiles WHERE tile_id = ?", (tile_id,)).fetchone()
            if row is None:
                return None
            blobs = [dict(r) for r in self._db.execute(
                "SELECT name, sha256, size, mime, status FROM pi_blobs WHERE tile_id = ? ORDER BY name",
                (tile_id,)).fetchall()]
        out = self._tile(row)
        out["blobs"] = blobs
        return out

    def events(self, kinds: Optional[tuple] = None, since_ts: Optional[float] = None,
               tile_id: Optional[str] = None, limit: int = 5000) -> list[dict]:
        q, args, where = "SELECT seq, ts, kind, tile_id, data_json FROM pi_events", [], []
        if kinds:
            where.append("kind IN (%s)" % ",".join("?" * len(kinds)))
            args += list(kinds)
        if since_ts is not None:
            where.append("ts >= ?")
            args.append(since_ts)
        if tile_id is not None:
            where.append("tile_id = ?")
            args.append(tile_id)
        if where:
            q += " WHERE " + " AND ".join(where)
        with self._lock:
            rows = self._db.execute(q + " ORDER BY seq LIMIT ?", (*args, int(limit))).fetchall()
        return [{"seq": r["seq"], "ts": r["ts"], "kind": r["kind"], "tile_id": r["tile_id"],
                 "data": json.loads(r["data_json"]) if r["data_json"] else {}} for r in rows]

    def counts(self) -> dict:
        with self._lock:
            tiles = self._db.execute("SELECT COUNT(*) AS n FROM pi_tiles").fetchone()["n"]
            events = self._db.execute("SELECT COUNT(*) AS n FROM pi_events").fetchone()["n"]
            by = {r["status"]: r["n"] for r in self._db.execute(
                "SELECT status, COUNT(*) AS n FROM pi_blobs GROUP BY status").fetchall()}
        return {"tiles": tiles, "events": events, "blobs": by}

    def close(self) -> None:
        with self._lock:
            self._db.close()
