"""development/regression_tools.py: diff two runs of per-tile rows."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "development"))

from regression_tools import compare, direction, format_report, worse_count  # noqa: E402


def row(video, seq, grade="Grade A", crack="none", corner=False, fill=0.95):
    return {"video": video, "seq": seq, "grade": grade, "crack_severity": crack,
            "corner_broken": corner, "corner_fill_ratio": fill}


def test_identical_runs_report_identical():
    a = [row("v.mp4", 1), row("v.mp4", 2, "Reject", corner=True)]
    r = compare(a, [dict(x) for x in a])
    assert r["identical"] and not r["changed"] and not r["added"] and not r["removed"]
    assert "RESULT: IDENTICAL" in format_report(r)


def test_a_grade_change_names_the_check_that_moved_it():
    base = [row("v.mp4", 1), row("v.mp4", 2)]
    cur = [row("v.mp4", 1), row("v.mp4", 2, "Reject", corner=True, fill=0.82)]
    r = compare(base, cur)
    assert not r["identical"]
    (t,) = r["changed"]
    assert t["seq"] == 2 and t["direction"] == "worse"
    assert t["what"]["corner_broken"] == (False, True) and t["what"]["grade"] == ("Grade A", "Reject")
    assert t["corner_fill_ratio"] == (0.95, 0.82)
    assert worse_count(r) == 1


def test_improvement_is_labelled_better():
    r = compare([row("v.mp4", 1, "Reject", crack="major")], [row("v.mp4", 1, "Grade A")])
    assert r["changed"][0]["direction"] == "better" and worse_count(r) == 0


def test_a_count_change_is_reported_with_added_and_removed_tiles():
    base = [row("v.mp4", 1), row("v.mp4", 2)]
    cur = [row("v.mp4", 1), row("v.mp4", 2), row("v.mp4", 3, "Grade B")]
    r = compare(base, cur)
    assert r["videos"]["v.mp4"] == {"baseline": 2, "current": 3}
    assert [t["seq"] for t in r["added"]] == [3] and not r["removed"]
    assert "COUNT CHANGED" in format_report(r)
    r2 = compare(cur, base)
    assert [t["seq"] for t in r2["removed"]] == [3]


def test_tiles_in_different_videos_are_not_confused():
    base = [row("a.mp4", 1), row("b.mp4", 1, "Reject")]
    cur = [row("a.mp4", 1, "Reject"), row("b.mp4", 1, "Reject")]
    r = compare(base, cur)
    assert [(t["video"], t["seq"]) for t in r["changed"]] == [("a.mp4", 1)]


def test_a_fill_ratio_drift_alone_is_not_a_change():
    r = compare([row("v.mp4", 1, fill=0.95)], [row("v.mp4", 1, fill=0.93)])
    assert r["identical"]


def test_direction_handles_unknown_grades():
    assert direction("Grade A", "Grade A") == "same"
    assert direction("Grade A", "Hold") == "other"
    assert direction("Grade B", "Grade A") == "better"


def test_grade_totals_are_counted():
    r = compare([row("v.mp4", 1), row("v.mp4", 2)], [row("v.mp4", 1), row("v.mp4", 2, "Reject")])
    assert r["grade_counts"]["baseline"] == {"Grade A": 2}
    assert r["grade_counts"]["current"] == {"Grade A": 1, "Reject": 1}
