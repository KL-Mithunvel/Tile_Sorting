import numpy as np
import pytest

from camera.corner_detection import CornerResult
from camera.crack_detection import CrackResult
from camera.pipeline import TileRecord, grade_tile, process_tile, tile_record_to_dict
from camera.segmentation import TileRegion

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


# --- Optional ONNX grade model integration -------------------------------
#
# process_tile() takes the model duck-typed (see its docstring: importing the
# real one would drag onnxruntime into every import of the pipeline), so these
# use stand-ins rather than a loaded model. That is the contract being tested:
# "anything with .predict(tile_bgr)", plus the rule that a model failure must
# never cost the station the crack/corner result it already computed.


class FakePrediction:
    def __init__(self, label, confidence, probabilities, inference_ms):
        self.label = label
        self.confidence = confidence
        self.probabilities = probabilities
        self.inference_ms = inference_ms


class FakeModel:
    """Duck-typed stand-in for TileGradeModel."""

    def __init__(self, label="3B"):
        self.label = label
        self.calls = 0

    def predict(self, tile_bgr):
        self.calls += 1
        return FakePrediction(
            label=self.label,
            confidence=0.8123456,
            probabilities={"3A": 0.1, "3B": 0.8123456, "4": 0.05, "5": 0.0376544},
            inference_ms=2.53456,
        )


class ExplodingModel:
    """A model that fails the way a real one might — a bad image, a corrupt
    session, an ONNX Runtime error mid-run."""

    def predict(self, tile_bgr):
        raise RuntimeError("ONNX session died")


def plain_tile_region(size=120):
    """A clean synthetic square tile, as segment_tile() would hand over."""
    frame = np.zeros((size * 2, size * 2, 3), dtype=np.uint8)
    frame[size // 2 : size // 2 + size, size // 2 : size // 2 + size] = (60, 90, 170)
    mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    mask[size // 2 : size // 2 + size, size // 2 : size // 2 + size] = 255
    contour = np.array(
        [
            [[size // 2, size // 2]],
            [[size // 2 + size - 1, size // 2]],
            [[size // 2 + size - 1, size // 2 + size - 1]],
            [[size // 2, size // 2 + size - 1]],
        ],
        dtype=np.int32,
    )
    return TileRegion(
        bbox=(size // 2, size // 2, size, size),
        area_px=float(size * size),
        contour=contour,
        mask=mask,
        cropped_bgr=frame[size // 2 : size // 2 + size, size // 2 : size // 2 + size],
    )


def run_process_tile(**kwargs):
    defaults = dict(
        seq=1,
        region=plain_tile_region(),
        canny_low=50,
        canny_high=150,
        min_crack_length_px=40,
        min_aspect_ratio=3.0,
        minor_severity_max_length_px=100,
        blur_kernel_size=5,
        min_fill_ratio=0.83,
    )
    defaults.update(kwargs)
    return process_tile(**defaults)


def test_process_tile_without_a_model_leaves_the_model_fields_none():
    record = run_process_tile()

    assert record.model_grade is None
    assert record.model_confidence is None
    assert record.model_probabilities is None
    assert record.model_inference_ms is None


def test_process_tile_records_the_model_prediction_alongside_the_rule_grade():
    model = FakeModel(label="4")
    record = run_process_tile(grading_model=model)

    assert model.calls == 1
    assert record.model_grade == "4"
    assert record.model_confidence == pytest.approx(0.8123456)
    assert record.model_inference_ms == pytest.approx(2.53456)
    # The station's own decision must be untouched by the model: the model
    # cannot see cracks or broken corners at all.
    assert record.grade == "Grade A"


def test_process_tile_survives_a_model_that_raises(capsys):
    """A classifier blowing up must not cost the station the crack/corner
    result it already has — that is the measurement it actually sorts on."""
    record = run_process_tile(grading_model=ExplodingModel())

    assert record.grade == "Grade A"  # rule-based result still delivered
    assert record.model_grade is None
    assert "prediction failed" in capsys.readouterr().out


def test_process_tile_records_the_centre_and_the_trigger_that_fired_it():
    region = plain_tile_region()
    record = run_process_tile(region=region, trigger="line_crossing")

    assert record.trigger == "line_crossing"
    assert record.center == region.center


def test_tile_record_to_dict_rounds_and_flattens_the_model_fields():
    record = run_process_tile(grading_model=FakeModel(label="3B"), trigger="line_crossing")

    result = tile_record_to_dict(record)

    assert result["model_grade"] == "3B"
    assert result["model_confidence"] == 0.8123        # rounded to 4dp
    assert result["model_inference_ms"] == 2.53        # rounded to 2dp
    assert result["model_probabilities"]["3A"] == 0.1
    assert result["trigger"] == "line_crossing"
    # Centre is flattened into two scalars so the dashboard's JSON stays flat.
    assert result["center_x"] == pytest.approx(120.0)
    assert result["center_y"] == pytest.approx(120.0)


def test_tile_record_to_dict_handles_a_record_with_no_model_or_centre():
    """A record from a station with grading off must serialize without
    KeyErrors or None-arithmetic — the dashboard reads the same shape either
    way."""
    record = TileRecord(
        seq=1, timestamp=0.0, crack=OK_CRACK, corner=OK_CORNER, grade="Grade A", tile_bgr=None
    )

    result = tile_record_to_dict(record)

    assert result["model_grade"] is None
    assert result["model_confidence"] is None
    assert result["model_probabilities"] is None
    assert result["center_x"] is None
    assert result["center_y"] is None
    assert result["trigger"] is None
