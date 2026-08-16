from camera.corner_detection import CornerResult
from camera.crack_detection import CrackResult
from camera.pipeline import TileRecord, grade_tile, tile_record_to_dict

OK_CRACK = CrackResult(crack_detected=False, crack_length_px=0.0, severity="none", contour=None)
MINOR_CRACK = CrackResult(crack_detected=True, crack_length_px=60.0, severity="minor", contour=None)
MAJOR_CRACK = CrackResult(crack_detected=True, crack_length_px=200.0, severity="major", contour=None)
OK_CORNER = CornerResult(
    corner_broken=False, fill_ratio=0.98, missing_area_px=0.0, missing_depth_px=1.0, missing_extent_fraction=0.01
)
BROKEN_CORNER = CornerResult(
    corner_broken=True, fill_ratio=0.6, missing_area_px=500.0, missing_depth_px=40.0, missing_extent_fraction=0.3
)


def test_grade_tile_healthy_is_grade_a():
    assert grade_tile(OK_CRACK, OK_CORNER) == "Grade A"


def test_grade_tile_minor_crack_is_grade_b():
    assert grade_tile(MINOR_CRACK, OK_CORNER) == "Grade B"


def test_grade_tile_major_crack_is_reject():
    assert grade_tile(MAJOR_CRACK, OK_CORNER) == "Reject"


def test_grade_tile_broken_corner_is_reject_even_without_a_crack():
    assert grade_tile(OK_CRACK, BROKEN_CORNER) == "Reject"


def test_tile_record_to_dict_excludes_raw_pixels_and_includes_snapshot_path():
    record = TileRecord(
        seq=3,
        timestamp=1710000000.0,
        crack=MINOR_CRACK,
        corner=OK_CORNER,
        grade="Grade B",
        tile_bgr=None,
        snapshot_path="data/camera_captures/tile_00003.jpg",
    )

    result = tile_record_to_dict(record)

    assert "tile_bgr" not in result
    assert result["seq"] == 3
    assert result["grade"] == "Grade B"
    assert result["crack_detected"] is True
    assert result["crack_severity"] == "minor"
    assert result["corner_broken"] is False
    assert result["snapshot_path"] == "data/camera_captures/tile_00003.jpg"


def test_tile_record_to_dict_snapshot_path_defaults_to_none():
    record = TileRecord(
        seq=1, timestamp=0.0, crack=OK_CRACK, corner=OK_CORNER, grade="Grade A", tile_bgr=None
    )

    assert tile_record_to_dict(record)["snapshot_path"] is None
