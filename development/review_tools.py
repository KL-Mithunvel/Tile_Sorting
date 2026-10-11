"""Pure logic behind the tile review sheet and the threshold tuning that reads its
answers. No cameras, no OpenCV, no files -- so it is unit-tested.

The problem this serves: the corner and crack checks have thresholds that trade
"a damaged tile ships" against "a good tile is rejected", and the right setting
cannot be derived from numbers alone. Someone has to LOOK at the borderline tiles
and say which are really damaged. `select_tiles` picks which tiles are worth
looking at, `tune_corner` / `tune_crack` turn the looking into thresholds.

Labels a reviewer gives a tile:

    fine     no visible damage
    chipped  a piece of the corner or edge is missing
    cracked  a visible crack across the surface
    both     chipped and cracked
    unsure   cannot tell from the picture (left out of the maths)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

LABELS = ("fine", "chipped", "cracked", "both", "unsure")

# Corner readings: `fill` is the share of the tile's own bounding rectangle that is
# tile (1.0 = perfect, lower = material missing); `extent` is how deep the deepest
# gap reaches as a fraction of the tile's side. A tile is flagged "broken" if
# fill < min_fill OR extent > max_extent -- the same rule corner_detection uses.
CONTESTED_FILL = (0.78, 0.93)  # the band where the shipped threshold (0.83) and the candidates (0.86-0.92) disagree
CLEARLY_BROKEN_FILL = 0.78


@dataclass(frozen=True)
class Row:
    """One graded tile, as the review needs to see it."""

    video: str
    seq: int
    fill: float
    extent: float
    corner_broken: bool
    crack_severity: str  # none / minor / major
    crack_length_px: float
    grade: str


def corner_flagged(fill: float, extent: float, min_fill: float, max_extent: float) -> bool:
    return fill < min_fill or extent > max_extent


def select_tiles(rows: list[Row], controls: int = 8, limit: Optional[int] = 60) -> list[tuple[Row, list[str]]]:
    """Which tiles to put in front of a reviewer, most informative first, each
    with the reasons it was picked.

    1. CONTESTED corner readings (fill 0.78-0.93), most uncertain first (closest
       to the 0.88 mid-point). These are where a threshold choice changes the
       answer, so a label here is worth the most.
    2. Tiles the CRACK check flagged -- the least trustworthy check.
    3. Clearly broken tiles (fill < 0.78): easy, but they are the positive
       examples that stop a tuned threshold from missing real damage.
    4. A few CLEAN controls, evenly spread: tiles nothing flagged. If the reviewer
       finds damage in these, the checks are missing things, not just over-firing.

    A tile can match several; it appears once, tagged with all that apply.
    """
    tags: dict[int, list[str]] = {}
    order: list[int] = []

    def add(idx: int, tag: str) -> None:
        if idx not in tags:
            tags[idx] = []
            order.append(idx)
        if tag not in tags[idx]:
            tags[idx].append(tag)

    contested = sorted(
        (i for i, r in enumerate(rows) if CONTESTED_FILL[0] <= r.fill < CONTESTED_FILL[1]),
        key=lambda i: abs(rows[i].fill - 0.88),
    )
    for i in contested:
        add(i, "contested corner")
    for i, r in enumerate(rows):
        if r.crack_severity != "none":
            add(i, f"{r.crack_severity} crack flagged")
    for i, r in enumerate(rows):
        if r.fill < CLEARLY_BROKEN_FILL:
            add(i, "clearly broken (positive example)")

    clean = [i for i, r in enumerate(rows) if i not in tags and r.fill >= CONTESTED_FILL[1] and r.crack_severity == "none"]
    if controls > 0 and clean:
        step = len(clean) / min(controls, len(clean))
        for k in range(min(controls, len(clean))):
            add(clean[int(k * step)], "clean control")

    picked = order if limit is None else order[:limit]
    return [(rows[i], tags[i]) for i in picked]


# ---------------------------------------------------------------- reading labels back


@dataclass(frozen=True)
class Labelled:
    label: str
    fill: float
    extent: float
    crack_severity: str
    crack_length_px: float
    corner_broken: bool = False


def parse_export(data: dict) -> list[Labelled]:
    """Items from the sheet's exported JSON that have a usable label. Raises
    ValueError for a file that is not an export."""
    if not isinstance(data, dict) or "items" not in data:
        raise ValueError("not a review-sheet export (no 'items')")
    out = []
    for it in data["items"]:
        label = it.get("label")
        if label is None:
            continue
        if label not in LABELS:
            raise ValueError(f"unknown label {label!r}; expected one of {LABELS}")
        out.append(
            Labelled(
                label=label,
                fill=float(it["fill"]),
                extent=float(it["extent"]),
                crack_severity=it.get("crack_severity", "none"),
                crack_length_px=float(it.get("crack_length_px", 0.0)),
                corner_broken=bool(it.get("corner_broken", False)),
            )
        )
    return out


def corner_truth(label: str) -> Optional[bool]:
    """True = the corner is damaged, False = it is not, None = do not count it."""
    if label in ("chipped", "both"):
        return True
    if label in ("fine", "cracked"):
        return False
    return None


def crack_truth(label: str) -> Optional[bool]:
    if label in ("cracked", "both"):
        return True
    if label in ("fine", "chipped"):
        return False
    return None


@dataclass(frozen=True)
class Score:
    tp: int  # damaged and flagged: caught
    fn: int  # damaged, not flagged: shipped
    fp: int  # fine, flagged: a good tile rejected
    tn: int

    @property
    def recall(self) -> Optional[float]:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None

    @property
    def false_positive_rate(self) -> Optional[float]:
        return self.fp / (self.fp + self.tn) if self.fp + self.tn else None


def score(truth_flag_pairs: Iterable[tuple[bool, bool]]) -> Score:
    tp = fn = fp = tn = 0
    for truth, flagged in truth_flag_pairs:
        if truth and flagged:
            tp += 1
        elif truth:
            fn += 1
        elif flagged:
            fp += 1
        else:
            tn += 1
    return Score(tp, fn, fp, tn)


def score_corner(items: list[Labelled], min_fill: float, max_extent: float) -> Score:
    pairs = [
        (truth, corner_flagged(it.fill, it.extent, min_fill, max_extent))
        for it in items
        if (truth := corner_truth(it.label)) is not None
    ]
    return score(pairs)


def corner_candidates() -> list[tuple[float, float]]:
    fills = [round(0.80 + 0.01 * k, 2) for k in range(0, 17)]  # 0.80 .. 0.96
    extents = [round(0.06 + 0.01 * k, 2) for k in range(0, 25)] + [1.0]  # 1.0 = extent check off
    return [(f, e) for f in fills for e in extents]


def recommend_corner(
    items: list[Labelled],
    shipped: tuple[float, float],
    max_false_positive_rate: float = 0.05,
) -> dict:
    """The corner threshold pair that catches the most damaged tiles while
    rejecting no more than `max_false_positive_rate` of the good ones.

    Ties (very common on a small sample) go to the pair with the lowest false
    positive rate, then the one closest to the shipped values -- change as little
    as the evidence demands. Returns the shipped pair's score for comparison.
    """
    have_pos = any(corner_truth(it.label) is True for it in items)
    have_neg = any(corner_truth(it.label) is False for it in items)
    result: dict = {"shipped": shipped, "shipped_score": score_corner(items, *shipped),
                    "positives": sum(corner_truth(it.label) is True for it in items),
                    "negatives": sum(corner_truth(it.label) is False for it in items)}
    if not (have_pos and have_neg):
        result["best"] = None
        result["why_not"] = "need at least one damaged and one fine tile to compare"
        return result

    best = None
    for f, e in corner_candidates():
        s = score_corner(items, f, e)
        if (s.false_positive_rate or 0.0) > max_false_positive_rate:
            continue
        distance = abs(f - shipped[0]) + abs(min(e, 0.5) - min(shipped[1], 0.5))
        key = (-(s.recall or 0.0), s.false_positive_rate or 0.0, distance)
        if best is None or key < best[0]:
            best = (key, (f, e), s)
    result["best"] = None if best is None else {"min_fill": best[1][0], "max_extent": best[1][1], "score": best[2]}
    if best is None:
        result["why_not"] = f"no setting keeps good-tile rejections under {max_false_positive_rate:.0%}"
    return result


def score_crack(items: list[Labelled], min_length_px: float) -> Score:
    pairs = [
        (truth, it.crack_length_px >= min_length_px and it.crack_severity != "none")
        for it in items
        if (truth := crack_truth(it.label)) is not None
    ]
    return score(pairs)


def crack_summary(items: list[Labelled]) -> dict:
    """How the crack check did against the labels, and what a length cutoff would do."""
    truth = [(crack_truth(it.label), it) for it in items]
    cracked = [it for t, it in truth if t is True]
    clean = [it for t, it in truth if t is False]
    return {
        "cracked_tiles": len(cracked),
        "caught": sum(1 for it in cracked if it.crack_severity != "none"),
        "missed_entirely": sum(1 for it in cracked if it.crack_severity == "none"),
        "fine_tiles": len(clean),
        "falsely_flagged": sum(1 for it in clean if it.crack_severity != "none"),
        "falsely_flagged_major": sum(1 for it in clean if it.crack_severity == "major"),
        "table": [(length, score_crack(items, length)) for length in range(0, 201, 20)],
        "cracked_lengths": sorted(round(it.crack_length_px) for it in cracked if it.crack_severity != "none"),
        "false_lengths": sorted(round(it.crack_length_px) for it in clean if it.crack_severity != "none"),
    }
