"""Turn the review sheet's labels into corner / crack threshold advice.

    python development\\tune_from_labels.py path\\to\\tile_labels_<id>.json
    python development\\tune_from_labels.py labels.json --max-fpr 0.03

It prints, from YOUR labels:

  * how the shipped corner thresholds did (damaged tiles caught / shipped, good
    tiles rejected), and the best setting that keeps good-tile rejections under
    `--max-fpr` (default 5%), with a table around it so you can see the trade;
  * how the crack check did (cracked tiles caught / missed, fine tiles falsely
    flagged) and what a minimum-crack-length cutoff would change.

It changes nothing: it prints numbers, you decide, and the values go into
camera_node/python/camera/config.yaml by hand. A handful of labels says little --
the output states how many it is working from, and says so when it cannot decide.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "camera_node" / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from camera.capture import load_config  # noqa: E402
from review_tools import (  # noqa: E402
    crack_summary,
    parse_export,
    recommend_corner,
    score_corner,
)


def _pct(x) -> str:
    return "  n/a" if x is None else f"{100 * x:4.0f}%"


def _row(label: str, s) -> str:
    return (f"{label:<24} caught {s.tp:>2}/{s.tp + s.fn:<2} ({_pct(s.recall)})   "
            f"good tiles rejected {s.fp:>2}/{s.fp + s.tn:<2} ({_pct(s.false_positive_rate)})")


def report(items, shipped_corner: tuple[float, float], shipped_crack_min_len: float, max_fpr: float) -> str:
    lines = []
    from collections import Counter
    counts = Counter(i.label for i in items)
    lines.append(f"Working from {len(items)} labelled tiles: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    if counts.get("unsure"):
        lines.append(f"  ({counts['unsure']} 'unsure' tiles are left out of the maths)")

    lines += ["", "=== CORNER CHECK (flagged broken if fill < min_fill OR gap depth > max_extent) ==="]
    rec = recommend_corner(items, shipped_corner, max_fpr)
    lines.append(f"damaged corners: {rec['positives']}   fine corners: {rec['negatives']}")
    lines.append(_row(f"shipped {shipped_corner[0]:.2f} / {shipped_corner[1]:.2f}", rec["shipped_score"]))
    best = rec.get("best")
    if best is None:
        lines.append(f"-> no recommendation: {rec.get('why_not')}")
    else:
        lines.append(_row(f"best  {best['min_fill']:.2f} / {best['max_extent']:.2f}", best["score"]))
        lines.append(f"-> suggested: corner_detection.min_fill_ratio: {best['min_fill']:.2f}"
                     + (f"   max_missing_extent_fraction: {best['max_extent']:.2f}" if best["max_extent"] < 1.0
                        else "   (the depth check adds nothing on these labels; leave it)"))
        lines += ["", "   min_fill   caught   good tiles rejected"]
        for f in sorted({round(best["min_fill"] + d, 2) for d in (-0.04, -0.02, 0.0, 0.02, 0.04)}):
            s = score_corner(items, f, best["max_extent"])
            mark = "  <- suggested" if f == best["min_fill"] else ""
            lines.append(f"   {f:.2f}      {s.tp:>2}/{s.tp + s.fn:<2} {_pct(s.recall)}   {s.fp:>2}/{s.fp + s.tn:<2} {_pct(s.false_positive_rate)}{mark}")
        if (rec["positives"] < 8) or (rec["negatives"] < 15):
            lines.append("   note: few labels on one side; treat this as a direction, not a final number.")

    lines += ["", "=== CRACK CHECK ==="]
    cs = crack_summary(items)
    lines.append(f"cracked tiles: {cs['cracked_tiles']}   of which the check caught {cs['caught']}, missed entirely {cs['missed_entirely']}")
    lines.append(f"fine tiles:    {cs['fine_tiles']}   falsely flagged {cs['falsely_flagged']} (of which 'major' = Reject: {cs['falsely_flagged_major']})")
    if cs["cracked_tiles"] and cs["fine_tiles"]:
        lines.append(f"crack lengths flagged on really-cracked tiles (px): {cs['cracked_lengths']}")
        lines.append(f"crack lengths flagged on fine tiles (px):           {cs['false_lengths']}")
        lines += ["", f"   min crack length   caught   fine tiles flagged     (shipped min_crack_length_px = {shipped_crack_min_len:g})"]
        for length, s in cs["table"]:
            lines.append(f"   {length:>4} px            {s.tp:>2}/{s.tp + s.fn:<2} {_pct(s.recall)}   {s.fp:>2}/{s.fp + s.tn:<2} {_pct(s.false_positive_rate)}")
        if cs["missed_entirely"]:
            lines.append(f"   note: {cs['missed_entirely']} cracked tile(s) were not flagged AT ALL; no length cutoff can fix that "
                         "(the edge detector never saw them).")
    else:
        lines.append("-> need both cracked and fine tiles labelled to say anything about cutoffs.")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("labels", type=Path, help="the JSON exported from the review sheet")
    ap.add_argument("--max-fpr", type=float, default=0.05, help="most good tiles you will accept rejecting (default 0.05 = 5%%)")
    args = ap.parse_args()
    if not args.labels.is_file():
        raise SystemExit(f"no such file: {args.labels}")
    try:
        items = parse_export(json.loads(args.labels.read_text(encoding="utf-8")))
    except (ValueError, KeyError) as exc:
        raise SystemExit(f"could not read {args.labels.name}: {exc}")
    if not items:
        raise SystemExit("that file has no labelled tiles yet - label some in the sheet first, then export again")

    cfg = load_config()
    print(report(
        items,
        shipped_corner=(cfg["corner_detection"]["min_fill_ratio"], cfg["corner_detection"]["max_missing_extent_fraction"]),
        shipped_crack_min_len=cfg["crack_detection"]["min_crack_length_px"],
        max_fpr=args.max_fpr,
    ))


if __name__ == "__main__":
    main()
