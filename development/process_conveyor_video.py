"""Line-crossing demo pipeline for the real conveyor-belt footage under
data/ ("WhatsApp Video 2026-08-26 at 7.27.04 PM.mp4" — a phone recording of
a terracotta tile riding the lab conveyor, not the checkerboard calibration
photos the rest of development/ works from).

Per frame: finds the tile, draws its bounding box, draws a horizontal line
across the belt at the frame's vertical center, and flags the moment the
tile's centre crosses that line (a stand-in for a future physical
photo-eye/ToF trigger position — see project_charter.md 6.2). Writes an
annotated copy of the video plus a JSON report of each crossing.

Crossing detection is camera_node's real `LineCrossingDetector`
(camera/line_trigger.py), not a copy of it. This script used to carry its own
hand-rolled "did the centroid pass the line" check with a 12-frame cooldown,
written before that module existed; running the two side by side on this clip
is what turned up the finding below, and the duplicate is now gone.

KNOWN PROBLEM THIS CLIP EXPOSES — two tiles, one identity
---------------------------------------------------------
This footage has **two tiles on the belt at once**, and `segment_tile()`
returns only the single largest region. The two tiles' areas are within ~0.02%
of each other (measured: 26283 vs 26278 px at frame 613), so which one is
"largest" flips frame to frame on nothing but segmentation noise. The reported
centre then teleports ~0.6 of the frame height between two different tiles,
and any centre-based trigger fires spuriously: 12 crossings where a human sees
far fewer, with three of them inside 160 ms (t=20.46/20.52/20.62).

`LineCrossingDetector`'s hysteresis cannot suppress this and is not meant to —
it is a spatial deadband for a tile jittering *at* the line, and these jumps
clear any sane deadband (confirmed by sweeping it to 0.08, which changes
nothing). The old hand-rolled cooldown appeared to give the right answer (9)
only because a blunt 12-frame temporal debounce happens to swallow repeats
that land within 0.4 s — it was masking the problem, not solving it.

The real fix belongs upstream in segmentation/tracking (track N regions and
keep tile identity across frames), not in the trigger, and is a design
decision rather than a tweak — see TODO.md and .claude/CLAUDE.md Known
Technical Debt. Until then this script REPORTS the condition instead of
hiding it: frames holding more than one tile-sized region are counted and
printed, and every candidate region is drawn -- green for the one
`segment_tile()` actually picked, amber for the runners-up it discarded -- so
the flip is visible in the annotated video as the green box jumping between
two tiles.

Why this needed its own tuning instead of reusing camera_node/python/camera/
config.yaml's segmentation values as-is: that config's hsv_lower/upper was
calibrated (development/analyze_dataset.py) against close-up photos of a
tile on a checkerboard sheet, not this footage. Run cold against this video,
it also matches the wood-grain benchtop the conveyor sits on (nearly
identical hue to the terracotta tile - see the README note this script's
docstring below links to), so the "tile" it finds is actually the whole
benchtop, present in every frame => TileTracker never sees a departure =>
development/output/whatsapp_video_report.json came back "tile_count": 0.

Fix applied here: segment_tile() itself (camera_node's real, pure function)
is reused unchanged - it's the belt-relative region of interest around it
that's new. The phone recording isn't a fixed tripod shot (confirmed by
sampling: the green belt's on-screen bounding box drifts ~100px over the
clip), so a hardcoded pixel crop isn't safe either. Instead, each frame's
belt lane is located fresh via its own (very distinct) green hue, and
tile-color segmentation only runs inside that lane +/- a margin - adapting
to the drift automatically instead of assuming a static camera.

Usage (from the repo root, venv active):

    python development\\process_conveyor_video.py
    python development\\process_conveyor_video.py --video "data/other_clip.mp4"
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VIDEO = REPO_ROOT / "data" / "WhatsApp Video 2026-08-26 at 7.27.04 PM.mp4"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

sys.path.insert(0, str(REPO_ROOT / "camera_node" / "python"))

from camera.line_trigger import LineCrossingDetector  # noqa: E402
from camera.segmentation import segment_tile  # noqa: E402
from camera.tile_tracker import TileTracker  # noqa: E402

# --- Tuned against this specific clip (see docstring above) - not a
# reusable camera_node config, just this demo script's constants. ---

# The conveyor belt's mat is a strongly saturated green, easily separated
# from both the terracotta tile and the wood benchtop by hue alone.
BELT_HSV_LOWER = (45, 60, 40)
BELT_HSV_UPPER = (85, 255, 255)
BELT_ROI_MARGIN_PX = 25  # tile overhangs the belt's rails slightly

# The tile itself: warm orange/terracotta. Only ever matched inside the
# belt ROI above, so it no longer needs to also exclude the wood benchtop
# (same hue range) the way a whole-frame threshold would.
TILE_HSV_LOWER = (0, 50, 50)
TILE_HSV_UPPER = (25, 255, 255)
TILE_MIN_AREA_PX = 3000
# Guards against thin sliver false-matches (rail/shadow edges) that pass
# the color+area filter but aren't tile-shaped.
TILE_MIN_ASPECT = 0.4
TILE_MAX_ASPECT = 2.5

TRACKER_MIN_PRESENT_FRAMES = 3
TRACKER_MIN_ABSENT_FRAMES = 5

LINE_FLASH_FRAMES = 12  # purely how long the crossing line stays red after a
# hit. It used to double as a debounce cooldown; that is deliberately gone.
# LineCrossingDetector owns debouncing now, and the temporal cooldown was
# masking the two-tile identity flip described in the module docstring rather
# than fixing it. Display only.

# Matches camera_node/python/camera/config.yaml's trigger_line defaults, so
# this clip exercises the values the station would actually ship with.
LINE_POSITION = 0.5
LINE_ORIENTATION = "horizontal"  # tiles travel down the frame on this clip
LINE_DIRECTION = "forward"  # y increasing; the belt only runs one way
LINE_HYSTERESIS = 0.02

_BOX_COLOR = (0, 200, 0)  # BGR
_OTHER_BOX_COLOR = (0, 190, 255)  # amber: a tile-sized region that is NOT
# the one segment_tile() picked, drawn so the identity flip is visible
_LINE_COLOR = (255, 180, 0)
_LINE_FLASH_COLOR = (0, 0, 255)
_TEXT_COLOR = (255, 255, 255)
_WARN_COLOR = (0, 190, 255)


@dataclass
class CrossingEvent:
    seq: int
    frame_index: int
    timestamp_s: float
    center_y_normalized: float
    direction: str


def _find_belt_roi(frame_bgr: np.ndarray, margin_px: int) -> Optional[tuple[int, int]]:
    """Returns (x0, x1) of the belt lane in this frame, or None if the green
    belt isn't visible at all. Recomputed every frame - see docstring."""
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, BELT_HSV_LOWER, BELT_HSV_UPPER)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    x, _, w, _ = cv2.boundingRect(max(contours, key=cv2.contourArea))
    frame_w = frame_bgr.shape[1]
    return max(0, x - margin_px), min(frame_w, x + w + margin_px)


def _find_tile_bbox(frame_bgr: np.ndarray) -> Optional[tuple[int, int, int, int]]:
    """Full-frame tile bbox (x, y, w, h), or None. Wraps camera_node's real
    segment_tile() with this clip's belt-relative ROI and an aspect-ratio
    sanity filter.

    This is the pipeline's real behaviour: ONE region, the largest. When two
    tiles are in frame it is also where tile identity gets lost — see the
    module docstring, and `_find_tile_candidates()` below, which reports how
    many there actually were."""
    roi_bounds = _find_belt_roi(frame_bgr, BELT_ROI_MARGIN_PX)
    if roi_bounds is None:
        return None
    x0, x1 = roi_bounds
    roi = frame_bgr[:, x0:x1]

    region = segment_tile(
        roi,
        hsv_lower=TILE_HSV_LOWER,
        hsv_upper=TILE_HSV_UPPER,
        min_area_px=TILE_MIN_AREA_PX,
        morph_kernel_size=5,
    )
    if region is None:
        return None

    rx, ry, rw, rh = region.bbox
    aspect = rw / rh if rh else 0
    if not (TILE_MIN_ASPECT <= aspect <= TILE_MAX_ASPECT):
        return None
    return rx + x0, ry, rw, rh


def _find_tile_candidates(frame_bgr: np.ndarray) -> list[tuple[int, int, int, int]]:
    """EVERY tile-sized region in the belt ROI, largest first — the thing
    `segment_tile()` deliberately does not return.

    Diagnostic only. This exists because "the pipeline saw one tile" and
    "there was one tile" are different statements, and on this clip they
    disagree: two tiles sit within ~0.02% of each other in area, so which one
    `_find_tile_bbox()` calls "the" tile flips on noise. Counting the
    candidates is what makes that visible instead of showing up downstream as
    an inexplicable burst of line crossings.

    Applies the same HSV/area/aspect filters as `_find_tile_bbox()` so the
    two are comparable; it just doesn't discard the runners-up.
    """
    roi_bounds = _find_belt_roi(frame_bgr, BELT_ROI_MARGIN_PX)
    if roi_bounds is None:
        return []
    x0, x1 = roi_bounds
    roi = frame_bgr[:, x0:x1]

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, TILE_HSV_LOWER, TILE_HSV_UPPER)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    found: list[tuple[float, tuple[int, int, int, int]]] = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < TILE_MIN_AREA_PX:
            continue
        rx, ry, rw, rh = cv2.boundingRect(contour)
        aspect = rw / rh if rh else 0
        if not (TILE_MIN_ASPECT <= aspect <= TILE_MAX_ASPECT):
            continue
        found.append((area, (rx + x0, ry, rw, rh)))

    found.sort(key=lambda item: item[0], reverse=True)
    return [bbox for _, bbox in found]


def process_conveyor_video(
    video_path: Path, output_video_path: Path
) -> tuple[list[CrossingEvent], dict]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video file '{video_path}'")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    line_y = frame_h // 2

    writer = cv2.VideoWriter(str(output_video_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (frame_w, frame_h))

    tracker = TileTracker(TRACKER_MIN_PRESENT_FRAMES, TRACKER_MIN_ABSENT_FRAMES)
    # camera_node's real trigger, on the same settings config.yaml ships.
    # It needs no "which side was it on last" bookkeeping here — that, the
    # re-arming, the direction filter and the deadband all live inside it.
    crossing_detector = LineCrossingDetector(
        position=LINE_POSITION,
        orientation=LINE_ORIENTATION,
        direction=LINE_DIRECTION,
        hysteresis=LINE_HYSTERESIS,
    )
    crossings: list[CrossingEvent] = []
    flash_countdown = 0
    frame_index = 0
    frames_with_tile = 0
    multi_region_frames = 0
    max_regions = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        # The bbox that drives the trigger comes from camera_node's real
        # segment_tile() via _find_tile_bbox(), NOT from the diagnostic
        # below -- the point of this script is to exercise the real path.
        # _find_tile_candidates() re-does the masking separately and is only
        # ever used to count and draw what segment_tile() threw away.
        bbox = _find_tile_bbox(frame)
        candidates = _find_tile_candidates(frame)
        tile_present = bbox is not None
        # Kept purely as a cross-check to print beside the crossing count:
        # TileTracker debounces presence and is blind to WHICH tile it is
        # looking at, so it cannot see the identity flip. Nothing here depends
        # on it any more -- the trigger owns the decision.
        tracker.process_frame(tile_present)

        if tile_present:
            frames_with_tile += 1
        max_regions = max(max_regions, len(candidates))
        if len(candidates) > 1:
            multi_region_frames += 1

        annotated = frame.copy()

        # Draw the discarded candidates first, so the one the pipeline
        # actually uses sits on top. Watching the green box jump between two
        # tiles is the identity flip the module docstring describes.
        for candidate in candidates:
            if candidate == bbox:
                continue
            ox, oy, ow, oh = candidate
            cv2.rectangle(annotated, (ox, oy), (ox + ow, oy + oh), _OTHER_BOX_COLOR, 2)

        center_normalized = None
        if bbox is not None:
            x, y, w, h = bbox
            cv2.rectangle(annotated, (x, y), (x + w, y + h), _BOX_COLOR, 2)
            center_normalized = ((x + w / 2.0) / frame_w, (y + h / 2.0) / frame_h)

        event = crossing_detector.update(center_normalized)
        if event:
            flash_countdown = LINE_FLASH_FRAMES
            crossings.append(
                CrossingEvent(
                    seq=len(crossings) + 1,
                    frame_index=frame_index,
                    timestamp_s=round(frame_index / fps, 2),
                    center_y_normalized=round(event.coordinate, 4),
                    direction=event.direction,
                )
            )

        line_color = _LINE_FLASH_COLOR if flash_countdown > 0 else _LINE_COLOR
        cv2.line(annotated, (0, line_y), (frame_w, line_y), line_color, 2)
        if flash_countdown > 0:
            flash_countdown -= 1

        cv2.putText(
            annotated,
            f"Tiles crossed: {len(crossings)}",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            _TEXT_COLOR,
            2,
            cv2.LINE_AA,
        )
        if len(candidates) > 1:
            cv2.putText(
                annotated,
                f"{len(candidates)} tile-sized regions - identity unreliable",
                (10, 60),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                _WARN_COLOR,
                2,
                cv2.LINE_AA,
            )

        writer.write(annotated)
        frame_index += 1

    cap.release()
    writer.release()

    diagnostics = {
        "frames": frame_index,
        "frames_with_tile": frames_with_tile,
        "multi_region_frames": multi_region_frames,
        "max_regions_in_one_frame": max_regions,
        "tracker_departures": tracker.tile_count,
        "trigger": {
            "position": LINE_POSITION,
            "orientation": LINE_ORIENTATION,
            "direction": LINE_DIRECTION,
            "hysteresis": LINE_HYSTERESIS,
        },
    }
    return crossings, diagnostics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--video", default=str(DEFAULT_VIDEO), help="Path to the conveyor footage to process")
    parser.add_argument(
        "--report",
        default=str(OUTPUT_DIR / "conveyor_line_crossing_report.json"),
        help="Where to write the JSON crossing report",
    )
    parser.add_argument(
        "--output-video",
        default=str(OUTPUT_DIR / "conveyor_annotated.mp4"),
        help="Where to write the annotated video",
    )
    args = parser.parse_args()

    video_path = Path(args.video)
    if not video_path.is_file():
        print(f"No such file: {video_path}", file=sys.stderr)
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_video_path = Path(args.output_video)
    crossings, diagnostics = process_conveyor_video(video_path, output_video_path)

    print(f"\n{video_path.name}: {len(crossings)} tile line-crossing(s) detected")
    print(
        f"  trigger: camera.line_trigger.LineCrossingDetector, "
        f"{LINE_ORIENTATION} line at {LINE_POSITION}, "
        f"direction={LINE_DIRECTION}, hysteresis={LINE_HYSTERESIS}\n"
    )
    for c in crossings:
        print(
            f"  #{c.seq}  frame {c.frame_index}  t={c.timestamp_s}s  "
            f"cy={c.center_y_normalized}  {c.direction}"
        )

    print(f"\nTile seen in {diagnostics['frames_with_tile']}/{diagnostics['frames']} frames.")
    if diagnostics["multi_region_frames"]:
        # Loud on purpose. Every crossing count above is unreliable while this
        # is non-zero, and the failure is otherwise silent — see the module
        # docstring.
        pct = diagnostics["multi_region_frames"] / diagnostics["frames"] * 100
        print(
            f"\nWARNING: {diagnostics['multi_region_frames']} frames ({pct:.1f}%) held more than\n"
            f"one tile-sized region (max {diagnostics['max_regions_in_one_frame']} at once).\n"
            f"segment_tile() returns only the largest, so tile identity flips between\n"
            f"them on small area differences and the centre jumps between two different\n"
            f"tiles. Crossings above are NOT a trustworthy tile count on this clip.\n"
            f"This is a segmentation/tracking gap, not a trigger bug - the trigger\n"
            f"assumes one tile in frame at a time. See TODO.md.\n"
            f"\n"
            f"Tell them apart by the cy column: a real crossing is recorded just past\n"
            f"the line (cy just over {LINE_POSITION + LINE_HYSTERESIS:.2f}), while an identity flip is recorded\n"
            f"far from it, because the 'centre' teleported to the other tile instead\n"
            f"of travelling there."
        )
        print(
            f"\nFor comparison, TileTracker counted "
            f"{diagnostics['tracker_departures']} departure(s) on the same frames\n"
            f"(it debounces presence only, so it is blind to which tile it is looking at\n"
            f"and does not see the flip at all)."
        )

    print(f"\nAnnotated video written to {output_video_path}")

    report = {
        "video_path": str(video_path),
        "line_crossing_count": len(crossings),
        "crossings": [c.__dict__ for c in crossings],
        "diagnostics": diagnostics,
    }
    Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report written to {args.report}")


if __name__ == "__main__":
    main()
