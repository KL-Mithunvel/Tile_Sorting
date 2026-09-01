"""Line-crossing demo pipeline for the real conveyor-belt footage under
data/ ("WhatsApp Video 2026-08-26 at 7.27.04 PM.mp4" — a phone recording of
a terracotta tile riding the lab conveyor, not the checkerboard calibration
photos the rest of development/ works from).

Per frame: finds the tile, draws its bounding box, draws a horizontal line
across the belt at the frame's vertical center, and flags the moment the
tile's centroid crosses that line (a stand-in for a future physical
photo-eye/ToF trigger position — see project_charter.md 6.2). Writes an
annotated copy of the video plus a JSON report of each crossing.

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

LINE_FLASH_FRAMES = 12  # how long the crossing line stays red after a hit -
# also doubles as a debounce cooldown (see the flash_countdown gate below),
# so bbox-height jitter right at the line doesn't register several crossings
# for what's really one pass.

_BOX_COLOR = (0, 200, 0)  # BGR
_LINE_COLOR = (255, 180, 0)
_LINE_FLASH_COLOR = (0, 0, 255)
_TEXT_COLOR = (255, 255, 255)


@dataclass
class CrossingEvent:
    seq: int
    frame_index: int
    timestamp_s: float


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
    sanity filter."""
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


def process_conveyor_video(video_path: Path, output_video_path: Path) -> list[CrossingEvent]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video file '{video_path}'")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    line_y = frame_h // 2

    writer = cv2.VideoWriter(str(output_video_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (frame_w, frame_h))

    tracker = TileTracker(TRACKER_MIN_PRESENT_FRAMES, TRACKER_MIN_ABSENT_FRAMES)
    crossings: list[CrossingEvent] = []
    # Which side of the line the tile was last confirmed on - re-armed
    # every time the tile re-crosses to the other side, NOT just once per
    # continuous sighting. A tile can cross the same line repeatedly while
    # staying in frame the whole time (e.g. someone shuttling one tile back
    # and forth to test the trigger point, as this clip does) - latching
    # "already counted" until a full departure would silently swallow every
    # crossing after the first. A real single-pass conveyor tile only ever
    # produces one above->below transition before it leaves frame for good,
    # so this doesn't double-count that case either.
    prev_centroid_y: Optional[float] = None
    flash_countdown = 0
    frame_index = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        bbox = _find_tile_bbox(frame)
        tile_present = bbox is not None
        departed = tracker.process_frame(tile_present)
        if departed:
            prev_centroid_y = None

        annotated = frame.copy()

        if bbox is not None:
            x, y, w, h = bbox
            cv2.rectangle(annotated, (x, y), (x + w, y + h), _BOX_COLOR, 2)
            centroid_y = y + h / 2.0

            if tracker.confirmed_present and prev_centroid_y is not None and flash_countdown <= 0:
                if prev_centroid_y < line_y <= centroid_y:
                    flash_countdown = LINE_FLASH_FRAMES
                    crossings.append(
                        CrossingEvent(
                            seq=len(crossings) + 1,
                            frame_index=frame_index,
                            timestamp_s=round(frame_index / fps, 2),
                        )
                    )
            prev_centroid_y = centroid_y

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

        writer.write(annotated)
        frame_index += 1

    cap.release()
    writer.release()
    return crossings


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
    crossings = process_conveyor_video(video_path, output_video_path)

    print(f"\n{video_path.name}: {len(crossings)} tile line-crossing(s) detected\n")
    for c in crossings:
        print(f"  #{c.seq}  frame {c.frame_index}  t={c.timestamp_s}s")
    print(f"\nAnnotated video written to {output_video_path}")

    report = {
        "video_path": str(video_path),
        "line_crossing_count": len(crossings),
        "crossings": [c.__dict__ for c in crossings],
    }
    Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report written to {args.report}")


if __name__ == "__main__":
    main()
