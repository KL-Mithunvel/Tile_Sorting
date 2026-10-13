"""Re-run the real videos and show exactly which tiles changed since a baseline.

    python development\\regression_check.py                      # vs the newest data\\tile_dataset\\*\\labels.json
    python development\\regression_check.py --baseline data\\tile_dataset\\<run>\\labels.json
    python development\\regression_check.py --save-baseline data\\regression_baseline.json
    python development\\regression_check.py --only 12_16_03 --report diff.json

Why: a change to a threshold, the trigger or the frame picker moves some tiles' grades,
and eyeballing 92 tiles to find which is not practical. This runs every video in
data\\videos\\ through the shipped pipeline (the same path as export_tile_dataset.py, no
snapshots written) and prints, per tile, what changed and which check moved it.

The baseline is either an export's labels.json or a file written by --save-baseline.
Tiles are matched on (video, tile number); a changed tile COUNT is reported separately
because every later number in that video may shift.

Exit code: 0 identical, 1 something changed, 2 could not run. So it can gate a change.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "camera_node" / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from regression_tools import compare, format_report, worse_count  # noqa: E402

VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".m4v"}


def newest_labels(root: Path):
    runs = sorted(root.glob("*/labels.json"))
    return runs[-1] if runs else None


def load_rows(path: Path) -> list:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "tiles" in data:  # export_tile_dataset labels.json
        return data["tiles"]
    if isinstance(data, dict) and "rows" in data:  # --save-baseline
        return data["rows"]
    if isinstance(data, list):
        return data
    raise ValueError(f"{path}: not a labels.json or a regression baseline")


def run_videos(videos: list, config: dict) -> list:
    from camera.pipeline import tile_record_to_dict
    from camera.process_video import process_video_file

    rows: list = []
    for video in videos:
        print(f"  {video.name} ...", flush=True)
        records = process_video_file(str(video), config, save_snapshots=False)
        for rec in records:
            d = tile_record_to_dict(rec)
            rows.append({
                "video": video.name,
                "seq": d["seq"],
                "grade": d["grade"],
                "rule_grade": d.get("rule_grade"),
                "crack_severity": d["crack_severity"],
                "corner_broken": d["corner_broken"],
                "corner_fill_ratio": d["corner_fill_ratio"],
            })
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--videos", default=str(REPO_ROOT / "data" / "videos"), help="folder of videos (default data\\videos)")
    ap.add_argument("--only", help="substring of a video file name")
    ap.add_argument("--baseline", help="labels.json of an export, or a --save-baseline file")
    ap.add_argument("--save-baseline", metavar="FILE", help="write this run as a baseline and exit 0")
    ap.add_argument("--report", metavar="FILE", help="also write the diff as JSON")
    args = ap.parse_args(argv)

    folder = Path(args.videos)
    videos = sorted(p for p in folder.iterdir() if p.suffix.lower() in VIDEO_SUFFIXES) if folder.is_dir() else []
    if args.only:
        videos = [v for v in videos if args.only in v.name]
    if not videos:
        print(f"No videos found in {folder}" + (f" matching {args.only!r}" if args.only else ""), file=sys.stderr)
        return 2

    from camera.capture import load_config

    config = load_config()
    print(f"Running {len(videos)} video(s) with the current camera config:")
    current = run_videos(videos, config)

    if args.save_baseline:
        out = Path(args.save_baseline)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"rows": current}, indent=1), encoding="utf-8")
        print(f"Saved {len(current)} tiles to {out}")
        return 0

    base_path = Path(args.baseline) if args.baseline else newest_labels(REPO_ROOT / "data" / "tile_dataset")
    if base_path is None or not base_path.exists():
        print("No baseline: pass --baseline, or save one with --save-baseline FILE.", file=sys.stderr)
        return 2
    baseline = load_rows(base_path)
    if args.only:  # compare like with like
        names = {v.name for v in videos}
        baseline = [r for r in baseline if r["video"] in names]
    print(f"\nBaseline: {base_path}  ({len(baseline)} tiles)\n")

    report = compare(baseline, current)
    print(format_report(report))
    if report["changed"]:
        print(f"\n{worse_count(report)} of the changed tile(s) got a WORSE grade.")
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return 0 if report["identical"] else 1


if __name__ == "__main__":
    sys.exit(main())
