"""Tile selection for the review sheet, and the label -> threshold maths."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "development"))

from review_tools import (  # noqa: E402
    Labelled,
    Row,
    corner_truth,
    crack_summary,
    crack_truth,
    parse_export,
    recommend_corner,
    score,
    score_corner,
    select_tiles,
)


def row(seq, fill=0.96, extent=0.02, crack="none", length=0.0, video="v"):
    return Row(video, seq, fill, extent, fill < 0.83, crack, length, "Grade A")


class TestSelect:
    def test_contested_first_most_uncertain_first(self):
        rows = [row(1, 0.80), row(2, 0.88), row(3, 0.92), row(4, 0.97), row(5, 0.60)]
        picked = [r.seq for r, _ in select_tiles(rows, controls=0)]
        assert picked[:3] == [2, 3, 1]                   # 0.88 is the most contested, then 0.92, then 0.80
        assert picked[3:] == [5]                         # the clearly broken one after them; the clean one is out

    def test_crack_flagged_and_clearly_broken_are_included_and_tagged(self):
        rows = [row(1, 0.97, crack="major", length=130), row(2, 0.60), row(3, 0.97)]
        got = {r.seq: tags for r, tags in select_tiles(rows, controls=0)}
        assert got[1] == ["major crack flagged"]
        assert got[2] == ["clearly broken (positive example)"]
        assert 3 not in got

    def test_a_tile_matching_several_reasons_appears_once_with_all_tags(self):
        rows = [row(1, 0.85, crack="minor", length=60)]
        ((r, tags),) = select_tiles(rows, controls=0)
        assert tags == ["contested corner", "minor crack flagged"]

    def test_controls_are_clean_tiles_spread_evenly(self):
        rows = [row(i, 0.97) for i in range(1, 21)] + [row(99, 0.85)]
        picked = select_tiles(rows, controls=4)
        controls = [r.seq for r, tags in picked if tags == ["clean control"]]
        assert len(controls) == 4 and controls == sorted(controls) and controls[0] == 1 and controls[-1] >= 15
        assert 99 not in controls

    def test_controls_never_include_a_flagged_tile(self):
        rows = [row(1, 0.97), row(2, 0.97, crack="minor", length=50), row(3, 0.60)]
        controls = [r.seq for r, tags in select_tiles(rows, controls=5) if "clean control" in tags]
        assert controls == [1]

    def test_limit_keeps_the_most_informative(self):
        rows = [row(i, 0.88 + i * 0.001) for i in range(10)] + [row(50, 0.60)]
        picked = select_tiles(rows, controls=0, limit=3)
        assert len(picked) == 3 and all("contested corner" in tags for _, tags in picked)

    def test_nothing_to_review(self):
        assert select_tiles([], controls=5) == []
        assert select_tiles([row(1, 0.97)], controls=0) == []


def lab(label, fill=0.96, extent=0.02, crack="none", length=0.0):
    return Labelled(label, fill, extent, crack, length)


class TestLabels:
    @pytest.mark.parametrize("label,corner,crack", [
        ("fine", False, False), ("chipped", True, False), ("cracked", False, True), ("both", True, True),
        ("unsure", None, None)])
    def test_what_each_label_says_about_each_check(self, label, corner, crack):
        assert corner_truth(label) is corner and crack_truth(label) is crack

    def test_parse_skips_unlabelled_and_rejects_nonsense(self):
        data = {"items": [{"label": None, "fill": 0.9, "extent": 0.1}, {"label": "fine", "fill": 0.96, "extent": 0.02}]}
        assert [x.label for x in parse_export(data)] == ["fine"]
        with pytest.raises(ValueError, match="unknown label"):
            parse_export({"items": [{"label": "broken", "fill": 0.9, "extent": 0.1}]})
        with pytest.raises(ValueError, match="not a review-sheet export"):
            parse_export({"nope": 1})


class TestScoring:
    def test_confusion_counts_and_rates(self):
        s = score([(True, True), (True, True), (True, False), (False, True), (False, False), (False, False)])
        assert (s.tp, s.fn, s.fp, s.tn) == (2, 1, 1, 2)
        assert s.recall == pytest.approx(2 / 3) and s.false_positive_rate == pytest.approx(1 / 3)

    def test_rates_are_none_when_there_is_nothing_to_divide(self):
        assert score([(False, False)]).recall is None
        assert score([(True, True)]).false_positive_rate is None

    def test_corner_flag_rule_matches_the_detector(self):
        items = [lab("chipped", fill=0.855, extent=0.14), lab("fine", fill=0.97, extent=0.01)]
        shipped = score_corner(items, 0.83, 0.22)
        tight = score_corner(items, 0.88, 0.22)
        assert shipped.tp == 0 and shipped.fn == 1
        assert tight.tp == 1 and tight.fp == 0


class TestRecommendCorner:
    def items(self):
        # three visibly chipped tiles the shipped rule misses; good tiles all well above 0.93
        return ([lab("chipped", fill=f, extent=0.10) for f in (0.855, 0.869, 0.878)]
                + [lab("fine", fill=f) for f in (0.96, 0.97, 0.95, 0.94, 0.97, 0.96, 0.95, 0.96, 0.97, 0.95,
                                                 0.96, 0.94, 0.97, 0.95, 0.96, 0.97, 0.96, 0.95, 0.94, 0.97)])

    def test_finds_a_setting_that_catches_the_chips_without_rejecting_good_tiles(self):
        r = recommend_corner(self.items(), shipped=(0.83, 0.22))
        assert r["shipped_score"].recall == 0.0
        best = r["best"]
        assert best["score"].recall == 1.0 and best["score"].fp == 0
        assert 0.879 < best["min_fill"] <= 0.94

    def test_prefers_the_smallest_change_among_equals(self):
        best = recommend_corner(self.items(), shipped=(0.83, 0.22))["best"]
        assert best["min_fill"] == 0.88                   # not 0.94: no reason to move further than needed

    def test_a_false_positive_budget_is_respected(self):
        items = [lab("chipped", fill=0.80)] + [lab("fine", fill=0.85 + 0.005 * k) for k in range(20)]
        best = recommend_corner(items, shipped=(0.83, 0.22), max_false_positive_rate=0.0)["best"]
        assert best["score"].fp == 0

    def test_says_so_when_it_cannot_decide(self):
        only_fine = recommend_corner([lab("fine")] * 5, shipped=(0.83, 0.22))
        assert only_fine["best"] is None and "damaged and one fine" in only_fine["why_not"]

    def test_says_so_when_no_setting_fits_the_budget(self):
        # a damaged tile looks exactly like the good ones: no threshold separates them
        items = [lab("chipped", fill=0.96, extent=0.02)] + [lab("fine", fill=0.96, extent=0.02)] * 10
        r = recommend_corner(items, shipped=(0.83, 0.22), max_false_positive_rate=0.0)
        # every setting that flags the chipped one also flags the fines, so the best it can do is catch nothing
        assert r["best"] is None or r["best"]["score"].recall == 0.0

    def test_unsure_tiles_are_left_out(self):
        items = self.items() + [lab("unsure", fill=0.80)] * 10
        assert recommend_corner(items, shipped=(0.83, 0.22))["positives"] == 3


class TestCrackSummary:
    def test_counts_caught_missed_and_false_alarms(self):
        items = [lab("cracked", crack="major", length=150), lab("cracked", crack="none"),
                 lab("fine", crack="minor", length=70), lab("fine", crack="major", length=130), lab("fine"),
                 lab("both", crack="minor", length=90), lab("chipped")]
        s = crack_summary(items)
        assert (s["cracked_tiles"], s["caught"], s["missed_entirely"]) == (3, 2, 1)
        assert (s["fine_tiles"], s["falsely_flagged"], s["falsely_flagged_major"]) == (4, 2, 1)
        assert s["cracked_lengths"] == [90, 150] and s["false_lengths"] == [70, 130]

    def test_a_length_cutoff_table_is_monotone_in_what_it_flags(self):
        items = [lab("cracked", crack="major", length=150), lab("fine", crack="minor", length=70)]
        flagged = [(sc.tp + sc.fp) for _, sc in crack_summary(items)["table"]]
        assert flagged == sorted(flagged, reverse=True)


class TestReport:
    """The text the tuning script prints (it changes nothing; it must read sensibly)."""

    def make(self):
        from tune_from_labels import report
        return report

    def items(self):
        return ([lab("chipped", fill=f, extent=0.10) for f in (0.855, 0.869, 0.878)]
                + [lab("fine", fill=0.96) for _ in range(20)]
                + [lab("cracked", crack="major", length=150), lab("fine", crack="minor", length=60)])

    def test_recommends_and_shows_shipped_vs_best(self):
        text = self.make()(self.items(), (0.83, 0.22), 40.0, 0.05)
        assert "shipped 0.83 / 0.22" in text and "best  0.88" in text
        assert "suggested: corner_detection.min_fill_ratio: 0.88" in text
        assert "caught  0/3" in text.split("best")[0]          # the shipped setting catches none of the three chips

    def test_says_how_many_labels_it_is_working_from(self):
        text = self.make()(self.items() + [lab("unsure")] * 2, (0.83, 0.22), 40.0, 0.05)
        assert text.startswith("Working from 27 labelled tiles")
        assert "2 'unsure' tiles are left out" in text

    def test_admits_when_it_cannot_decide(self):
        text = self.make()([lab("fine")] * 4, (0.83, 0.22), 40.0, 0.05)
        assert "no recommendation" in text and "need both cracked and fine" in text

    def test_flags_cracks_no_cutoff_can_recover(self):
        text = self.make()([lab("cracked", crack="none"), lab("fine"), lab("cracked", crack="major", length=100)], (0.83, 0.22), 40.0, 0.05)
        assert "not flagged AT ALL" in text
