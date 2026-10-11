"""Tile discovery, verdict storage and export for the annotator."""
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "development"))

from annotation_tools import LABELS_FILE, LabelStore, load_tiles  # noqa: E402
from dataset_tools import build_row, to_csv, video_stem  # noqa: E402
from review_tools import parse_export  # noqa: E402


def _record(seq):
    return {
        "seq": seq, "track_id": 1, "grade": "Grade A", "rule_grade": "Grade A", "crack_severity": "none",
        "crack_detected": False, "crack_length_px": 0.0, "corner_broken": False, "corner_fill_ratio": 0.95,
        "corner_missing_extent_fraction": 0.03, "corner_missing_area_px": 0.0, "sharpness": 10.0,
        "sharpness_candidates": 3, "model_grade": None, "model_confidence": None, "model_station_grade": None,
        "model_used": False, "crop_w": 40, "crop_h": 40, "center_x": 1.0, "center_y": 1.0,
    }


def _png(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.full((40, 40, 3), 120, np.uint8))


@pytest.fixture
def dataset(tmp_path):
    rows = []
    video = "WIN_20261011_12_16_03_Pro.mp4"
    for seq in (1, 2, 3):
        tile, ctx = f"tiles/t{seq}.png", f"context/t{seq}.jpg"
        _png(tmp_path / tile)
        _png(tmp_path / ctx)
        rows.append(build_row(_record(seq), video=video, stem=video_stem(video), frame=10 * seq, fps=30.0,
                              bbox=(0, 0, 40, 40), tile_file=tile, context_file=ctx))
    (tmp_path / "labels.csv").write_text(to_csv(rows), encoding="utf-8")
    return tmp_path


def test_dataset_loads_in_order_with_readings(dataset):
    tiles, kind = load_tiles(dataset)
    assert kind == "dataset"
    assert [t.seq for t in tiles] == [1, 2, 3]
    assert tiles[0].context is not None
    assert tiles[0].readings["fill"] == pytest.approx(0.95)


def test_missing_picture_is_skipped_not_fatal(dataset):
    (dataset / "tiles" / "t2.png").unlink()
    tiles, _ = load_tiles(dataset)
    assert [t.seq for t in tiles] == [1, 3]


def test_plain_folder_reads_sequence_from_snapshot_name(tmp_path):
    _png(tmp_path / "tile_00007_1760000000.png")
    _png(tmp_path / "other.png")
    tiles, kind = load_tiles(tmp_path)
    assert kind == "images"
    assert {t.id: t.seq for t in tiles} == {"other": 1, "tile_00007_1760000000": 7}
    assert all(not t.readings for t in tiles)


def test_empty_folder_is_an_error(tmp_path):
    with pytest.raises(ValueError):
        load_tiles(tmp_path)


def test_verdicts_persist_and_reload(dataset):
    tiles, _ = load_tiles(dataset)
    store = LabelStore(dataset, tiles)
    store.set_label(tiles[0].id, "chipped")
    store.set_tier(tiles[0].id, "3B")
    store.set_note(tiles[1].id, "back face")
    again = LabelStore(dataset, tiles)
    assert again.get(tiles[0].id).label == "chipped"
    assert again.get(tiles[0].id).tier == "3B"
    assert again.get(tiles[1].id).note == "back face"
    assert again.get(tiles[2].id).label is None


def test_clearing_everything_removes_the_entry(dataset):
    tiles, _ = load_tiles(dataset)
    store = LabelStore(dataset, tiles)
    store.set_label(tiles[0].id, "fine")
    store.set_label(tiles[0].id, None)
    assert tiles[0].id not in store.verdicts


def test_unknown_label_rejected(dataset):
    tiles, _ = load_tiles(dataset)
    with pytest.raises(ValueError):
        LabelStore(dataset, tiles).set_label(tiles[0].id, "broken")


def test_corrupt_or_foreign_file_does_not_cost_the_session(dataset):
    tiles, _ = load_tiles(dataset)
    (dataset / LABELS_FILE).write_text("{not json", encoding="utf-8")
    assert LabelStore(dataset, tiles).verdicts == {}
    (dataset / LABELS_FILE).write_text(json.dumps({"items": [{"id": "nope", "label": "fine"}]}), encoding="utf-8")
    assert LabelStore(dataset, tiles).verdicts == {}


def test_next_unlabelled_wraps_and_ends(dataset):
    tiles, _ = load_tiles(dataset)
    store = LabelStore(dataset, tiles)
    store.set_label(tiles[0].id, "fine")
    store.set_label(tiles[1].id, "fine")
    assert store.next_unlabelled(-1) == 2
    assert store.next_unlabelled(2) == 2  # wraps all the way round to the only open one
    store.set_label(tiles[2].id, "unsure")
    assert store.next_unlabelled(0) is None


def test_export_is_readable_by_tune_from_labels(dataset):
    tiles, _ = load_tiles(dataset)
    store = LabelStore(dataset, tiles)
    store.set_label(tiles[0].id, "fine")
    store.set_label(tiles[1].id, "chipped")
    out = json.loads((dataset / LABELS_FILE).read_text(encoding="utf-8"))
    assert len(out["items"]) == 3  # unlabelled tiles are listed with label null
    labelled = parse_export(out)
    assert sorted(i.label for i in labelled) == ["chipped", "fine"]
    assert labelled[0].fill == pytest.approx(0.95)


def test_counts(dataset):
    tiles, _ = load_tiles(dataset)
    store = LabelStore(dataset, tiles)
    store.set_label(tiles[0].id, "fine")
    store.set_label(tiles[1].id, "cracked")
    store.set_tier(tiles[1].id, "4")
    c = store.counts()
    assert (c["total"], c["labelled"], c["fine"], c["cracked"], c["tiered"]) == (3, 2, 1, 1, 1)


def test_defect_marks_persist_export_and_undo(dataset):
    tiles, _ = load_tiles(dataset)
    store = LabelStore(dataset, tiles)
    store.add_mark(tiles[0].id, 10, 20, "crack")
    store.add_mark(tiles[0].id, 30, 22, "crack")
    again = LabelStore(dataset, tiles)
    assert again.get(tiles[0].id).marks == [{"x": 10, "y": 20, "kind": "crack"}, {"x": 30, "y": 22, "kind": "crack"}]
    item = json.loads((dataset / LABELS_FILE).read_text(encoding="utf-8"))["items"][0]
    assert item["marks"][1]["x"] == 30
    assert again.remove_last_mark(tiles[0].id) and len(again.get(tiles[0].id).marks) == 1
    assert again.remove_last_mark(tiles[0].id) and tiles[0].id not in again.verdicts   # last mark gone -> entry gone
    assert not again.remove_last_mark(tiles[0].id)
    with pytest.raises(ValueError):
        again.add_mark(tiles[0].id, 1, 1, "scratch")


def test_marks_do_not_make_a_tile_count_as_labelled(dataset):
    tiles, _ = load_tiles(dataset)
    store = LabelStore(dataset, tiles)
    store.add_mark(tiles[1].id, 5, 5)
    assert store.counts()["labelled"] == 0
    assert store.next_unlabelled(-1) == 0
