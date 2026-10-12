"""The plant model behind the 3D view: what each station is doing, and where
each tile is on the belt.

Pure: `build_plant()` takes the collector's snapshot (and a clock value) and
returns plain data, so it is tested with synthetic snapshots and the page only
draws what it is told. Nothing here talks to the bus.

Belt position is *derived*, not measured: no station reports where a tile is
physically, only which stages have produced a result. The page therefore shows
an honest approximation - a tile moves along the belt with its age but is held
back at the next station until that station has reported - and `u` is a 0..1
parameter along the line, not a distance.
"""

from __future__ import annotations

from typing import Optional

# A station that has not reported for this long is shown as offline.
SILENT_AFTER_S = 8.0

# Seconds a tile takes to ride the whole drawn line when nothing holds it back.
LINE_TRANSIT_S = 14.0
# Tiles older than this have been sorted and left the scene.
SORTED_VISIBLE_S = 20.0

# Where the stations sit along the belt (0 = entry, 1 = pick-up end).
STATION_U = {"camera": 0.25, "acoustic": 0.6, "gantry": 0.9}
# A tile may not pass a station before that station has reported on it.
STAGE_U_CAP = {"camera": 0.58, "acoustic": 0.88, "sorted": 1.0}

# station id -> (label, key in the Pi's health dict)
STATIONS = (
    ("pi", "Station service (Pi)", None),
    ("camera", "Camera station", "camera"),
    ("acoustic", "Acoustic station", "mic"),
    ("conveyor", "Conveyor (Nano)", "nano"),
    ("gantry", "Pick & place (UNO Q)", "unoq"),
)

LEVELS = ("ok", "warn", "fail", "offline")


def tile_stage(row: dict) -> str:
    """`camera` (graded, waiting for a tap), `acoustic` (tapped, no final yet) or `sorted`."""
    if row.get("final_grade"):
        return "sorted"
    if row.get("acoustic_grade") or row.get("acoustic_status"):
        return "acoustic"
    return "camera"


def tile_position(stage: str, age_s: float) -> float:
    """0..1 along the belt: ages forward with time, held at the next station."""
    free = max(0.0, age_s) / LINE_TRANSIT_S
    return round(min(free, STAGE_U_CAP.get(stage, 1.0)), 4)


def _silent(age: Optional[float]) -> bool:
    return age is None or age > SILENT_AFTER_S


def _gantry_direct(snap: dict) -> Optional[tuple]:
    """(level, detail) from the gantry's own retained status, when it is fresh.
    Preferred over the Pi's `health.gantry`: it is the machine speaking, not the
    Pi's opinion of it, and it is still there if the Pi is not."""
    g = snap.get("gantry")
    if not isinstance(g, dict) or _silent(snap.get("gantry_age_s")):
        return None
    if g.get("state") == "offline":
        return "offline", "gantry service went offline"
    level = g.get("level") if g.get("level") in LEVELS else "warn"
    if level == "fail":
        why = g.get("alarm") or g.get("fault") or ("MCU link down" if g.get("link") is False else "fault")
        return "fail", f"needs a person: {why}"
    if g.get("paused"):
        return "warn", "paused"
    if g.get("homed") is False:
        return "warn", "not homed"
    return level, f"{g.get('completed', 0)} placed, queue {g.get('queue_depth', 0)}"


def _stations(snap: dict) -> list[dict]:
    station = snap.get("station")
    silent = station is None or _silent(snap.get("station_age_s"))
    health = (station or {}).get("health") or {}
    out = []
    for sid, label, key in STATIONS:
        direct = _gantry_direct(snap) if sid == "gantry" else None
        if direct is not None:
            level, detail = direct
        elif silent:
            level, detail = "offline", "no status from the Pi station"
        elif key is None:
            state = station.get("state", "")
            level = "fail" if state == "fault" else "warn" if state in ("hold_all", "line_stopped") else "ok"
            detail = station.get("state_detail") or state
        else:
            h = health.get(key)
            level = h.get("level", "offline") if isinstance(h, dict) else "offline"
            detail = h.get("detail", "") if isinstance(h, dict) else "not reported"
        if level not in LEVELS:
            level = "warn"
        out.append({"id": sid, "label": label, "level": level, "detail": detail, "u": STATION_U.get(sid)})
    return out


def stations_from_snapshot(snap: dict) -> list[dict]:
    """Per-station level + detail, as the 3D view and the health history use it."""
    return _stations(snap)


def build_plant(snap: dict, now: float) -> dict:
    station = snap.get("station") or {}
    silent = not station or _silent(snap.get("station_age_s"))
    settings = station.get("settings") or {}
    nano = snap.get("nano") or {}
    belt = nano.get("belt_pct")

    tiles = []
    for row in snap.get("recent") or []:
        first = row.get("first_ts")
        if first is None:
            continue
        age = max(0.0, now - first)
        stage = tile_stage(row)
        if stage == "sorted" and age > SORTED_VISIBLE_S:
            continue
        tiles.append({
            "tile_id": row["tile_id"],
            "stage": stage,
            "age_s": round(age, 1),
            "u": tile_position(stage, age),
            "grade": row.get("final_grade") or row.get("camera_grade"),
            "destination": row.get("destination"),
            "placement": row.get("placement"),
        })

    return {
        "now": now,
        "line": {
            "state": "silent" if silent else station.get("state"),
            "detail": "the Pi station is not reporting" if silent else station.get("state_detail"),
            "belt_pct": belt,
            "hold_all": bool(settings.get("hold_all")),
            "stopped": settings.get("line") == "stopped",
            "vote_enabled": bool(settings.get("vote_enabled")),
            "awaiting": station.get("awaiting_count", 0),
        },
        "stations": _stations(snap),
        "tiles": tiles,
        "tally": snap.get("tally") or {},
        "alarms": [a for a in snap.get("alarms") or [] if a.get("level") == "alarm"][:10],
    }
