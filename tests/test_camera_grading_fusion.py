"""One station grade from two graders (2026-10-10).

The rules see cracks/corners; the model sees cosmetic tier (3A/3B/4/5). They are
put on one scale (Grade A/B/C/Reject) and combined as the WORSE of the two, with
the model abstaining when it is unsure.
"""
from types import SimpleNamespace

import numpy as np
import pytest

from camera.pipeline import (
    GRADE_ORDER,
    fuse_grade,
    grading_options,
    process_tile,
    tile_record_to_dict,
    validate_tier_map,
    worse_grade,
)
from camera.segmentation import TileRegion

TIER_MAP = {"3A": "Grade A", "3B": "Grade B", "4": "Grade C", "5": "Reject"}


class Model:
    classes = ["3A", "3B", "4", "5"]

    def __init__(self, label, confidence):
        self.label, self.confidence = label, confidence

    def predict(self, tile_bgr):
        return SimpleNamespace(label=self.label, confidence=self.confidence,
                               probabilities={c: 0.25 for c in self.classes}, inference_ms=1.0)


def region(size=120):
    frame = np.zeros((size * 2, size * 2, 3), np.uint8)
    o = size // 2
    frame[o:o + size, o:o + size] = (60, 90, 170)
    contour = np.array([[[o, o]], [[o + size - 1, o]], [[o + size - 1, o + size - 1]], [[o, o + size - 1]]], np.int32)
    return TileRegion(bbox=(o, o, size, size), area_px=float(size * size), contour=contour,
                      mask=np.zeros(frame.shape[:2], np.uint8), cropped_bgr=frame[o:o + size, o:o + size])


def run(model=None, **kw):
    return process_tile(seq=1, region=region(), canny_low=50, canny_high=150, min_crack_length_px=40,
                        min_aspect_ratio=3.0, minor_severity_max_length_px=100, blur_kernel_size=5,
                        min_fill_ratio=0.83, grading_model=model, **kw)


class TestScale:
    def test_order_is_best_to_worst_and_ends_in_reject(self):
        assert GRADE_ORDER == ("Grade A", "Grade B", "Grade C", "Reject")

    @pytest.mark.parametrize("a,b,worse", [
        ("Grade A", "Grade B", "Grade B"), ("Grade C", "Grade B", "Grade C"),
        ("Reject", "Grade A", "Reject"), ("Grade A", "Grade A", "Grade A"),
    ])
    def test_worse_grade_is_symmetric(self, a, b, worse):
        assert worse_grade(a, b) == worse == worse_grade(b, a)


class TestFuse:
    def test_confident_model_can_downgrade_a_clean_tile(self):
        assert fuse_grade("Grade A", "Grade C", 0.9, 0.6) == ("Grade C", True)

    def test_model_can_never_rescue_a_tile_with_a_defect(self):
        # Rules say Reject (broken corner); a model that thinks the face is 3A must not undo that.
        assert fuse_grade("Reject", "Grade A", 0.99, 0.6) == ("Reject", True)

    def test_unsure_model_abstains_and_the_rules_stand(self):
        assert fuse_grade("Grade A", "Reject", 0.31, 0.6) == ("Grade A", False)

    def test_exactly_at_the_threshold_counts(self):
        assert fuse_grade("Grade A", "Grade B", 0.6, 0.6) == ("Grade B", True)

    @pytest.mark.parametrize("station,conf", [(None, 0.9), ("Grade C", None)])
    def test_no_usable_model_answer_means_rules_only(self, station, conf):
        assert fuse_grade("Grade B", station, conf, 0.6) == ("Grade B", False)


class TestProcessTile:
    def test_no_tier_map_keeps_the_model_display_only(self):
        r = run(Model("5", 0.99))                       # no tier_map -> old behaviour
        assert r.grade == "Grade A" and r.model_grade == "5" and not r.model_used

    def test_confident_bad_tier_downgrades_and_records_both(self):
        r = run(Model("4", 0.9), tier_map=TIER_MAP, model_min_confidence=0.6)
        assert (r.grade, r.rule_grade, r.model_station_grade, r.model_used) == ("Grade C", "Grade A", "Grade C", True)

    def test_tier_5_is_a_reject(self):
        assert run(Model("5", 0.9), tier_map=TIER_MAP).grade == "Reject"

    def test_unsure_model_does_not_change_the_grade_but_is_still_shown(self):
        r = run(Model("5", 0.3), tier_map=TIER_MAP, model_min_confidence=0.6)
        assert r.grade == "Grade A" and not r.model_used
        assert r.model_grade == "5" and r.model_station_grade == "Reject"   # visible, just not counted

    def test_no_model_at_all_is_rules_only(self):
        r = run(None, tier_map=TIER_MAP)
        assert r.grade == r.rule_grade == "Grade A" and r.model_station_grade is None

    def test_dict_carries_the_new_fields(self):
        d = tile_record_to_dict(run(Model("3B", 0.9), tier_map=TIER_MAP))
        assert (d["grade"], d["rule_grade"], d["model_station_grade"], d["model_used"]) == ("Grade B", "Grade A", "Grade B", True)


class TestConfig:
    def test_validate_rejects_a_class_with_no_mapping(self):
        with pytest.raises(ValueError, match="no entry"):
            validate_tier_map({"3A": "Grade A"}, ["3A", "3B"])

    def test_validate_rejects_a_value_that_is_not_a_station_grade(self):
        with pytest.raises(ValueError, match="must be one of"):
            validate_tier_map({"3A": "Excellent"}, ["3A"])

    def test_options_empty_without_a_model_or_a_map(self):
        cfg = {"grading": {"tier_map": TIER_MAP}}
        assert grading_options(cfg, None) == {}
        assert grading_options({}, Model("3A", 1)) == {}

    def test_options_validated_against_the_model_classes(self):
        cfg = {"grading": {"tier_map": {"3A": "Grade A"}}}
        with pytest.raises(ValueError):
            grading_options(cfg, Model("3A", 1))

    def test_options_pass_through(self):
        opts = grading_options({"grading": {"tier_map": TIER_MAP, "model_min_confidence": 0.7}}, Model("3A", 1))
        assert opts == {"tier_map": TIER_MAP, "model_min_confidence": 0.7}
