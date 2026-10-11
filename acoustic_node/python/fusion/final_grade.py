"""The station's final grade: camera result + sound result -> one decision.

Same rule `camera.pipeline.fuse_grade` uses for rules-vs-model: the two
stations see different things, so neither may rescue a tile the other has
condemned — the result is the WORSE of the two. The sound result only gets a
vote when ALL of these hold:

1. the operator/config has enabled it (`vote_enabled`) — it is off by default
   because no sound threshold has been validated against real tiles;
2. the tap was a valid measurement (a real strike, not silence or noise);
3. it produced a grade at all (no reference profile -> it abstains);
4. its confidence is at least `min_confidence`.

Otherwise the sound is recorded and displayed as "not counted" and the camera
grade stands alone. Pure functions, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fusion.grades import GRADE_ORDER, HOLD, destination_for, is_grade, worse_grade


@dataclass(frozen=True)
class FinalDecision:
    grade: str
    destination: str
    acoustic_counted: bool
    reasons: tuple


def fuse_final(
    camera_grade: Optional[str],
    acoustic_grade: Optional[str],
    acoustic_confidence: Optional[float],
    *,
    vote_enabled: bool,
    min_confidence: float,
    acoustic_valid: bool = True,
    bins: Optional[dict] = None,
    hold_all: bool = False,
) -> FinalDecision:
    reasons = []

    if hold_all:
        reasons.append("operator hold-all is on")
        return FinalDecision(HOLD, destination_for(HOLD, bins), False, tuple(reasons))

    if not is_grade(camera_grade):
        reasons.append(f"camera grade {camera_grade!r} is not recognised")
        return FinalDecision(HOLD, destination_for(HOLD, bins), False, tuple(reasons))

    reasons.append(f"camera: {camera_grade}")
    counted = False
    final = camera_grade

    if not acoustic_valid:
        reasons.append("sound: no usable tap measurement, not counted")
    elif acoustic_grade is None:
        reasons.append("sound: no grade (no reference profile), not counted")
    elif not is_grade(acoustic_grade):
        reasons.append(f"sound: unrecognised grade {acoustic_grade!r}, not counted")
    elif not vote_enabled:
        reasons.append(f"sound: {acoustic_grade}, recorded only (vote off), not counted")
    elif acoustic_confidence is None or acoustic_confidence < min_confidence:
        shown = "?" if acoustic_confidence is None else f"{acoustic_confidence:.2f}"
        reasons.append(f"sound: {acoustic_grade} at {shown} < {min_confidence:.2f}, abstained")
    else:
        counted = True
        final = worse_grade(camera_grade, acoustic_grade)
        if final != camera_grade:
            reasons.append(f"sound: {acoustic_grade}, lowered the grade")
        else:
            reasons.append(f"sound: {acoustic_grade}, counted, no change")

    return FinalDecision(final, destination_for(final, bins), counted, tuple(reasons))


def decide_timeout(
    camera_grade: Optional[str],
    *,
    max_wait_s: float,
    bins: Optional[dict] = None,
    hold_all: bool = False,
) -> FinalDecision:
    """No tap was heard for this tile. A tile the camera already rejected stays
    rejected (no sound result could improve it); anything else is HELD for a
    person, never guessed."""
    if camera_grade == "Reject" and not hold_all:
        return FinalDecision(
            "Reject",
            destination_for("Reject", bins),
            False,
            ("camera: Reject", f"sound: no tap heard within {max_wait_s:g} s, not needed"),
        )
    return FinalDecision(
        HOLD,
        destination_for(HOLD, bins),
        False,
        (f"camera: {camera_grade}", f"no tap heard within {max_wait_s:g} s, held for a person"),
    )


__all__ = ["FinalDecision", "fuse_final", "decide_timeout", "GRADE_ORDER"]
