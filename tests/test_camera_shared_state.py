import numpy as np

from camera.corner_detection import CornerResult
from camera.crack_detection import CrackResult
from camera.dashboard import create_app
from camera.pipeline import TileRecord
from camera.worker import SharedState

OK_CRACK = CrackResult(crack_detected=False, crack_length_px=0.0, severity="none", contour=None)
OK_CORNER = CornerResult(
    corner_broken=False, fill_ratio=0.98, missing_area_px=0.0, missing_depth_px=1.0, missing_extent_fraction=0.01
)


def make_record(seq, grade):
    return TileRecord(
        seq=seq, timestamp=1.0, crack=OK_CRACK, corner=OK_CORNER, grade=grade,
        tile_bgr=np.full((20, 30, 3), 128, np.uint8),
    )


def test_tally_counts_every_tile_even_past_the_recent_window():
    state = SharedState(max_recent_tiles=2)
    for i, grade in enumerate(["Grade A", "Grade A", "Reject", "Grade B"], start=1):
        state.record_tile(make_record(i, grade), i)

    status = state.status()
    assert len(status["recent_tiles"]) == 2          # window trimmed...
    assert status["tally"] == {"Grade A": 2, "Reject": 1, "Grade B": 1}  # ...tally not


def test_last_tile_png_is_served_after_a_tile_and_204_before():
    state = SharedState()
    client = create_app(state).test_client()
    assert client.get("/last_tile.png").status_code == 204

    state.record_tile(make_record(1, "Grade A"), 1)
    res = client.get("/last_tile.png")
    assert res.status_code == 200
    assert res.data.startswith(b"\x89PNG")


def test_api_status_exposes_state_tally_and_info():
    state = SharedState()
    state.set_info({"trigger": "line_crossing"})
    state.set_station_state("in_view")
    body = create_app(state).test_client().get("/api/status").get_json()

    assert body["station_state"] == "in_view"
    assert body["info"]["trigger"] == "line_crossing"
    assert body["tally"] == {}
