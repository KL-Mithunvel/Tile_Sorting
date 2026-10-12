"""Production analysis over the master's own record of tiles.

Pure: `build_analysis()` takes plain rows (see `MasterCollector.analysis_rows`)
and a clock value and returns plain data for the Analysis page, so every number
is testable with synthetic rows. Nothing here is a statement about *why* a
number moved - it only counts what the stations reported.

Definitions the page repeats, so they live in one place:
- throughput = tiles first seen per minute over the window;
- reject rate = Reject / graded tiles (those with a final grade);
- agreement = of tiles that have BOTH a camera grade and a sound grade, the
  share where they match. Sound is not counted in the final grade unless the
  station's vote is on, so this measures the sensors, not the decision;
- transit = camera result time to final-grade time, as stamped by the stations.
"""

from __future__ import annotations

from collections import Counter
from typing import Optional

GRADES = ("Grade A", "Grade B", "Grade C", "Reject", "Hold")
WINDOWS_S = (300, 900, 3600, 21600, 86400)
DEFAULT_WINDOW_S = 3600
BUCKETS = 30
# Below this many graded tiles a rate is shown but flagged as too few to trust.
MIN_SAMPLE = 20


def _rate(n: int, d: int) -> Optional[float]:
    return None if d == 0 else round(n / d, 4)


def _percentile(sorted_vals: list[float], q: float) -> Optional[float]:
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return round(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo), 2)


def build_analysis(rows: list[dict], alarms: list[dict], now: float,
                   window_s: int = DEFAULT_WINDOW_S, buckets: int = BUCKETS) -> dict:
    window_s = int(window_s)
    start = now - window_s
    step = window_s / buckets
    rows = [r for r in rows if r.get("first_ts") is not None and start <= r["first_ts"] <= now]

    # ---- over time -----------------------------------------------------
    series = [{"t": round(start + i * step, 1), "total": 0, **{g: 0 for g in GRADES}} for i in range(buckets)]
    for r in rows:
        i = min(buckets - 1, int((r["first_ts"] - start) / step))
        series[i]["total"] += 1
        g = r.get("final_grade")
        if g in GRADES:
            series[i][g] += 1

    # ---- headline numbers ----------------------------------------------
    graded = [r for r in rows if r.get("final_grade")]
    grades = Counter(r["final_grade"] for r in graded)
    n_graded = len(graded)
    minutes = window_s / 60.0

    both = [r for r in rows if r.get("camera_grade") and r.get("acoustic_grade")]
    agree = sum(1 for r in both if r["camera_grade"] == r["acoustic_grade"])
    matrix: dict[str, dict[str, int]] = {}
    for r in both:
        matrix.setdefault(r["camera_grade"], {}).setdefault(r["acoustic_grade"], 0)
        matrix[r["camera_grade"]][r["acoustic_grade"]] += 1

    transits = sorted(
        r["final_ts"] - r["camera_ts"] for r in rows
        if r.get("final_ts") is not None and r.get("camera_ts") is not None and r["final_ts"] >= r["camera_ts"]
    )
    timed_out = sum(1 for r in graded if r.get("timed_out"))
    counted = sum(1 for r in graded if r.get("acoustic_counted"))
    unacked = sum(1 for r in graded if r.get("final_grade") and r.get("ack_status") not in ("ok", None)) 

    return {
        "now": now,
        "window_s": window_s,
        "bucket_s": round(step, 1),
        "min_sample": MIN_SAMPLE,
        "totals": {
            "tiles": len(rows),
            "graded": n_graded,
            "throughput_per_min": round(len(rows) / minutes, 2) if minutes else None,
            "reject_rate": _rate(grades.get("Reject", 0), n_graded),
            "hold_rate": _rate(grades.get("Hold", 0), n_graded),
            "timeout_rate": _rate(timed_out, n_graded),
            "sound_counted_rate": _rate(counted, n_graded),
            "failed_delivery": unacked,
            "small_sample": n_graded < MIN_SAMPLE,
        },
        "grades": {g: grades.get(g, 0) for g in GRADES},
        "destinations": dict(Counter(r["destination"] for r in graded if r.get("destination"))),
        "agreement": {
            "compared": len(both),
            "agree": agree,
            "rate": _rate(agree, len(both)),
            "matrix": matrix,
            "small_sample": len(both) < MIN_SAMPLE,
        },
        "transit_s": {
            "n": len(transits),
            "median": _percentile(transits, 0.5),
            "p90": _percentile(transits, 0.9),
            "max": round(transits[-1], 2) if transits else None,
        },
        "series": series,
        "alarms_by_kind": dict(Counter(a.get("kind") or "unknown" for a in alarms
                                       if start <= (a.get("ts") or 0) <= now)),
    }
