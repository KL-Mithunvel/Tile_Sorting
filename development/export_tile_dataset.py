"""Run every video through the shipped pipeline and save each graded tile as a labelled
picture, in one dated folder, for reuse later.

    python development\\export_tile_dataset.py                       # all of data\\videos\\
    python development\\export_tile_dataset.py --only 12_16_03       # one video
    python development\\export_tile_dataset.py --merge-labels tile_labels_<id>.json --into data\\tile_dataset\\<run>

What you get (default: data\\tile_dataset\\<date>_<time>\\, gitignored with the rest of data\\):

    README.txt          what is in the folder and how to use it
    manifest.json       the run: date, git commit, settings, per-video facts, counts
    labels.csv / .json  ONE ROW PER TILE: ids, where in which video, the pipeline's grade
                        and every measurement behind it, the model's guess, file names,
                        and empty human_label / human_note columns to fill in later
    tiles/<id>.png      the exact crop the checks and the model were given (lossless)
    context/<id>.jpg    the same tile with some belt around it, full resolution
    config_used.yaml    the exact config.yaml this run used

The pictures are of the frame that was GRADED (with sharpest-frame picking on, the
sharpest near the line). Labels are the pipeline's own readings plus a blank slot for a
human verdict: nothing in here has been checked by eye unless you do it. A tile's id is
stable (video + tile number), so a later run, the review sheet's export, or a hand-edited
CSV can all be matched to the same tile -- see --merge-labels.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "camera_node" / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from camera.capture import load_config  # noqa: E402
from camera.pipeline import tile_record_to_dict  # noqa: E402
from camera.process_video import process_video_file  # noqa: E402
from dataset_tools import (  # noqa: E402
    CSV_COLUMNS,
    build_row,
    merge_human_labels,
    summarise,
    to_csv,
    unique_ids,
)

VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".m4v"}

README = """TILE DATASET  (made {when})
=============================

One row per tile that crossed the trigger line in the videos listed in manifest.json.

  tiles/<id>.png     the exact crop the crack/corner checks and the model were given
  context/<id>.jpg   the same tile with belt around it (full resolution)
  labels.csv / .json one row per tile: {n} tiles, columns below
  manifest.json      date, git commit, the settings used, per-video facts
  config_used.yaml   the exact camera config this run used

What the labels ARE: the pipeline's own readings (grade, crack length, corner fill and
gap depth, sharpness, model guess) and where each tile came from (video, frame, time,
position). What they are NOT: a human's verdict. Nothing here has been checked by eye
unless someone fills in human_label (fine / chipped / cracked / both / unsure) and
human_note. The pipeline's crack check in particular is uncalibrated - do not treat
"crack_severity" as ground truth.

Reuse:
  * re-grade with new thresholds without re-running the videos: every picture is here
  * train or evaluate a model: tiles/ + the human_label column once it is filled in
  * add human labels: label tiles in the review sheet (development/build_review_sheet.py),
    then  python development\\export_tile_dataset.py --merge-labels <export.json> --into <this folder>
  * a tile's id is video + tile number and stays the same between runs

Columns of labels.csv:
  {columns}
"""


def git_state() -> dict:
    """The commit this ran at and whether the tree had uncommitted changes (a run on a
    dirty tree is not exactly reproducible from the commit alone)."""
    def run(*args):
        return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=20).stdout.strip()
    try:
        return {"commit": run("rev-parse", "HEAD") or None, "dirty": bool(run("status", "--porcelain", "--untracked-files=no"))}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}


def write_context(frame, region, path: Path) -> None:
    x, y, w, h = region.bbox
    pad = max(24, int(0.12 * max(w, h)))
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(frame.shape[1], x + w + pad), min(frame.shape[0], y + h + pad)
    cv2.imwrite(str(path), frame[y0:y1, x0:x1], [cv2.IMWRITE_JPEG_QUALITY, 95])


def find_videos(videos_dir: Path) -> list[tuple[Path, str]]:
    """(path, name) of every video: loose files in the folder, plus the dashboard's
    recording folders (<date_time>/video.mp4). A recording is named after its folder --
    every one is called video.mp4, which would give all their tiles the same id."""
    found = [(p, p.name) for p in videos_dir.glob("*") if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES]
    for sub in videos_dir.glob("*"):
        if sub.is_dir():
            for inner in sorted(sub.glob("video.*")):
                if inner.suffix.lower() in VIDEO_SUFFIXES:
                    found.append((inner, sub.name + inner.suffix))
                    break
    return sorted(found, key=lambda pn: pn[1])


def export(videos_dir: Path, out_dir: Path, only: str | None) -> Path:
    videos = find_videos(videos_dir)
    if only:
        videos = [(p, n) for p, n in videos if only in n]
    if not videos:
        raise SystemExit(f"no videos found in {videos_dir}" + (f" matching {only!r}" if only else ""))

    config = load_config()
    # The model's guess is worth recording alongside the rule-based grade. It is unvalidated
    # (it scored ~30% on 4 classes here) and only changes the final grade at >= 60% confidence,
    # which it has never reached on these videos. Skipped quietly if onnxruntime is absent.
    config["grading_model"]["enabled"] = True

    (out_dir / "tiles").mkdir(parents=True, exist_ok=True)
    (out_dir / "context").mkdir(exist_ok=True)
    shutil.copyfile(REPO_ROOT / "camera_node" / "python" / "camera" / "config.yaml", out_dir / "config_used.yaml")

    stems = unique_ids(n for _, n in videos)
    rows: list[dict] = []
    video_info: list[dict] = []
    print(f"Exporting {len(videos)} video(s) to {out_dir}", flush=True)

    for path, name in videos:
        stem = stems[name]
        cap = cv2.VideoCapture(str(path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        before = len(rows)
        t0 = time.time()

        def on_tile(record, region, frame, frame_index, _stem=stem, _name=name, _fps=fps) -> None:
            tid = f"{_stem}_t{record.seq:03d}"
            tile_file, context_file = f"tiles/{tid}.png", f"context/{tid}.jpg"
            cv2.imwrite(str(out_dir / tile_file), record.tile_bgr)  # PNG: lossless
            write_context(frame, region, out_dir / context_file)
            rows.append(build_row(
                tile_record_to_dict(record), video=_name, stem=_stem, frame=frame_index, fps=_fps,
                bbox=region.bbox, tile_file=tile_file, context_file=context_file,
            ))

        info: dict = {}
        process_video_file(str(path), config, save_snapshots=False, info=info, on_tile=on_tile)
        video_info.append({
            "video": name, "stem": stem, "resolution": [width, height], "fps": round(fps, 3),
            "size_mb": round(path.stat().st_size / 1_048_576, 1), "frames": info["frames"],
            "belt_found_in_frames": info["belt_frames"], "line_orientation": info["line_orientation"],
            "tiles": len(rows) - before,
        })
        print(f"  {name}: {len(rows) - before} tiles, {time.time() - t0:.0f}s", flush=True)

    write_labels(out_dir, rows)
    manifest = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "git": git_state(),
        "tool": "development/export_tile_dataset.py",
        "videos": video_info,
        "settings": {
            "trigger_line": config["trigger_line"],
            "grading": config.get("grading"),
            "model_enabled_for_export": True,
            "camera_note": "videos were recorded elsewhere; see each video's resolution/fps",
        },
        "summary": summarise(rows),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out_dir / "README.txt").write_text(
        README.format(when=manifest["created"], n=len(rows), columns=", ".join(CSV_COLUMNS)), encoding="utf-8"
    )
    print(f"\n{len(rows)} tiles -> {out_dir}\n  {manifest['summary']}", flush=True)
    return out_dir


def write_labels(out_dir: Path, rows: list[dict]) -> None:
    (out_dir / "labels.json").write_text(json.dumps({"columns": CSV_COLUMNS, "tiles": rows}, indent=2), encoding="utf-8")
    (out_dir / "labels.csv").write_text(to_csv(rows), encoding="utf-8")


def merge(export_file: Path, dataset: Path) -> None:
    labels_path = dataset / "labels.json"
    if not labels_path.is_file():
        raise SystemExit(f"{dataset} is not a tile dataset folder (no labels.json)")
    rows = json.loads(labels_path.read_text(encoding="utf-8"))["tiles"]
    export_data = json.loads(export_file.read_text(encoding="utf-8"))
    applied, unmatched, invalid = merge_human_labels(rows, export_data)
    write_labels(dataset, rows)
    manifest_path = dataset / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["summary"] = summarise(rows)
        manifest.setdefault("label_merges", []).append(
            {"when": datetime.now().isoformat(timespec="seconds"), "from": export_file.name, "applied": applied}
        )
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"applied {applied} human label(s) to {dataset.name}")
    if unmatched:
        print(f"  {len(unmatched)} label(s) matched no tile (a different video set?): {unmatched[:5]}")
    if invalid:
        print(f"  {len(invalid)} label(s) were not a recognised label and were skipped: {invalid[:5]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--videos", type=Path, default=REPO_ROOT / "data" / "videos", help="folder of videos (default data/videos)")
    ap.add_argument("--out", type=Path, default=None, help="output folder (default data/tile_dataset/<date>_<time>)")
    ap.add_argument("--only", default=None, help="only videos whose name contains this text")
    ap.add_argument("--merge-labels", type=Path, default=None, metavar="EXPORT.json",
                    help="instead of exporting, copy a review-sheet export's human labels into --into")
    ap.add_argument("--into", type=Path, default=None, help="the dataset folder to update (with --merge-labels)")
    args = ap.parse_args()

    if args.merge_labels:
        if not args.into:
            raise SystemExit("--merge-labels needs --into <dataset folder>")
        merge(args.merge_labels, args.into)
        return
    out = args.out or REPO_ROOT / "data" / "tile_dataset" / f"{datetime.now():%Y-%m-%d_%H-%M-%S}"
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} already has files in it; pick a new --out (an export never overwrites)")
    export(args.videos, out, args.only)


if __name__ == "__main__":
    main()
