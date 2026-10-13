"""Rule-based anomaly detection over the master's own tile record.

Compares the most recent slice of a window against the slice before it and
reports what moved *by more than chance*, with the numbers. It is deterministic
and needs no model: the LLM layer (`llm.py`) only ever explains what is found
here, it never decides what is an anomaly.

What it can and cannot say: it detects a change in how the line has been
behaving. It cannot say *why* - a rise in rejects may be a bad batch of clay,
a dirty lens or a changed light; the evidence is the numbers, the cause is for a
person. Everything is relative to this master's own history, so a line that has
been bad all along reads as normal.

Pure: takes plain rows (`MasterCollector.analysis_rows`) and alarms.
"""

from __future__ import annotations

from collections import Counter
from math import sqrt
from typing import Optional

# Fraction of the window that is "recent"; the rest is the baseline it is compared to.
RECENT_FRACTION = 0.25
MIN_BASELINE = 20      # graded tiles needed in the baseline before a rate comparison means anything
MIN_RECENT = 10
Z_THRESHOLD = 2.5      # ~1 in 80 by chance, one-sided
MIN_RATE_SHIFT = 0.10  # ...and at least this big in absolute terms; tiny real shifts are not worth a page
MIN_AGREE_SHIFT = 0.15
TRANSIT_RATIO = 1.5
TRANSIT_MIN_ABS_S = 1.0
MIN_TRANSITS = 8
THROUGHPUT_RATIO = 0.5
ALARM_BURST = 5


def two_proportion_z(x1: int, n1: int, x2: int, n2: int) -> Optional[float]:
    """z for (recent rate - baseline rate); None when it cannot be computed."""
    if n1 <= 0 or n2 <= 0:
        return None
    p = (x1 + x2) / (n1 + n2)
    se = sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return None
    return (x1 / n1 - x2 / n2) / se


def _median(vals: list[float]) -> Optional[float]:
    if not vals:
        return None
    v = sorted(vals)
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2


def _pct(x: float) -> str:
    return f"{x * 100:.0f} %"


def _transits(rows) -> list[float]:
    return [r["final_ts"] - r["camera_ts"] for r in rows
            if r.get("final_ts") is not None and r.get("camera_ts") is not None and r["final_ts"] >= r["camera_ts"]]


def _rate_anomaly(kind, title, label, recent_rows, base_rows, pred, *, rise_only=True, severity="warn"):
    """A shift in the share of graded tiles for which `pred(row)` holds."""
    n1, n2 = len(recent_rows), len(base_rows)
    if n1 < MIN_RECENT or n2 < MIN_BASELINE:
        return None
    x1, x2 = sum(1 for r in recent_rows if pred(r)), sum(1 for r in base_rows if pred(r))
    z = two_proportion_z(x1, n1, x2, n2)
    if z is None:
        return None
    r1, r2 = x1 / n1, x2 / n2
    up = z >= Z_THRESHOLD and r1 - r2 >= MIN_RATE_SHIFT
    down = (not rise_only) and z <= -Z_THRESHOLD and r2 - r1 >= MIN_RATE_SHIFT
    if not (up or down):
        return None
    return {
        "id": kind, "kind": kind, "severity": severity if up else "info", "title": title,
        "summary": f"{label} is {_pct(r1)} over the last stretch ({x1} of {n1} tiles), against {_pct(r2)} "
                   f"before it ({x2} of {n2}).",
        "evidence": {"recent_rate": round(r1, 4), "baseline_rate": round(r2, 4), "recent_n": n1,
                     "baseline_n": n2, "z": round(z, 2), "direction": "up" if up else "down"},
    }


def detect(rows: list[dict], alarms: list[dict], now: float, window_s: float = 3600.0) -> dict:
    """Anomalies for `[now-window_s, now]`: the last RECENT_FRACTION against the rest."""
    start = now - window_s
    split = now - window_s * RECENT_FRACTION
    rows = [r for r in rows if r.get("first_ts") is not None and start <= r["first_ts"] <= now]
    recent = [r for r in rows if r["first_ts"] >= split]
    base = [r for r in rows if r["first_ts"] < split]
    rg = [r for r in recent if r.get("final_grade")]
    bg = [r for r in base if r.get("final_grade")]

    found: list[dict] = []

    def add(a):
        if a:
            found.append(a)

    add(_rate_anomaly("reject_rate", "Reject rate has risen", "The reject rate", rg, bg,
                      lambda r: r["final_grade"] == "Reject", rise_only=False))
    add(_rate_anomaly("hold_rate", "More tiles are being held for a person", "The hold rate", rg, bg,
                      lambda r: r["final_grade"] == "Hold"))
    add(_rate_anomaly("timeout_rate", "More tiles are timing out without a tap", "The no-tap timeout rate", rg, bg,
                      lambda r: bool(r.get("timed_out"))))

    # camera vs sound agreement (a DROP is the anomaly)
    rb = [r for r in recent if r.get("camera_grade") and r.get("acoustic_grade")]
    bb = [r for r in base if r.get("camera_grade") and r.get("acoustic_grade")]
    if len(rb) >= MIN_RECENT and len(bb) >= MIN_BASELINE:
        a1 = sum(1 for r in rb if r["camera_grade"] == r["acoustic_grade"])
        a2 = sum(1 for r in bb if r["camera_grade"] == r["acoustic_grade"])
        z = two_proportion_z(a1, len(rb), a2, len(bb))
        r1, r2 = a1 / len(rb), a2 / len(bb)
        if z is not None and z <= -Z_THRESHOLD and r2 - r1 >= MIN_AGREE_SHIFT:
            found.append({
                "id": "agreement_drop", "kind": "agreement_drop", "severity": "warn",
                "title": "Camera and sound disagree more than before",
                "summary": f"They matched on {_pct(r1)} of tiles lately ({a1} of {len(rb)}), against "
                           f"{_pct(r2)} before ({a2} of {len(bb)}). One of the two sensors, or the tiles, changed.",
                "evidence": {"recent_rate": round(r1, 4), "baseline_rate": round(r2, 4), "recent_n": len(rb),
                             "baseline_n": len(bb), "z": round(z, 2)}})

    # camera -> final transit time (a RISE is the anomaly)
    tr, tb = _transits(recent), _transits(base)
    if len(tr) >= MIN_TRANSITS and len(tb) >= MIN_TRANSITS:
        m1, m2 = _median(tr), _median(tb)
        if m2 and m1 / m2 >= TRANSIT_RATIO and m1 - m2 >= TRANSIT_MIN_ABS_S:
            found.append({
                "id": "transit_drift", "kind": "transit_drift", "severity": "warn",
                "title": "Tiles take longer from camera to final grade",
                "summary": f"Median {m1:.1f} s lately against {m2:.1f} s before ({m1 / m2:.1f}x). The belt may have "
                           f"slowed, or taps are arriving late.",
                "evidence": {"recent_median_s": round(m1, 2), "baseline_median_s": round(m2, 2),
                             "recent_n": len(tr), "baseline_n": len(tb)}})

    # throughput (tiles first seen per minute). May simply be the belt stopped on purpose.
    r_min, b_min = window_s * RECENT_FRACTION / 60.0, window_s * (1 - RECENT_FRACTION) / 60.0
    if len(base) >= MIN_BASELINE and r_min > 0:
        t1, t2 = len(recent) / r_min, len(base) / b_min
        if t2 > 0 and t1 <= t2 * THROUGHPUT_RATIO:
            found.append({
                "id": "throughput_drop", "kind": "throughput_drop", "severity": "info",
                "title": "Fewer tiles are coming through",
                "summary": f"{t1:.1f} tiles/min lately against {t2:.1f} before. Fine if the belt was stopped on "
                           f"purpose; otherwise the camera may be missing tiles or the feed has stalled.",
                "evidence": {"recent_per_min": round(t1, 2), "baseline_per_min": round(t2, 2),
                             "recent_n": len(recent), "baseline_n": len(base)}})

    # alarm bursts: one kind repeating
    recent_al = Counter(a.get("kind") or "unknown" for a in alarms
                        if a.get("level", "alarm") == "alarm" and split <= (a.get("ts") or 0) <= now)
    for kind, n in sorted(recent_al.items()):
        if n >= ALARM_BURST:
            found.append({
                "id": f"alarm_burst:{kind}", "kind": "alarm_burst", "severity": "warn",
                "title": f"Alarm '{kind}' keeps repeating",
                "summary": f"{n} '{kind}' alarms in the last {window_s * RECENT_FRACTION / 60:.0f} min.",
                "evidence": {"alarm_kind": kind, "count": n}})

    found.sort(key=lambda a: (0 if a["severity"] == "warn" else 1, a["id"]))
    return {
        "now": now, "window_s": window_s,
        "recent_s": window_s * RECENT_FRACTION,
        "recent_tiles": len(recent), "baseline_tiles": len(base),
        "enough_data": len(bg) >= MIN_BASELINE and len(rg) >= MIN_RECENT,
        "anomalies": found,
    }
