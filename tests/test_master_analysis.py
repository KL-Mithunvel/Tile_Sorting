"""Production analysis for the master: pure numbers over per-tile rows, the
collector's raw-row queries, and the /api/analysis endpoint."""

import json

from master.analysis import BUCKETS, MIN_SAMPLE, WINDOWS_S, build_analysis
from master.app import create_app
from master.bus import InMemoryBus
from master.collector import MasterCollector

NOW = 10_000.0


def row(ts, cam="Grade A", sound=None, final=None, dest=None, cam_ts=None, final_ts=None, **kw):
    d = {"tile_id": f"T{ts}", "first_ts": ts, "camera_grade": cam, "acoustic_grade": sound,
         "final_grade": final, "destination": dest, "camera_ts": cam_ts, "final_ts": final_ts,
         "timed_out": False, "acoustic_counted": False, "ack_status": None}
    d.update(kw)
    return d


def test_counts_rates_and_throughput_over_the_window():
    rows = [row(NOW - 100 + i, final="Grade A", dest="bin_1") for i in range(8)]
    rows += [row(NOW - 50, final="Reject", dest="bin_reject"), row(NOW - 40, final="Hold", dest="bin_hold")]
    a = build_analysis(rows, [], NOW, window_s=600)
    t = a["totals"]
    assert t["tiles"] == 10 and t["graded"] == 10
    assert t["throughput_per_min"] == 1.0           # 10 tiles / 10 min
    assert t["reject_rate"] == 0.1 and t["hold_rate"] == 0.1
    assert a["grades"]["Grade A"] == 8 and a["destinations"] == {"bin_1": 8, "bin_reject": 1, "bin_hold": 1}


def test_small_samples_are_flagged_not_hidden():
    a = build_analysis([row(NOW - 5, final="Reject")], [], NOW)
    assert a["totals"]["reject_rate"] == 1.0 and a["totals"]["small_sample"] is True
    assert a["min_sample"] == MIN_SAMPLE


def test_empty_window_gives_none_not_zero_division():
    a = build_analysis([], [], NOW)
    t = a["totals"]
    assert t["tiles"] == 0 and t["reject_rate"] is None and t["timeout_rate"] is None
    assert a["agreement"]["rate"] is None and a["transit_s"]["median"] is None
    assert len(a["series"]) == BUCKETS


def test_tiles_outside_the_window_or_undated_are_ignored():
    rows = [row(NOW - 5000, final="Reject"), row(None, final="Reject"), row(NOW + 50, final="Reject"),
            row(NOW - 10, final="Grade A")]
    a = build_analysis(rows, [], NOW, window_s=300)
    assert a["totals"]["tiles"] == 1 and a["grades"]["Reject"] == 0


def test_series_buckets_every_tile_exactly_once_including_the_edges():
    w = 300
    rows = [row(NOW - w, final="Grade A"), row(NOW, final="Reject"), row(NOW - 1, cam="Grade B")]
    a = build_analysis(rows, [], NOW, window_s=w)
    s = a["series"]
    assert sum(b["total"] for b in s) == 3
    assert s[0]["total"] == 1 and s[0]["Grade A"] == 1
    assert s[-1]["total"] == 2 and s[-1]["Reject"] == 1      # the ungraded one counts as a tile, not a grade


def test_agreement_matrix_compares_camera_and_sound_only_where_both_exist():
    rows = [row(NOW - 10, cam="Grade A", sound="Grade A"), row(NOW - 9, cam="Grade A", sound="Grade B"),
            row(NOW - 8, cam="Reject", sound="Reject"), row(NOW - 7, cam="Grade A", sound=None)]
    ag = build_analysis(rows, [], NOW)["agreement"]
    assert ag["compared"] == 3 and ag["agree"] == 2 and ag["rate"] == round(2 / 3, 4)
    assert ag["matrix"] == {"Grade A": {"Grade A": 1, "Grade B": 1}, "Reject": {"Reject": 1}}
    assert ag["small_sample"] is True


def test_transit_uses_the_stations_own_timestamps_and_skips_nonsense():
    rows = [row(NOW - 10, cam_ts=100.0, final_ts=102.0, final="Grade A"),
            row(NOW - 9, cam_ts=100.0, final_ts=104.0, final="Grade A"),
            row(NOW - 8, cam_ts=100.0, final_ts=90.0, final="Grade A"),    # clock skew: dropped
            row(NOW - 7, cam_ts=None, final_ts=104.0, final="Grade A")]
    t = build_analysis(rows, [], NOW)["transit_s"]
    assert t["n"] == 2 and t["median"] == 3.0 and t["max"] == 4.0


def test_alarms_are_counted_by_kind_inside_the_window_only():
    alarms = [{"ts": NOW - 10, "kind": "camera_silent"}, {"ts": NOW - 20, "kind": "camera_silent"},
              {"ts": NOW - 30, "kind": None}, {"ts": NOW - 99999, "kind": "old"}]
    a = build_analysis([], alarms, NOW, window_s=300)
    assert a["alarms_by_kind"] == {"camera_silent": 2, "unknown": 1}


def test_collector_rows_carry_the_stations_timestamps_and_respect_since():
    c = MasterCollector()
    c.handle_message("tile/T1/camera/result", json.dumps({"grade": "Grade A", "ts": 50.0}).encode(), now=100.0)
    c.handle_message("tile/T1/final", json.dumps({"final_grade": "Grade A", "ts": 53.5}).encode(), now=101.0)
    c.handle_message("tile/T2/camera/result", json.dumps({"grade": "Reject", "ts": 150.0}).encode(), now=200.0)
    c.handle_message("machine/alarm", json.dumps({"kind": "x", "message": "m", "ts": 120.0}).encode(), now=120.0)
    rows = c.analysis_rows()
    assert [r["tile_id"] for r in rows] == ["T1", "T2"]
    assert rows[0]["camera_ts"] == 50.0 and rows[0]["final_ts"] == 53.5 and rows[1]["final_ts"] is None
    assert [r["tile_id"] for r in c.analysis_rows(since_ts=150.0)] == ["T2"]
    assert [a["kind"] for a in c.alarm_rows(since_ts=100.0)] == ["x"]
    assert c.alarm_rows(since_ts=500.0) == []


def test_analysis_endpoint_validates_window_and_serves_page():
    bus, col = InMemoryBus(), MasterCollector()
    col.attach(bus)
    app = create_app(col, bus, clock=lambda: NOW)
    c = app.test_client()
    ok = c.get("/api/analysis?window=900")
    assert ok.status_code == 200 and ok.get_json()["window_s"] == 900
    assert c.get("/api/analysis").get_json()["window_s"] == 3600           # default
    for bad in ("abc", "7", "-1", "999999999"):
        assert c.get("/api/analysis?window=" + bad).status_code == 400
    assert WINDOWS_S and c.get("/analysis").status_code == 200
