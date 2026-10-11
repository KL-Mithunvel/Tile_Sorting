"""fuse_final / decide_timeout: the worse-of-two rule, and every way the sound
result can fail to count."""

from fusion.final_grade import decide_timeout, fuse_final
from fusion.grades import destination_for, worse_grade
import pytest

BINS = {"Grade A": "bin1", "Grade B": "bin2", "Grade C": "bin3", "Reject": "bin4", "Hold": "manual"}


def fuse(cam, ac, conf, **kw):
    kw.setdefault("vote_enabled", True)
    kw.setdefault("min_confidence", 0.6)
    kw.setdefault("bins", BINS)
    return fuse_final(cam, ac, conf, **kw)


def test_sound_lowers_a_clean_tile_when_it_votes():
    d = fuse("Grade A", "Reject", 0.9)
    assert d.grade == "Reject" and d.acoustic_counted and d.destination == "bin4"


def test_sound_can_never_rescue_a_tile_the_camera_rejected():
    d = fuse("Reject", "Grade A", 0.99)
    assert d.grade == "Reject" and d.acoustic_counted


def test_vote_off_records_sound_but_camera_stands_alone():
    d = fuse("Grade A", "Reject", 0.99, vote_enabled=False)
    assert d.grade == "Grade A" and not d.acoustic_counted
    assert any("vote off" in r for r in d.reasons)


def test_low_confidence_sound_abstains():
    d = fuse("Grade A", "Reject", 0.4)
    assert d.grade == "Grade A" and not d.acoustic_counted
    assert any("abstained" in r for r in d.reasons)


def test_missing_confidence_abstains():
    assert fuse("Grade B", "Reject", None).grade == "Grade B"


def test_no_sound_grade_means_camera_stands_alone():
    d = fuse("Grade B", None, None)
    assert d.grade == "Grade B" and not d.acoustic_counted


def test_invalid_tap_never_counts_even_with_a_grade():
    d = fuse("Grade A", "Reject", 0.99, acoustic_valid=False)
    assert d.grade == "Grade A" and not d.acoustic_counted


def test_unrecognised_camera_grade_is_held_not_guessed():
    assert fuse("Grade Z", "Grade A", 0.9).grade == "Hold"
    assert fuse(None, "Grade A", 0.9).grade == "Hold"


def test_hold_all_overrides_everything():
    d = fuse("Grade A", "Grade A", 0.9, hold_all=True)
    assert d.grade == "Hold" and d.destination == "manual"


def test_timeout_holds_unless_camera_already_rejected():
    assert decide_timeout("Grade A", max_wait_s=30, bins=BINS).grade == "Hold"
    assert decide_timeout(None, max_wait_s=30, bins=BINS).grade == "Hold"
    assert decide_timeout("Reject", max_wait_s=30, bins=BINS).grade == "Reject"
    assert decide_timeout("Reject", max_wait_s=30, bins=BINS, hold_all=True).grade == "Hold"


def test_unmapped_grade_goes_to_manual_bin():
    assert destination_for("Grade C", {"Hold": "manual"}) == "manual"
    assert destination_for("Grade C", None) == "manual"


def test_worse_grade_rejects_typos():
    assert worse_grade("Grade A", "Grade C") == "Grade C"
    with pytest.raises(ValueError):
        worse_grade("Grade A", "grade a")
