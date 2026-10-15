"""Deterministic checks of the Pi's own record - the part of the AI feature that
FINDS errors.

`insights.py` asks "has the line's behaviour drifted?" over the master's live
statistics. This module asks a different question of the Pi's database: "is
the record itself consistent, and did the systems around it misbehave?" Each
check is a plain rule over tile rows + the event history, returns the numbers
behind it, and is tested with synthetic records. The language model (`llm.py`)
only ever explains what is found here; it never decides what is an error.

What it can say: "7 tiles have no tap result after 2 min", "the final grade is
better than the camera's on 3 tiles", "tile numbers 41-43 never arrived".
What it cannot say is *why* - that is for a person (or the explainer, with the
numbers).

Pure: plain dicts in, plain dicts out.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Optional

GRADE_ORDER = ("Grade A", "Grade B", "Grade C", "Reject")        # same vocabulary as fusion/grades.py
_RUN_ID = re.compile(r"^T(?P<run>.+)-(?P<n>\d{5})$")

STUCK_DELIVERY_S = 60.0
WAITING_TAP_S = 120.0
MIN_TAPS_FOR_QUALITY = 10
UNUSABLE_TAP_SHARE = 0.5
CORRELATE_WINDOW_S = 300.0
CORRELATE_MIN_N = 5
CORRELATE_MIN_SHIFT = 0.30
ALARM_BURST = 5
EXAMPLES = 5


def _finding(kind: str, severity: str, title: str, summary: str, evidence: dict,
             tile_ids: Optional[list[str]] = None, fid: Optional[str] = None) -> dict:
    out = {"id": fid or kind, "kind": kind, "severity": severity, "title": title, "summary": summary,
           "evidence": evidence}
    if tile_ids:
        out["tile_ids"] = tile_ids[:EXAMPLES]
    return out


def _rank(grade) -> Optional[int]:
    return GRADE_ORDER.index(grade) if grade in GRADE_ORDER else None


def check_tiles(tiles: list[dict], now: float, blobs: Optional[dict] = None) -> list[dict]:
    found: list[dict] = []

    # 1. a final grade was decided but the UNO Q never took it
    stuck = [t for t in tiles
             if t.get("final_grade") and t.get("final_grade") != "Hold"
             and t.get("delivery") in ("pending", "sent") and now - (t.get("updated_ts") or now) > STUCK_DELIVERY_S]
    if stuck:
        found.append(_finding(
            "stuck_delivery", "warn", "Final grades were not acknowledged by the UNO Q",
            f"{len(stuck)} tile(s) have a final grade but the UNO Q has not acknowledged it for over "
            f"{STUCK_DELIVERY_S:.0f} s (delivery stuck at pending/sent).",
            {"count": len(stuck), "states": dict(Counter(t["delivery"] for t in stuck))},
            [t["tile_id"] for t in stuck]))

    # 2. camera result but the tap never came
    waiting = [t for t in tiles if t.get("state") == "awaiting_tap" and not t.get("final_grade")
               and now - (t.get("created_ts") or now) > WAITING_TAP_S]
    if waiting:
        found.append(_finding(
            "waiting_for_tap", "warn", "Tiles are still waiting for a tap result",
            f"{len(waiting)} tile(s) were graded by the camera more than {WAITING_TAP_S:.0f} s ago and have "
            f"no tap and no final grade. The tap station may be missing them, or the ledger is out of step.",
            {"count": len(waiting)}, [t["tile_id"] for t in waiting]))

    # 3. the final grade is better than its inputs allow ("worse of the two")
    bad = []
    for t in tiles:
        fr, cr = _rank(t.get("final_grade")), _rank(t.get("camera_grade"))
        if fr is None:
            continue
        if cr is not None and fr < cr:
            bad.append(t)
            continue
        ar = _rank(t.get("acoustic_grade"))
        if t.get("acoustic_counted") and ar is not None and fr < ar:
            bad.append(t)
    if bad:
        found.append(_finding(
            "grade_rule_violation", "alarm", "Final grade is better than the camera or sound result allows",
            f"{len(bad)} tile(s) were graded better than the worse of their inputs. The fusion rule says a "
            f"tile can only be downgraded by a second check, never rescued - this should not happen.",
            {"count": len(bad), "examples": [
                {"tile_id": t["tile_id"], "camera": t.get("camera_grade"), "acoustic": t.get("acoustic_grade"),
                 "final": t.get("final_grade")} for t in bad[:EXAMPLES]]},
            [t["tile_id"] for t in bad]))

    # 4. tile numbers that never arrived (the camera minted them, the station never saw them)
    runs: dict[str, set[int]] = defaultdict(set)
    for t in tiles:
        m = _RUN_ID.match(t["tile_id"])
        if m:
            runs[m["run"]].add(int(m["n"]))
    gaps = {}
    for run, nums in runs.items():
        missing = sorted(set(range(min(nums), max(nums) + 1)) - nums)
        if missing:
            gaps[run] = missing
    if gaps:
        total = sum(len(v) for v in gaps.values())
        found.append(_finding(
            "sequence_gap", "warn", "Tile numbers are missing from the record",
            f"{total} tile number(s) in {len(gaps)} run(s) were issued by the camera but never reached the "
            f"station. A message was lost, or the station was down when they passed.",
            {"missing_total": total, "runs": {r: m[:EXAMPLES] for r, m in list(gaps.items())[:EXAMPLES]}}))

    # 5. taps with no camera tile (a tile under the striker the camera never reported)
    unknown = [t for t in tiles if str(t["tile_id"]).startswith("UNKNOWN-")]
    if unknown:
        found.append(_finding(
            "unreported_tiles", "warn", "Tiles reached the tap station without a camera result",
            f"{len(unknown)} tile(s) were struck with no matching camera result, so the camera missed them "
            f"or reported them late.",
            {"count": len(unknown)}, [t["tile_id"] for t in unknown]))

    # 6. the tap recordings are mostly unusable
    tapped = [t for t in tiles if isinstance(t.get("acoustic"), dict) and "valid" in t["acoustic"]]
    tapped = tapped[-20:]
    if len(tapped) >= MIN_TAPS_FOR_QUALITY:
        bad_taps = [t for t in tapped if not t["acoustic"].get("valid")]
        share = len(bad_taps) / len(tapped)
        if share >= UNUSABLE_TAP_SHARE:
            reasons = Counter(str(t["acoustic"].get("reason_invalid") or "unspecified") for t in bad_taps)
            found.append(_finding(
                "unusable_taps", "warn", "Most recent tap recordings are unusable",
                f"{len(bad_taps)} of the last {len(tapped)} taps could not be analysed "
                f"({share * 100:.0f} %). The microphone, striker or noise floor may have changed.",
                {"bad": len(bad_taps), "of": len(tapped), "reasons": dict(reasons.most_common(3))},
                [t["tile_id"] for t in bad_taps]))

    # 7. the gantry did not (or has not) placed tiles
    notp = [t for t in tiles if t.get("placement") in ("failed", "overdue")]
    if notp:
        found.append(_finding(
            "placement_problem", "warn", "Tiles were not placed by the gantry",
            f"{len(notp)} tile(s) have a placement of failed/overdue.",
            {"count": len(notp), "by_state": dict(Counter(t["placement"] for t in notp)),
             "reasons": dict(Counter(str(t.get("placement_reason") or "unspecified") for t in notp).most_common(3))},
            [t["tile_id"] for t in notp]))

    # 8. tiles that need a person
    need = [t for t in tiles if t.get("delivery") == "needs_person"]
    if need:
        found.append(_finding(
            "needs_person", "info", "Tiles are waiting for a person",
            f"{len(need)} tile(s) were held and are waiting to be taken off the belt.",
            {"count": len(need)}, [t["tile_id"] for t in need]))

    # 9. evidence that should exist but is missing / corrupt on this copy
    if blobs is not None:
        lost = [t["tile_id"] for t in tiles
                if (t.get("camera") or {}).get("snapshot_path") and (blobs.get(t["tile_id"]) or {}).get("camera")
                in (None, "corrupt")]
        if lost:
            found.append(_finding(
                "missing_evidence", "info", "Some tile pictures are not in the record",
                f"{len(lost)} tile(s) were photographed by the camera but the picture is not held "
                f"(not kept on the Pi, or failed its checksum here).",
                {"count": len(lost)}, lost))
    return found


def correlate(tiles: list[dict], events: list[dict], now: float) -> list[dict]:
    """Does a system problem line up with worse outcomes for the tiles around it?

    For each station health change to `fail`, compare the share of tiles that
    ended Hold/timed-out in the window after against the window before."""
    found: list[dict] = []
    by_created = sorted((t for t in tiles if t.get("created_ts") is not None), key=lambda t: t["created_ts"])

    def bad(t: dict) -> bool:
        return t.get("final_grade") == "Hold" or t.get("delivery") in ("needs_person", "dropped")

    seen: set[str] = set()
    for e in events:
        if e["kind"] != "health" or (e.get("data") or {}).get("level") != "fail":
            continue
        name = str((e.get("data") or {}).get("name") or "?")
        if name in seen:
            continue
        ts = e["ts"]
        before = [t for t in by_created if ts - CORRELATE_WINDOW_S <= t["created_ts"] < ts]
        after = [t for t in by_created if ts <= t["created_ts"] < ts + CORRELATE_WINDOW_S]
        if len(before) < CORRELATE_MIN_N or len(after) < CORRELATE_MIN_N:
            continue
        rb = sum(map(bad, before)) / len(before)
        ra = sum(map(bad, after)) / len(after)
        if ra - rb >= CORRELATE_MIN_SHIFT:
            seen.add(name)
            found.append(_finding(
                "correlated_outage", "warn", f"Tiles went wrong after '{name}' failed",
                f"'{name}' went to FAIL at one point; in the {CORRELATE_WINDOW_S / 60:.0f} min after, "
                f"{ra * 100:.0f} % of tiles were held/dropped ({sum(map(bad, after))} of {len(after)}), "
                f"against {rb * 100:.0f} % before. This does not prove the cause, but it is where to look.",
                {"station": name, "at": ts, "rate_before": round(rb, 3), "rate_after": round(ra, 3),
                 "n_before": len(before), "n_after": len(after), "detail": (e.get("data") or {}).get("detail")},
                fid=f"correlated_outage:{name}"))

    # alarms repeating (same kind) in the recorded history
    recent_cut = now - 3600
    kinds = Counter(str((e.get("data") or {}).get("kind") or "unknown") for e in events
                    if e["kind"] == "alarm" and e["ts"] >= recent_cut)
    for kind, n in sorted(kinds.items()):
        if n >= ALARM_BURST:
            found.append(_finding(
                "alarm_repeat", "warn", f"Alarm '{kind}' keeps repeating",
                f"{n} '{kind}' alarms were recorded in the last hour.",
                {"alarm_kind": kind, "count": n}, fid=f"alarm_repeat:{kind}"))
    return found


def check_sync(report: Optional[dict], reconcile: Optional[dict] = None) -> list[dict]:
    """The copy itself: is the master actually reading the Pi?"""
    out: list[dict] = []
    if report is None:
        return [_finding("sync_never_ran", "info", "The Pi's record has not been pulled yet",
                         "No sync has completed since the master started.", {})]
    if not report.get("ok"):
        out.append(_finding("sync_failed", "warn", "The master cannot read the Pi's record",
                            "The last attempt to pull the Pi's record failed (see the error in the details).",
                            {"error": report.get("error")}))
    if report.get("store_replaced"):
        out.append(_finding("pi_store_replaced", "warn", "The Pi's database was replaced",
                            "The Pi is now serving a different database than before (it was reset or re-imaged). "
                            "History from before is kept here; event numbering restarted.", {}))
    if reconcile and reconcile.get("ok") and not reconcile.get("in_sync"):
        out.append(_finding("sync_incomplete", "info", "This copy is not complete yet",
                            "; ".join(reconcile.get("problems") or []), {"problems": reconcile.get("problems")}))
    if report.get("blobs_failed"):
        out.append(_finding("blob_transfer_failed", "info", "Some evidence files could not be fetched",
                            f"{report['blobs_failed']} file(s) failed or did not match their checksum.",
                            {"count": report["blobs_failed"]}))
    return out


def analyse(tiles: list[dict], events: list[dict], now: float, blobs: Optional[dict] = None,
            sync_report: Optional[dict] = None, reconcile: Optional[dict] = None) -> dict:
    findings = check_tiles(tiles, now, blobs) + correlate(tiles, events, now) + check_sync(sync_report, reconcile)
    order = {"alarm": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda f: (order.get(f["severity"], 3), f["id"]))
    return {"now": now, "tiles_checked": len(tiles), "events_checked": len(events), "findings": findings}
