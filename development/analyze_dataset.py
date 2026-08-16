"""Batch-analyzes the real tile photos under data/ to derive data-driven
starting values for camera_node's segmentation/crack/corner thresholds,
instead of the hand-guessed placeholders that were in
camera_node/python/camera/config.yaml before this script existed (see
.CLAUDE/CLAUDE.md Known Technical Debt).

Every photo in data/ is a single intact terracotta tile centered on a
checkerboard calibration sheet (see development/README.md). That lets this
script make two assumptions no interactive tool can verify on its own:

1. A small box at the exact center of every photo is guaranteed to be pure
   tile pixels (the tile is always the largest centered object) - so pixel
   colors sampled there, across the whole dataset, are a real HSV
   distribution to threshold against, not a guess.
2. Since no photo in this calibration set shows an actually damaged tile,
   the corner/crack detectors "should" report healthy on all of them. That
   gives a false-positive FLOOR for corner_detection.min_fill_ratio and
   crack_detection's Canny thresholds - not a true-positive sensitivity
   guarantee, since there are no cracked-tile photos yet to validate
   against (still open, see Known Technical Debt).

Deliberately NOT calibrated here: segmentation.min_tile_area_px. These
photos are close-up calibration shots (tile fills 30-60% of a
4000-6000px-wide frame); the live pipeline runs on 640x480 frames at
whatever distance the final conveyor-mounted camera ends up at (not decided
- see .CLAUDE/CLAUDE.md Known Technical Debt). An absolute pixel-area
threshold from these photos would not transfer. Only scale-independent
properties (hue/saturation/value range, contour fill ratio, edge
thresholds) are recommended.

Usage:
    venv\\Scripts\\activate
    python development\\analyze_dataset.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
OUTPUT_XML = Path(__file__).resolve().parent / "output" / "tile_vision_params_calibrated.xml"

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO_ROOT / "camera_node" / "python"))

from tile_param_tuner import downscale_for_processing, load_image_bgr, save_params_xml  # noqa: E402

from camera.corner_detection import detect_broken_corner  # noqa: E402
from camera.crack_detection import detect_cracks  # noqa: E402
from camera.segmentation import segment_tile  # noqa: E402

CENTER_CROP_FRACTION = 0.12  # central box assumed to be pure tile color
CANNY_CANDIDATES = [(30, 100), (50, 150), (75, 200)]
BORDER_MARGIN_CANDIDATES = [0, 8, 15, 25, 40]

# Most photos in data/ center the tile in frame, so a small center-crop box
# is pure tile color. A minority (found empirically - see the median-saturation
# print below) instead frame the tile off to one side (room left for other
# equipment in some shots), so their center crop lands on the checkerboard/
# floor/background instead. Those two populations are cleanly separated by
# center-crop saturation (observed: background crops cluster under 40,
# on-tile crops cluster over 130 - nothing in between), so this threshold
# filters out the off-center photos rather than letting them skew the HSV
# range with background colors.
CENTER_CROP_MEDIAN_SAT_MIN = 80


def find_dataset_images() -> list[Path]:
    exts = {".jpg", ".jpeg", ".png"}
    return sorted(p for p in DATA_DIR.rglob("*") if p.suffix.lower() in exts)


def center_crop_hsv_pixels(bgr: np.ndarray, fraction: float) -> np.ndarray:
    h, w = bgr.shape[:2]
    bw, bh = max(2, int(w * fraction)), max(2, int(h * fraction))
    cx, cy = w // 2, h // 2
    crop = bgr[cy - bh // 2 : cy + bh // 2, cx - bw // 2 : cx + bw // 2]
    return cv2.cvtColor(crop, cv2.COLOR_BGR2HSV).reshape(-1, 3)


def main() -> None:
    images = find_dataset_images()
    if not images:
        print(f"No images found under {DATA_DIR}")
        return
    print(f"Found {len(images)} images under {DATA_DIR}\n")

    # --- Pass 1: center-crop HSV distribution -> recommend hsv_lower/upper ---
    hsv_samples = []
    working_images: list[np.ndarray] = []
    off_center_count = 0
    for path in images:
        try:
            bgr = downscale_for_processing(load_image_bgr(str(path)))
        except Exception as exc:  # corrupt/unreadable file - skip, don't abort the batch
            print(f"  skipping {path.name}: {exc}")
            continue
        crop_hsv = center_crop_hsv_pixels(bgr, CENTER_CROP_FRACTION)
        if np.median(crop_hsv[:, 1]) < CENTER_CROP_MEDIAN_SAT_MIN:
            off_center_count += 1
            continue  # center crop isn't on the tile in this photo - see note above
        working_images.append(bgr)
        hsv_samples.append(crop_hsv)

    print(
        f"Excluded {off_center_count}/{len(images)} photos where the tile isn't centered "
        f"(center-crop median saturation < {CENTER_CROP_MEDIAN_SAT_MIN}); "
        f"using the remaining {len(working_images)} for calibration.\n"
    )

    all_hsv = np.concatenate(hsv_samples, axis=0)
    p_low, p_high = 0.5, 99.5

    # Hue is mathematically unstable (near-random) on near-gray pixels - the
    # tile's own glare highlights and shadow creases are desaturated/very
    # dark-or-bright, not off-color, but they'd otherwise blow the hue range
    # wide open (observed: unfiltered hue percentile reached 120+, which is
    # most of the color wheel and useless for excluding backgrounds). Sat and
    # Val don't have this problem, so they're percentiled over every pixel -
    # shaded tile pixels legitimately belong in that range.
    well_lit_mask = (all_hsv[:, 1] >= 40) & (all_hsv[:, 2] >= 15) & (all_hsv[:, 2] <= 245)
    hue_pixels = all_hsv[well_lit_mask, 0]
    hue_lower_raw, hue_upper_raw = np.percentile(hue_pixels, [p_low, p_high])
    sat_lower_raw, sat_upper_raw = np.percentile(all_hsv[:, 1], [p_low, p_high])
    val_lower_raw, val_upper_raw = np.percentile(all_hsv[:, 2], [p_low, p_high])
    hsv_lower_raw = np.array([hue_lower_raw, sat_lower_raw, val_lower_raw])
    hsv_upper_raw = np.array([hue_upper_raw, sat_upper_raw, val_upper_raw])

    # Small safety margin outward so per-tile shading/glare/edge pixels
    # still fall inside the range in a live frame, not just this crop.
    margin = np.array([2, 15, 15])
    hsv_lower = np.clip(hsv_lower_raw - margin, [0, 0, 0], [179, 255, 255]).astype(int)
    hsv_upper = np.clip(hsv_upper_raw + margin, [0, 0, 0], [179, 255, 255]).astype(int)

    print("=== Color isolation (HSV) ===")
    print(f"  center-crop sample: {all_hsv.shape[0]} px from {len(hsv_samples)} images "
          f"({well_lit_mask.sum()} well-lit px used for hue)")
    print(f"  raw {p_low}/{p_high} percentile:  lower={hsv_lower_raw}  upper={hsv_upper_raw}")
    print(f"  recommended (with margin):        lower={tuple(hsv_lower)}  upper={tuple(hsv_upper)}\n")

    # --- Pass 2: segment every image with the recommended range, collect
    # contour fill-ratio (for corner_detection.min_fill_ratio) and run each
    # cropped tile through crack detection across a Canny x border-margin
    # grid (see crack_detection.py's border_margin_px - without it, the
    # tile's own silhouette edge at the crop border gets misread as a crack
    # on nearly every photo) ---
    fill_ratios = []
    missing_extents = []
    seg_failures = 0
    combos = [(pair, margin) for pair in CANNY_CANDIDATES for margin in BORDER_MARGIN_CANDIDATES]
    crack_stats = {combo: {"detected": 0, "total": 0} for combo in combos}

    for bgr in working_images:
        h, w = bgr.shape[:2]
        region = segment_tile(
            bgr,
            hsv_lower=tuple(hsv_lower.tolist()),
            hsv_upper=tuple(hsv_upper.tolist()),
            min_area_px=(h * w) * 0.03,  # generous floor, just to reject noise specks
            morph_kernel_size=5,
        )
        if region is None:
            seg_failures += 1
            continue

        # thresholds here don't affect fill_ratio/missing_extent_fraction themselves, only corner_broken
        corner = detect_broken_corner(region.contour, min_fill_ratio=0.0, max_missing_extent_fraction=1.0)
        fill_ratios.append(corner.fill_ratio)
        missing_extents.append(corner.missing_extent_fraction)

        for pair, margin in combos:
            crack = detect_cracks(
                region.cropped_bgr,
                canny_low=pair[0],
                canny_high=pair[1],
                min_crack_length_px=40,
                min_aspect_ratio=4.0,
                minor_severity_max_length_px=120,
                blur_kernel_size=5,
                border_margin_px=margin,
            )
            crack_stats[(pair, margin)]["total"] += 1
            if crack.crack_detected:
                crack_stats[(pair, margin)]["detected"] += 1

    print("=== Segmentation sanity check (recommended HSV range) ===")
    print(f"  segment_tile found a region in {len(working_images) - seg_failures}/{len(working_images)} images")
    if seg_failures:
        print(f"  ({seg_failures} failed to segment - inspect those separately before trusting the range)")
    print()

    fill_arr = np.array(fill_ratios)
    print("=== Corner detection (contour fill ratio on known-intact tiles) ===")
    print(f"  n={len(fill_arr)}  mean={fill_arr.mean():.4f}  min={fill_arr.min():.4f}  "
          f"p1={np.percentile(fill_arr, 1):.4f}  p5={np.percentile(fill_arr, 5):.4f}  "
          f"p50={np.percentile(fill_arr, 50):.4f}")
    recommended_min_fill_ratio = float(np.clip(np.percentile(fill_arr, 1) - 0.02, 0.75, 0.95))
    print(f"  recommended min_fill_ratio (p1 - 0.02 margin, clipped to [0.75, 0.95]): {recommended_min_fill_ratio:.3f}\n")

    extent_arr = np.array(missing_extents)
    print("=== Corner detection (missing-region depth fraction on known-intact tiles) ===")
    print(f"  n={len(extent_arr)}  mean={extent_arr.mean():.4f}  max={extent_arr.max():.4f}  "
          f"p95={np.percentile(extent_arr, 95):.4f}  p99={np.percentile(extent_arr, 99):.4f}  "
          f"p99.5={np.percentile(extent_arr, 99.5):.4f}")
    recommended_max_missing_extent_fraction = float(np.clip(extent_arr.max() + 0.03, 0.1, 0.5))
    print(f"  recommended max_missing_extent_fraction (observed max + 0.03 margin, clipped to [0.1, 0.5]): "
          f"{recommended_max_missing_extent_fraction:.3f}")
    print(
        "  (this catches diagonal/triangular corner chips that fill_ratio alone misses - see "
        "corner_detection.py's docstring)\n"
    )

    print("=== Crack detection false-positive rate on known-intact tiles (canny, border_margin_px) ===")
    best_combo = None
    best_rate = None
    for combo, stats in crack_stats.items():
        rate = stats["detected"] / stats["total"] if stats["total"] else float("nan")
        print(f"  canny={combo[0]} margin={combo[1]:>3}: {stats['detected']}/{stats['total']} flagged "
              f"a crack ({rate:.1%} false-positive rate)")
        if best_rate is None or rate < best_rate:
            best_rate = rate
            best_combo = combo
    best_pair, best_margin = best_combo
    print(f"  recommended canny_low/high + border_margin_px (lowest false-positive rate): "
          f"{best_pair}, margin={best_margin}\n")

    params = {
        "hsv_lower": tuple(hsv_lower.tolist()),
        "hsv_upper": tuple(hsv_upper.tolist()),
        "canny_low": best_pair[0],
        "canny_high": best_pair[1],
        "min_area_fraction": 0.05,
        "min_fill_ratio": recommended_min_fill_ratio,
        "max_missing_extent_fraction": recommended_max_missing_extent_fraction,
    }
    save_params_xml(OUTPUT_XML, params, str(DATA_DIR))
    print(f"Saved recommended parameters to {OUTPUT_XML}")
    print(
        "\nNOTE: min_tile_area_px is NOT recommended here - these are close-up "
        "calibration photos, not frames from the final conveyor-mounted camera "
        "at its real distance (undecided, see .CLAUDE/CLAUDE.md). Re-derive that "
        "one from actual live frames once the camera is mounted."
    )
    print(
        "\nNOTE: border_margin_px isn't part of this tool's saved XML schema (crack_detection.py's "
        "concern, not corner_detection.py's) - copy it into "
        "camera_node/python/camera/config.yaml's crack_detection section by hand "
        f"(recommended above: margin={best_margin})."
    )


if __name__ == "__main__":
    main()
