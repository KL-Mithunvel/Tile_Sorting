"""One grade vocabulary for the whole station.

Same strings the camera node already uses (camera/pipeline.py GRADE_ORDER), so
a camera result needs no translation. `Hold` is the fusion layer's own word
for "do not sort this automatically" — a tile whose sound result never came, a
message that could not be read, an operator's hold-all. It is not a quality
grade and is never produced by a detector.
"""

from __future__ import annotations

from typing import Optional

# Best -> worst.
GRADE_ORDER = ("Grade A", "Grade B", "Grade C", "Reject")
HOLD = "Hold"
FINAL_GRADES = GRADE_ORDER + (HOLD,)


def is_grade(value: object) -> bool:
    return isinstance(value, str) and value in GRADE_ORDER


def worse_grade(a: str, b: str) -> str:
    """The worse of two quality grades (Reject worst). Raises ValueError on
    anything that is not one of GRADE_ORDER — a typo must not silently rank."""
    for g in (a, b):
        if g not in GRADE_ORDER:
            raise ValueError(f"unknown grade {g!r}; expected one of {list(GRADE_ORDER)}")
    return a if GRADE_ORDER.index(a) >= GRADE_ORDER.index(b) else b


def destination_for(grade: str, bins: Optional[dict]) -> str:
    """Where a final grade goes. `bins` is config `final.bins` (grade -> bin
    name). A grade with no entry goes to the manual bin rather than raising:
    the tile is already on the belt and must end up somewhere safe."""
    bins = bins or {}
    return str(bins.get(grade, bins.get(HOLD, "manual")))
