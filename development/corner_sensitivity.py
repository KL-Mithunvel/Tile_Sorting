"""Decision support for the broken-corner thresholds: what does each setting
cost in good tiles wrongly rejected, and what does it buy in chips caught?

The two thresholds (`corner_detection.min_fill_ratio` and
`max_missing_extent_fraction`) trade against each other, and the trade is a
business call, not a measurement:

  * too lax   -> a chipped tile ships as Grade A (a customer complaint)
  * too tight -> good tiles are rejected (lost revenue)

This script prints both sides of that trade so the choice can be made on
numbers instead of by feel:

  1. FALSE POSITIVES -- the real intact tile photos in data/ should all read
     "intact". For each candidate (min_fill_ratio, max_missing_extent_fraction)
     it prints the share of them that would be flagged broken.
  2. DETECTION -- a synthetic tile with a triangular corner chip of growing
     size. For each candidate it prints the smallest chip that is caught.

Run from the repo root with the venv active:

    python development\\corner_sensitivity.py

Findings recorded 2026-10-10 (378 photos, 9x9 in tiles; chip leg = fraction of
the tile's width, so 0.5 is a 4.5 in leg and 12.5% of the tile's area):

    thresholds (fill / extent)   good tiles rejected   smallest chip caught
    0.83 / 0.22  (shipped)              1.9%             leg 0.55  (~5.0 in)
    0.86 / 0.17                         3.2%             leg 0.50  (~4.5 in)
    0.88 / 0.15                         3.7%             leg 0.45  (~4.0 in)
    0.90 / 0.12                         4.5%             leg 0.40  (~3.6 in)
    0.92 / 0.10                         5.6%             leg 0.30  (~2.7 in)

So the shipped setting lets a ~5 in chip through as intact, and every step toward
catching smaller chips costs roughly 0.5-1 point more good tiles rejected. The
photos' own segmentation noise (p99 extent 0.154, max 0.264) is what sets that
floor: the extent threshold cannot go below it without rejecting intact tiles.

The photos are a poor stand-in for the station camera (different lens, light
and background), and the chip is painted onto ONE real tile from the belt clip,
so treat these as the SHAPE of the trade, not as final numbers. Re-run this once
photos from the real rig, including real damaged tiles, exist.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "camera_node" / "python"))

from camera.capture import load_config  # noqa: E402
from camera.corner_detection import detect_broken_corner  # noqa: E402
from camera.segmentation import locate_tile, segment_tile  # noqa: E402

CANDIDATES = [(0.83, 0.22), (0.86, 0.17), (0.88, 0.15), (0.90, 0.12), (0.92, 0.10)]
CHIP_LEGS = [round(x, 2) for x in np.arange(0.10, 0.85, 0.05)]
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
SKIP_DIRS = ("camera_captures", "roboflow")  # derived/augmented copies, not originals


def intact_photo_metrics() -> tuple[np.ndarray, np.ndarray]:
    """(fill_ratio, missing_extent_fraction) for every intact photo in data/."""
    seg = load_config()["segmentation"]
    fills, extents = [], []
    for path in (REPO_ROOT / "data").rglob("*"):
        if path.suffix.lower() not in IMAGE_SUFFIXES or any(d in str(path) for d in SKIP_DIRS):
            continue
        img = cv2.imread(str(path))
        if img is None:
            continue
        if max(img.shape[:2]) > 1600:
            scale = 1600 / max(img.shape[:2])
            img = cv2.resize(img, None, fx=scale, fy=scale)
        region = segment_tile(
            img, tuple(seg["hsv_lower"]), tuple(seg["hsv_upper"]),
            seg["min_tile_area_px"] * 20, seg["morph_kernel_size"],
        )
        if region is None:
            continue
        # Thresholds disabled: only the measured values matter here.
        result = detect_broken_corner(region.contour, min_fill_ratio=0.0, max_missing_extent_fraction=1.0)
        fills.append(result.fill_ratio)
        extents.append(result.missing_extent_fraction)
    return np.array(fills), np.array(extents)


BELT_GREEN_BGR = (40, 140, 40)  # paint chips in the belt's colour: the tile "isn't there"
CLIP = REPO_ROOT / "data" / "WhatsApp Video 2026-08-26 at 7.27.04 PM.mp4"
CLIP_FRAME = 511  # a clean frame of one whole tile on the belt


def real_tile_frame():
    """One real frame of a tile on the belt, or None if the clip isn't here."""
    if not CLIP.exists():
        return None
    cap = cv2.VideoCapture(str(CLIP))
    cap.set(cv2.CAP_PROP_POS_FRAMES, CLIP_FRAME)
    ok, frame = cap.read()
    cap.release()
    return frame if ok else None


def chipped_real_tile_contour(frame, leg_fraction: float):
    """Contour of the real tile in `frame` after painting a triangular chip off
    one corner, re-segmented with the production settings. Measured on a REAL
    contour on purpose: on a perfect synthetic square the extent metric scores
    the same chip ~2.4x higher (leg 0.35 -> 0.247 vs 0.101 here), because a real
    tile is slightly rotated and rough-edged, which changes how its minimum-area
    rectangle fits. A synthetic square flatters the detector."""
    seg = load_config()["segmentation"]
    region, _ = locate_tile(frame, seg)
    x, y, w, _ = region.bbox
    painted = frame.copy()
    leg = int(leg_fraction * w)
    if leg > 0:
        cv2.fillPoly(painted, [np.array([[x, y], [x + leg, y], [x, y + leg]])], BELT_GREEN_BGR)
    return locate_tile(painted, seg)[0].contour


def smallest_chip_caught(frame, min_fill: float, max_extent: float):
    for leg in CHIP_LEGS:
        contour = chipped_real_tile_contour(frame, leg)
        if detect_broken_corner(contour, min_fill, max_extent).corner_broken:
            return leg
    return None


def main() -> None:
    cfg = load_config()["corner_detection"]
    shipped = (cfg["min_fill_ratio"], cfg["max_missing_extent_fraction"])
    tile_in = cfg.get("tile_size_inches") or 9.0

    fills, extents = intact_photo_metrics()
    if len(fills) == 0:
        print("No intact photos found under data/ - nothing to measure false positives on.")
        return
    print(f"{len(fills)} intact photos segmented.")
    print(f"fill_ratio    p1 {np.percentile(fills, 1):.3f}  p5 {np.percentile(fills, 5):.3f}  "
          f"median {np.median(fills):.3f}  min {fills.min():.3f}")
    print(f"extent        median {np.median(extents):.3f}  p95 {np.percentile(extents, 95):.3f}  "
          f"p99 {np.percentile(extents, 99):.3f}  max {extents.max():.3f}\n")

    frame = real_tile_frame()
    if frame is None:
        print(f"(belt clip not found at {CLIP}: skipping the chip-detection column)\n")

    print(f"{'min_fill':>8} {'max_extent':>10} {'good tiles rejected':>20} {'smallest chip caught':>24}")
    rows = list(CANDIDATES)
    if shipped not in rows:
        rows.insert(0, shipped)
    for min_fill, max_extent in rows:
        false_pos = float(np.mean((fills < min_fill) | (extents > max_extent))) * 100
        if frame is None:
            caught = "n/a"
        else:
            leg = smallest_chip_caught(frame, min_fill, max_extent)
            caught = f"leg {leg:.2f} (~{leg * tile_in:.1f} in)" if leg is not None else "none up to leg 0.80"
        tag = "  <- shipped" if (min_fill, max_extent) == shipped else ""
        print(f"{min_fill:>8.2f} {max_extent:>10.2f} {false_pos:>19.1f}% {caught:>24}{tag}")


if __name__ == "__main__":
    main()
