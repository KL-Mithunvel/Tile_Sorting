"""Offline batch processing for recorded conveyor footage: runs the same
segmentation + tracker + crack/corner pipeline as the live dashboard worker
(camera/worker.py), but against a saved video file instead of a live webcam.
Reports the total tile flow count (how many tiles crossed the line in the
footage) plus a numbered, crack/corner-graded record for each one.

This is the camera station's own local count for a given clip, the same way
the live dashboard's tile_count is local to that run — not the conveyor's
authoritative encoder-based tracking (see tile_tracker.py's docstring).

Usage (from camera_node/python/, with the venv active):

    python -m camera.process_video path/to/footage.mp4
    python -m camera.process_video path/to/footage.mp4 --report report.json
    python -m camera.process_video path/to/footage.mp4 --no-snapshots

Mirrors live_dashboard.py's CLI shape, but is a synchronous batch tool
(processes the whole file, then exits) rather than a long-running server.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from camera.capture import VideoFileCapture, load_config
from camera.pipeline import TileRecord, process_tile, tile_record_to_dict
from camera.segmentation import TileRegion, segment_tile
from camera.snapshot import resolve_output_dir, save_tile_snapshot
from camera.tile_tracker import TileTracker


def process_video_file(video_path: str, config: dict, save_snapshots: bool = True) -> list[TileRecord]:
    """Runs the full video file to completion and returns one TileRecord per
    tile that crossed the line, in the order they crossed (record.seq is
    that tile's position in this run's flow count, 1-indexed)."""
    seg_cfg = config["segmentation"]
    crack_cfg = config["crack_detection"]
    corner_cfg = config["corner_detection"]
    snapshot_cfg = config["capture_snapshots"]

    tracker = TileTracker(
        min_present_frames=config["tile_tracker"]["min_present_frames"],
        min_absent_frames=config["tile_tracker"]["min_absent_frames"],
    )

    records: list[TileRecord] = []
    best_seen_region: Optional[TileRegion] = None
    seq = 0

    with VideoFileCapture(video_path) as capture:
        while True:
            try:
                frame = capture.read_frame()
            except StopIteration:
                break

            region = segment_tile(
                frame,
                hsv_lower=tuple(seg_cfg["hsv_lower"]),
                hsv_upper=tuple(seg_cfg["hsv_upper"]),
                min_area_px=seg_cfg["min_tile_area_px"],
                morph_kernel_size=seg_cfg["morph_kernel_size"],
            )
            tile_present = region is not None
            if tile_present:
                # Same "largest sighting, not latest" choice as worker.py -
                # see its _loop() comment for why.
                if best_seen_region is None or region.area_px > best_seen_region.area_px:
                    best_seen_region = region

            departed = tracker.process_frame(tile_present)
            if departed and best_seen_region is not None:
                seq += 1
                record = process_tile(
                    seq=seq,
                    region=best_seen_region,
                    canny_low=crack_cfg["canny_low"],
                    canny_high=crack_cfg["canny_high"],
                    min_crack_length_px=crack_cfg["min_crack_length_px"],
                    min_aspect_ratio=crack_cfg["min_aspect_ratio"],
                    minor_severity_max_length_px=crack_cfg["minor_severity_max_length_px"],
                    blur_kernel_size=crack_cfg["blur_kernel_size"],
                    min_fill_ratio=corner_cfg["min_fill_ratio"],
                    border_margin_px=crack_cfg["border_margin_px"],
                    max_missing_extent_fraction=corner_cfg["max_missing_extent_fraction"],
                    tile_size_inches=corner_cfg.get("tile_size_inches"),
                )
                if save_snapshots and snapshot_cfg["enabled"]:
                    path = save_tile_snapshot(record, resolve_output_dir(config))
                    record.snapshot_path = str(path)
                records.append(record)
                best_seen_region = None

    return records


def _print_report(video_path: str, records: list[TileRecord]) -> None:
    print(f"\n{video_path}: {len(records)} tile(s) crossed the line\n")
    if not records:
        return
    header = f"{'#':>4}  {'grade':<9} {'crack':<7} {'length_px':>10} {'corner_broken':>14} {'fill_ratio':>11} {'missing_corner':>15}"
    print(header)
    print("-" * len(header))
    for r in records:
        if r.corner.corner_broken and r.corner.missing_depth_inches is not None:
            missing_str = f"~{r.corner.missing_depth_inches:.1f}in deep"
        elif r.corner.corner_broken:
            missing_str = f"{r.corner.missing_depth_px:.0f}px deep"
        else:
            missing_str = "-"
        print(
            f"{r.seq:>4}  {r.grade:<9} {r.crack.severity:<7} {r.crack.crack_length_px:>10.1f} "
            f"{str(r.corner.corner_broken):>14} {r.corner.fill_ratio:>11.3f} {missing_str:>15}"
        )
    rejects = sum(1 for r in records if r.grade == "Reject")
    print(f"\nTotal tile flow: {len(records)}  |  Reject: {rejects}  |  Grade A/B: {len(records) - rejects}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video_path", help="Path to a recorded video file (any format OpenCV can open)")
    parser.add_argument("--report", metavar="PATH", help="Also write the per-tile results to this JSON file")
    parser.add_argument(
        "--no-snapshots", action="store_true", help="Skip saving tile photos even if config.yaml enables it"
    )
    args = parser.parse_args()

    if not Path(args.video_path).is_file():
        print(f"No such file: {args.video_path}", file=sys.stderr)
        sys.exit(1)

    config = load_config()
    records = process_video_file(args.video_path, config, save_snapshots=not args.no_snapshots)
    _print_report(args.video_path, records)

    if args.report:
        report = {"video_path": args.video_path, "tile_count": len(records), "tiles": [tile_record_to_dict(r) for r in records]}
        Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nWrote report to {args.report}")


if __name__ == "__main__":
    main()
