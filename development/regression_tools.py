"""Pure logic for the regression check: diff two lists of per-tile rows.

A row is a dict with at least `video`, `seq`, `grade`; the check also compares
`crack_severity`, `corner_broken` and (reported, not a change by itself)
`corner_fill_ratio`, so a diff says WHICH check moved a tile, not only that its
grade did. Tiles are matched on (video, seq). If a change makes the pipeline count
a different number of tiles in a video, every later seq may shift, so a count change
is reported on its own line and per-tile differences in that video should be read
with that in mind.

No I/O, no OpenCV: tested in tests/test_dev_regression_tools.py.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable

COMPARED = ("grade", "crack_severity", "corner_broken")
GRADE_ORDER = {"Grade A": 0, "Grade B": 1, "Grade C": 2, "Reject": 3}


def _key(row: dict) -> tuple:
    return (row["video"], int(row["seq"]))


def _index(rows: Iterable[dict]) -> dict:
    out: dict = {}
    for r in rows:
        out[_key(r)] = r
    return out


def direction(before, after) -> str:
    """'worse' / 'better' / 'same' on the A < B < C < Reject scale; 'other' if unknown."""
    if before == after:
        return "same"
    if before not in GRADE_ORDER or after not in GRADE_ORDER:
        return "other"
    return "worse" if GRADE_ORDER[after] > GRADE_ORDER[before] else "better"


def compare(baseline: list, current: list) -> dict:
    """What moved between a saved baseline and a fresh run.

    Returns {"videos": {video: {"baseline": n, "current": n}},
             "changed": [per-tile dicts], "added": [...], "removed": [...],
             "grade_counts": {"baseline": {...}, "current": {...}},
             "identical": bool}"""
    b, c = _index(baseline), _index(current)

    videos: dict = {}
    for r in baseline:
        videos.setdefault(r["video"], {"baseline": 0, "current": 0})["baseline"] += 1
    for r in current:
        videos.setdefault(r["video"], {"baseline": 0, "current": 0})["current"] += 1

    changed = []
    for k in sorted(set(b) & set(c)):
        old, new = b[k], c[k]
        diffs = {f: (old.get(f), new.get(f)) for f in COMPARED if old.get(f) != new.get(f)}
        if diffs:
            changed.append({
                "video": k[0], "seq": k[1],
                "grade": (old.get("grade"), new.get("grade")),
                "direction": direction(old.get("grade"), new.get("grade")),
                "what": diffs,
                "corner_fill_ratio": (old.get("corner_fill_ratio"), new.get("corner_fill_ratio")),
            })
    added = [{"video": k[0], "seq": k[1], "grade": c[k].get("grade")} for k in sorted(set(c) - set(b))]
    removed = [{"video": k[0], "seq": k[1], "grade": b[k].get("grade")} for k in sorted(set(b) - set(c))]

    counts = {
        "baseline": dict(Counter(r.get("grade") for r in baseline)),
        "current": dict(Counter(r.get("grade") for r in current)),
    }
    return {
        "videos": videos,
        "changed": changed,
        "added": added,
        "removed": removed,
        "grade_counts": counts,
        "identical": not (changed or added or removed)
        and all(v["baseline"] == v["current"] for v in videos.values()),
    }


def format_report(report: dict) -> str:
    lines = ["tiles per video (baseline -> current):"]
    for video, n in sorted(report["videos"].items()):
        flag = "" if n["baseline"] == n["current"] else "   <-- COUNT CHANGED, later seq numbers may shift"
        lines.append(f"  {video}: {n['baseline']} -> {n['current']}{flag}")
    gc = report["grade_counts"]
    grades = sorted(set(gc["baseline"]) | set(gc["current"]), key=lambda g: GRADE_ORDER.get(g, 99))
    lines.append("grade totals (baseline -> current):")
    for g in grades:
        lines.append(f"  {g}: {gc['baseline'].get(g, 0)} -> {gc['current'].get(g, 0)}")
    if report["changed"]:
        lines.append(f"{len(report['changed'])} tile(s) changed:")
        for t in report["changed"]:
            what = ", ".join(f"{f} {a} -> {b}" for f, (a, b) in t["what"].items())
            fill = t["corner_fill_ratio"]
            fill_txt = f"  [fill {fill[0]} -> {fill[1]}]" if fill[0] != fill[1] else ""
            lines.append(f"  {t['video']} #{t['seq']}: {t['direction'].upper()}  {what}{fill_txt}")
    else:
        lines.append("no tile changed its grade or its crack/corner result.")
    for label in ("added", "removed"):
        if report[label]:
            lines.append(f"{len(report[label])} tile(s) {label}:")
            for t in report[label]:
                lines.append(f"  {t['video']} #{t['seq']} ({t['grade']})")
    lines.append("RESULT: " + ("IDENTICAL" if report["identical"] else "DIFFERENT"))
    return "\n".join(lines)


def worse_count(report: dict) -> int:
    return sum(1 for t in report["changed"] if t["direction"] == "worse")
