"""Ids, label rows, CSV and label merging for the tile dataset export."""
import csv
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "development"))

from dataset_tools import (  # noqa: E402
    CSV_COLUMNS,
    build_row,
    make_id,
    merge_human_labels,
    summarise,
    to_csv,
    unique_ids,
    video_stem,
)


def record(seq=1, grade="Grade A", crack="none", broken=False, **extra):
    base = {
        "seq": seq, "track_id": 9, "grade": grade, "rule_grade": grade, "crack_severity": crack,
        "crack_detected": crack != "none", "crack_length_px": 0.0 if crack == "none" else 120.0,
        "corner_broken": broken, "corner_fill_ratio": 0.96, "corner_missing_extent_fraction": 0.02,
        "corner_missing_area_px": 0.0, "sharpness": 41.5, "sharpness_candidates": 6, "model_grade": None,
        "model_confidence": None, "model_station_grade": None, "model_used": False,
        "crop_w": 380, "crop_h": 376, "center_x": 1100.0, "center_y": 563.0,
    }
    base.update(extra)
    return base


def row(seq=1, video="WIN_20261011_12_16_03_Pro.mp4", **kw):
    return build_row(record(seq, **kw), video=video, stem=video_stem(video), frame=228, fps=30.0,
                     bbox=(946, 388, 380, 375), tile_file="tiles/x.png", context_file="context/x.jpg")


class TestIds:
    @pytest.mark.parametrize("name,stem", [
        ("WIN_20261011_12_16_03_Pro.mp4", "WIN_20261011_12_16_03"),
        ("WhatsApp Video 2026-08-26 at 7.27.04 PM.mp4", "WhatsApp_Video_2026_08_26_at_7_27_04_PM"),
        ("clip.final (2).MOV", "clip_final_2"),
        ("...", "video"),
    ])
    def test_stems_are_filename_safe(self, name, stem):
        assert video_stem(name) == stem

    def test_ids_are_stable_and_sortable(self):
        assert make_id("WIN_20261011_12_16_03_Pro.mp4", 6) == "WIN_20261011_12_16_03_t006"
        ids = [make_id("a.mp4", n) for n in (2, 10, 1)]
        assert sorted(ids) == [make_id("a.mp4", 1), make_id("a.mp4", 2), make_id("a.mp4", 10)]

    def test_two_files_that_sanitise_alike_do_not_collide(self):
        m = unique_ids(["a b.mp4", "a_b.mp4", "other.mp4"])
        assert len(set(m.values())) == 3 and m["a b.mp4"] == "a_b" and m["a_b.mp4"] == "a_b_2"


class TestRows:
    def test_a_row_has_exactly_the_csv_columns_in_order(self):
        assert list(row()) == CSV_COLUMNS

    def test_where_the_tile_came_from_is_recorded(self):
        r = row(seq=6)
        assert r["id"] == "WIN_20261011_12_16_03_t006" and r["frame"] == 228 and r["time_s"] == 7.6
        assert (r["bbox_x"], r["bbox_y"], r["bbox_w"], r["bbox_h"]) == (946, 388, 380, 375)

    def test_human_labels_start_empty(self):
        r = row()
        assert r["human_label"] is None and r["human_note"] is None

    def test_missing_optional_fields_become_none_not_errors(self):
        rec = record()
        for k in ("rule_grade", "sharpness", "track_id", "model_used", "crop_w"):
            rec.pop(k)
        r = build_row(rec, video="v.mp4", stem="v", frame=None, fps=30.0, bbox=(0, 0, 1, 1), tile_file="a", context_file="b")
        assert r["sharpness"] is None and r["time_s"] is None and r["rule_grade"] is None


class TestCsv:
    def test_round_trips_through_a_csv_reader(self):
        rows = [row(1), row(2, grade="Reject", crack="major", broken=True)]
        got = list(csv.DictReader(io.StringIO(to_csv(rows))))
        assert [g["id"] for g in got] == [r["id"] for r in rows]
        assert got[1]["grade"] == "Reject" and got[1]["corner_broken"] == "True" and got[0]["human_label"] == ""

    def test_header_is_the_documented_columns(self):
        assert to_csv([]).splitlines()[0].split(",") == CSV_COLUMNS


class TestMergeLabels:
    def export(self, *items):
        return {"items": [dict(i) for i in items]}

    def test_labels_land_on_the_matching_tile(self):
        rows = [row(1), row(2)]
        applied, unmatched, invalid = merge_human_labels(rows, self.export(
            {"video": "WIN_20261011_12_16_03_Pro.mp4", "seq": 2, "label": "chipped", "note": "bottom right"}))
        assert (applied, unmatched, invalid) == (1, [], [])
        assert rows[1]["human_label"] == "chipped" and rows[1]["human_note"] == "bottom right" and rows[0]["human_label"] is None

    def test_blank_labels_leave_rows_alone_and_do_not_erase_existing_ones(self):
        rows = [row(1)]
        rows[0]["human_label"] = "fine"
        applied, *_ = merge_human_labels(rows, self.export({"video": rows[0]["video"], "seq": 1, "label": None}))
        assert applied == 0 and rows[0]["human_label"] == "fine"

    def test_reports_labels_that_match_nothing_or_are_not_labels(self):
        rows = [row(1)]
        applied, unmatched, invalid = merge_human_labels(rows, self.export(
            {"video": "other.mp4", "seq": 1, "label": "fine"},
            {"video": rows[0]["video"], "seq": 1, "label": "broken"}))
        assert applied == 0 and unmatched == ["other.mp4 #1"] and invalid and "broken" in invalid[0]

    def test_a_non_export_is_refused(self):
        with pytest.raises(ValueError, match="not a review-sheet export"):
            merge_human_labels([], {"nope": 1})


def test_summary_counts():
    rows = [row(1), row(2, grade="Reject", crack="major", broken=True), row(3, crack="minor")]
    rows[0]["human_label"] = "fine"
    s = summarise(rows)
    assert s == {"tiles": 3, "by_grade": {"Grade A": 2, "Reject": 1}, "corner_broken": 1, "crack_flagged": 2, "human_labelled": 1}


class TestExporterFiles:
    """The folder-level pieces of export_tile_dataset (no videos needed)."""

    def make_dataset(self, tmp_path):
        import json
        from export_tile_dataset import write_labels
        rows = [row(1), row(2, grade="Reject", crack="major", broken=True)]
        write_labels(tmp_path, rows)
        (tmp_path / "manifest.json").write_text(json.dumps({"summary": summarise(rows)}))
        return rows

    def test_labels_are_written_as_matching_json_and_csv(self, tmp_path):
        import json
        rows = self.make_dataset(tmp_path)
        j = json.loads((tmp_path / "labels.json").read_text())
        assert j["columns"] == CSV_COLUMNS and [t["id"] for t in j["tiles"]] == [r["id"] for r in rows]
        c = list(csv.DictReader(open(tmp_path / "labels.csv", encoding="utf-8")))
        assert [x["id"] for x in c] == [r["id"] for r in rows]

    def test_merging_a_review_export_updates_both_files_and_the_manifest(self, tmp_path):
        import json
        from export_tile_dataset import merge
        self.make_dataset(tmp_path)
        export = tmp_path / "export.json"
        export.write_text(json.dumps({"items": [{"video": "WIN_20261011_12_16_03_Pro.mp4", "seq": 2, "label": "chipped"}]}))
        merge(export, tmp_path)
        tiles = json.loads((tmp_path / "labels.json").read_text())["tiles"]
        assert tiles[1]["human_label"] == "chipped" and tiles[0]["human_label"] is None
        assert list(csv.DictReader(open(tmp_path / "labels.csv", encoding="utf-8")))[1]["human_label"] == "chipped"
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert manifest["summary"]["human_labelled"] == 1 and manifest["label_merges"][0]["applied"] == 1

    def test_merging_into_a_folder_that_is_not_a_dataset_is_refused(self, tmp_path):
        from export_tile_dataset import merge
        (tmp_path / "e.json").write_text('{"items": []}')
        with pytest.raises(SystemExit, match="not a tile dataset"):
            merge(tmp_path / "e.json", tmp_path)

    def test_the_context_picture_is_the_tile_with_belt_around_it(self, tmp_path):
        import cv2
        import numpy as np
        from export_tile_dataset import write_context
        from types import SimpleNamespace
        frame = np.full((300, 400, 3), 40, np.uint8)
        region = SimpleNamespace(bbox=(150, 100, 100, 100))
        write_context(frame, region, tmp_path / "c.jpg")
        img = cv2.imread(str(tmp_path / "c.jpg"))
        assert img.shape[0] > 100 and img.shape[1] > 100        # bigger than the bare tile: padding included
        assert img.shape[0] <= 100 + 2 * 24 + 2 * 12 + 4
