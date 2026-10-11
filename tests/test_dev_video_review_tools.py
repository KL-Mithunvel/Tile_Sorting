"""Marks, the corrected tile count and the pipeline cache for the video review tool."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "development"))

from video_review_tools import (  # noqa: E402
    GradedTile,
    ReviewStore,
    cache_path,
    load_cache,
    nearest_graded,
    review_path,
    save_cache,
)


def tile(seq, frame, grade="Grade A"):
    return GradedTile(seq=seq, frame=frame, bbox=(100, 200, 60, 40), grade=grade)


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"not really a video")
    return p


def store(video, graded=None):
    return ReviewStore(video, fps=30.0, frames=900, graded=graded if graded is not None else [tile(1, 100), tile(2, 400)])


def test_nearest_graded_respects_the_window():
    g = [tile(1, 100), tile(2, 400)]
    assert nearest_graded(g, 110, 45).seq == 1
    assert nearest_graded(g, 250, 45) is None
    assert nearest_graded([], 10) is None


def test_corrected_count_is_counted_minus_false_plus_missed(video):
    s = store(video)
    s.add_missed(250, 300, 500)
    s.add_missed(260, 10, 10)
    s.add_false(405)
    assert s.summary() == {"pipeline_counted": 2, "missed": 2, "false_detections": 1, "corrected_count": 3, "notes": 0}


def test_false_detection_attaches_to_the_nearest_tile_once(video):
    s = store(video)
    m = s.add_false(120)
    assert m.seq == 1 and m.frame == 100 and (m.x, m.y) == (130, 220)
    with pytest.raises(ValueError):
        s.add_false(100)                  # already marked
    with pytest.raises(ValueError):
        s.add_false(250)                  # nothing near


def test_note_needs_text(video):
    s = store(video)
    with pytest.raises(ValueError):
        s.add_note(5, "  ")
    assert s.add_note(5, " blurry ").note == "blurry"


def test_undo_and_remove_near(video):
    s = store(video)
    s.add_missed(50, 1, 1)
    s.add_note(300, "x")
    assert s.undo().type == "note"
    assert s.remove_near(52).type == "missed"
    assert s.remove_near(52) is None and s.undo() is None


def test_marks_survive_a_reload_and_a_damaged_file_is_an_empty_review(video):
    s = store(video)
    s.add_missed(250, 300, 500, note="shard")
    again = store(video)
    assert [(m.type, m.frame, m.note) for m in again.marks] == [("missed", 250, "shard")]
    review_path(video).write_text("{broken", encoding="utf-8")
    assert store(video).marks == []


def test_saved_file_carries_the_summary(video):
    store(video).add_missed(250, 3, 4)
    data = json.loads(review_path(video).read_text(encoding="utf-8"))
    assert data["summary"]["corrected_count"] == 3 and data["marks"][0]["type"] == "missed"


def test_recording_folders_keep_files_inside_the_folder(tmp_path):
    rec = tmp_path / "2026-10-12_10-00-00"
    rec.mkdir()
    v = rec / "video.mp4"
    v.write_bytes(b"x")
    assert review_path(v).parent == rec and cache_path(v).parent == rec


def test_cache_is_used_only_for_the_same_file(video):
    save_cache(video, [tile(1, 10), tile(2, 20)])
    assert [g.seq for g in load_cache(video)] == [1, 2]
    assert load_cache(video).__class__ is list and load_cache(video)[0].bbox == (100, 200, 60, 40)
    video.write_bytes(b"re-saved with different content")
    assert load_cache(video) is None


def test_missing_or_garbage_cache_is_none(video):
    assert load_cache(video) is None
    cache_path(video).write_text("nope", encoding="utf-8")
    assert load_cache(video) is None
