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
            self._db.commit()

    def upsert(self, tile_id: str, now: float, **fields) -> None:
        """Create the row if new, then set the given columns. Dict values for
        the *_json columns are serialised here."""
        unknown = set(fields) - _COLUMNS
        if unknown:
            raise ValueError(f"unknown tile column(s): {sorted(unknown)}")
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
            self._db.commit()

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
