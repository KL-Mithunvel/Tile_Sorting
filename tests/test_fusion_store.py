import pytest

from fusion.store import TileStore


def test_upsert_creates_then_updates_and_roundtrips_json():
    s = TileStore(":memory:")
    s.upsert("T1", 10.0, seq=1, state="awaiting_tap", camera_grade="Grade A", camera_json={"grade": "Grade A", "n": 3})
    s.upsert("T1", 12.0, state="final", final_grade="Grade B", reasons_json=["a", "b"], acoustic_counted=True)
    row = s.get("T1")
    assert row["created_ts"] == 10.0 and row["updated_ts"] == 12.0
    assert row["camera"] == {"grade": "Grade A", "n": 3}
    assert row["reasons"] == ["a", "b"]
    assert row["acoustic_counted"] is True
    assert row["delivery"] == "pending"


def test_recent_is_newest_first_and_limited():
    s = TileStore(":memory:")
    for i in range(5):
        s.upsert(f"T{i}", float(i), state="final", final_grade="Grade A")
    assert [r["tile_id"] for r in s.recent(3)] == ["T4", "T3", "T2"]


def test_tally_counts_final_grades_only():
    s = TileStore(":memory:")
    s.upsert("T1", 1.0, state="final", final_grade="Grade A")
    s.upsert("T2", 2.0, state="final", final_grade="Grade A")
    s.upsert("T3", 3.0, state="final", final_grade="Reject")
    s.upsert("T4", 4.0, state="awaiting_tap")
    assert s.tally() == {"Grade A": 2, "Reject": 1}


def test_unknown_column_is_refused():
    s = TileStore(":memory:")
    with pytest.raises(ValueError):
        s.upsert("T1", 1.0, nonsense=1)


def test_file_store_survives_reopen(tmp_path):
    p = tmp_path / "sub" / "tiles.db"
    s = TileStore(p)
    s.upsert("T1", 1.0, state="final", final_grade="Reject")
    s.close()
    assert TileStore(p).get("T1")["final_grade"] == "Reject"
