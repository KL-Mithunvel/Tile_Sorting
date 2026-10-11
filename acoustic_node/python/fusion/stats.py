"""Summary of the measured camera -> tap transit times.

`ledger.max_wait_s` has to be longer than the slowest real transit, and the
belt speed is open-loop, so the right number can only come from measuring. The
station records each tile's transit (camera result received -> its tap heard)
and this turns the recent ones into min / median / p95 / max plus a suggestion
the operator can read off the service view. Pure, numpy-free.
"""

from __future__ import annotations

import math
from typing import Iterable, Optional

# A suggestion from fewer samples than this would be a guess dressed as a number.
MIN_SAMPLES_FOR_SUGGESTION = 10


def _percentile(sorted_values: list[float], q: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    frac = pos - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def summarize_transits(values: Iterable[float], margin: float = 2.0) -> dict:
    """{n, min_s, median_s, p95_s, max_s, suggested_max_wait_s}. The suggestion
    is `margin` x the observed maximum rounded up to 5 s — a wait that is too
    short sends good tiles to Hold, one that is too long only delays a Hold, so
    err long — and is None until there are enough samples."""
    vals = sorted(float(v) for v in values if v is not None and v >= 0)
    if not vals:
        return {"n": 0, "min_s": None, "median_s": None, "p95_s": None, "max_s": None, "suggested_max_wait_s": None}
    suggested: Optional[float] = None
    if len(vals) >= MIN_SAMPLES_FOR_SUGGESTION:
        suggested = float(math.ceil(vals[-1] * margin / 5.0) * 5)
    return {
        "n": len(vals),
        "min_s": round(vals[0], 2),
        "median_s": round(_percentile(vals, 0.5), 2),
        "p95_s": round(_percentile(vals, 0.95), 2),
        "max_s": round(vals[-1], 2),
        "suggested_max_wait_s": suggested,
    }
