"""Rule-based anomaly detection: flags real shifts, stays quiet on noise and on
too little data, and always reports the numbers it used."""

from master.insights import MIN_BASELINE, MIN_RECENT, detect, two_proportion_z

NOW = 10_000.0
W = 4000.0                    # window; recent = last 1000 s, baseline = the 3000 s before
BASE_T, RECENT_T = NOW - 3500.0, NOW - 500.0


def tile(i, t, final="Grade A", **kw):
    d = {"tile_id": f"T{i}", "first_ts": t, "camera_grade": "Grade A", "acoustic_grade": None,
         "final_grade": final, "timed_out": False, "camera_ts": None, "final_ts": None}
    d.update(kw)
    return d


def batch(n, t0, final_fn, **kw):
    return [tile(f"{t0}-{i}", t0 + i, final_fn(i), **kw) for i in range(n)]


def kinds(r):
    return [a["kind"] for a in r["anomalies"]]


def test_z_helper_edge_cases():
    assert two_proportion_z(0, 0, 1, 5) is None
    assert two_proportion_z(0, 10, 0, 10) is None            # no variance at all
    assert two_proportion_z(8, 10, 1, 10) > 2.5


def test_a_real_rise_in_rejects_is_flagged_with_its_numbers():
    rows = batch(60, BASE_T, lambda i: "Reject" if i % 15 == 0 else "Grade A")          # ~7 %
    rows += batch(20, RECENT_T, lambda i: "Reject" if i % 3 == 0 else "Grade A")         # ~35 %
    r = detect(rows, [], NOW, W)
    (a,) = [x for x in r["anomalies"] if x["kind"] == "reject_rate"]
    assert a["severity"] == "warn" and a["evidence"]["direction"] == "up"
    assert a["evidence"]["recent_n"] == 20 and a["evidence"]["baseline_n"] == 60
    assert a["evidence"]["recent_rate"] > 0.3 > 0.1 > a["evidence"]["baseline_rate"] - 0.01
    assert "%" in a["summary"] and r["enough_data"] is True


def test_a_steady_line_raises_nothing():
    rows = batch(60, BASE_T, lambda i: "Reject" if i % 10 == 0 else "Grade A")
    rows += batch(20, RECENT_T, lambda i: "Reject" if i % 10 == 0 else "Grade A")
    assert detect(rows, [], NOW, W)["anomalies"] == []


def test_a_small_wobble_is_not_a_page_even_if_it_is_a_few_tiles():
    rows = batch(60, BASE_T, lambda i: "Reject" if i % 10 == 0 else "Grade A")           # 10 %
    rows += batch(20, RECENT_T, lambda i: "Reject" if i % 5 == 0 else "Grade A")          # 20 %: +10 pts but weak z
    assert "reject_rate" not in kinds(detect(rows, [], NOW, W))


def test_too_little_data_never_flags():
    rows = batch(MIN_BASELINE - 1, BASE_T, lambda i: "Grade A") + batch(MIN_RECENT, RECENT_T, lambda i: "Reject")
    r = detect(rows, [], NOW, W)
    assert r["anomalies"] == [] and r["enough_data"] is False
    rows = batch(MIN_BASELINE, BASE_T, lambda i: "Grade A") + batch(MIN_RECENT - 1, RECENT_T, lambda i: "Reject")
    assert detect(rows, [], NOW, W)["anomalies"] == []


def test_a_drop_in_rejects_is_reported_as_info_not_a_warning():
    rows = batch(60, BASE_T, lambda i: "Reject" if i % 3 == 0 else "Grade A")
    rows += batch(20, RECENT_T, lambda i: "Grade A")
    (a,) = [x for x in detect(rows, [], NOW, W)["anomalies"] if x["kind"] == "reject_rate"]
    assert a["severity"] == "info" and a["evidence"]["direction"] == "down"


def test_timeouts_and_holds_rising():
    rows = batch(60, BASE_T, lambda i: "Grade A")
    rows += [tile(f"r{i}", RECENT_T + i, "Hold" if i % 2 else "Grade A", timed_out=bool(i % 2)) for i in range(20)]
    k = kinds(detect(rows, [], NOW, W))
    assert "hold_rate" in k and "timeout_rate" in k


def test_agreement_drop_uses_only_tiles_with_both_grades():
    base = [tile(f"b{i}", BASE_T + i, acoustic_grade="Grade A") for i in range(60)]
    rec = [tile(f"c{i}", RECENT_T + i, acoustic_grade="Grade A" if i % 3 == 0 else "Grade B") for i in range(20)]
    nosound = [tile(f"n{i}", RECENT_T + 50 + i) for i in range(30)]       # no sound grade: must not count as disagreement
    r = detect(base + rec + nosound, [], NOW, W)
    (a,) = [x for x in r["anomalies"] if x["kind"] == "agreement_drop"]
    assert a["evidence"]["recent_n"] == 20 and a["evidence"]["baseline_rate"] == 1.0


def test_transit_drift_needs_a_real_ratio_and_absolute_gap():
    base = [tile(f"b{i}", BASE_T + i, camera_ts=0.0, final_ts=2.0) for i in range(30)]
    slow = [tile(f"s{i}", RECENT_T + i, camera_ts=0.0, final_ts=5.0) for i in range(12)]
    assert "transit_drift" in kinds(detect(base + slow, [], NOW, W))
    small = [tile(f"m{i}", RECENT_T + i, camera_ts=0.0, final_ts=2.4) for i in range(12)]
    assert "transit_drift" not in kinds(detect(base + small, [], NOW, W))
    tiny = [tile(f"t{i}", BASE_T + i, camera_ts=0.0, final_ts=0.2) for i in range(30)]
    quick = [tile(f"q{i}", RECENT_T + i, camera_ts=0.0, final_ts=0.5) for i in range(12)]   # 2.5x but only 0.3 s
    assert "transit_drift" not in kinds(detect(tiny + quick, [], NOW, W))


def test_throughput_drop_is_info_only():
    rows = batch(90, BASE_T, lambda i: "Grade A") + batch(5, RECENT_T, lambda i: "Grade A")
    (a,) = [x for x in detect(rows, [], NOW, W)["anomalies"] if x["kind"] == "throughput_drop"]
    assert a["severity"] == "info"


def test_alarm_burst_counts_only_recent_alarms_and_sorts_warnings_first():
    alarms = [{"ts": NOW - 100 - i, "kind": "missed_camera", "level": "alarm"} for i in range(5)]
    alarms += [{"ts": NOW - 3000, "kind": "orphan_tap", "level": "alarm"} for _ in range(9)]      # old: ignored
    alarms += [{"ts": NOW - 50, "kind": "event", "level": "info"} for _ in range(9)]               # not an alarm
    rows = batch(90, BASE_T, lambda i: "Grade A") + batch(5, RECENT_T, lambda i: "Grade A")      # also trips throughput
    r = detect(rows, alarms, NOW, W)
    ids = [a["id"] for a in r["anomalies"]]
    assert "alarm_burst:missed_camera" in ids and not any("orphan_tap" in i or "event" in i for i in ids)
    assert [a["severity"] for a in r["anomalies"]] == ["warn", "info"]


def test_empty_input_is_quiet():
    r = detect([], [], NOW, W)
    assert r["anomalies"] == [] and r["enough_data"] is False
