from fusion.stats import MIN_SAMPLES_FOR_SUGGESTION, summarize_transits
from fusion.store import TileStore


def test_empty_has_no_numbers():
    s = summarize_transits([])
    assert s["n"] == 0 and s["median_s"] is None and s["suggested_max_wait_s"] is None


def test_summary_and_suggestion_need_enough_samples():
    few = summarize_transits([5.0, 6.0, 7.0])
    assert few["n"] == 3 and few["median_s"] == 6.0 and few["suggested_max_wait_s"] is None
    many = summarize_transits([float(x) for x in range(5, 5 + MIN_SAMPLES_FOR_SUGGESTION)])  # 5..14
    assert many["min_s"] == 5.0 and many["max_s"] == 14.0
    assert many["suggested_max_wait_s"] == 30.0  # 14 s x 2 = 28 -> rounded up to 30


def test_ignores_none_and_negative():
    assert summarize_transits([None, -1.0, 4.0])["n"] == 1


def test_store_adds_the_transit_column_to_an_old_database(tmp_path):
    import sqlite3

    p = tmp_path / "old.db"
    db = sqlite3.connect(p)
    db.executescript(
        "CREATE TABLE tiles (tile_id TEXT PRIMARY KEY, seq INTEGER, created_ts REAL NOT NULL, updated_ts REAL NOT NULL,"
        " state TEXT NOT NULL, camera_grade TEXT, camera_json TEXT, acoustic_grade TEXT, acoustic_json TEXT,"
        " acoustic_counted INTEGER NOT NULL DEFAULT 0, final_grade TEXT, destination TEXT, reasons_json TEXT,"
        " delivery TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, ack_ts REAL);"
        "INSERT INTO tiles (tile_id, created_ts, updated_ts, state) VALUES ('OLD', 1, 1, 'final');"
    )
    db.commit()
    db.close()
    s = TileStore(p)  # must migrate, not raise
    s.upsert("NEW", 2.0, state="final", transit_s=7.5)
    assert s.get("OLD") is not None and s.recent_transits() == [7.5]
