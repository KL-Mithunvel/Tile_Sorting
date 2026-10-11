"""Build the tile review sheet: a self-contained HTML page of the borderline tiles
from your videos, for you to label by eye (fine / chipped / cracked / both /
unsure). The labels then set the corner and crack thresholds from evidence --
see `tune_from_labels.py`.

It runs the SHIPPED pipeline (config.yaml, no overrides) over every video in
data/videos/, picks the tiles worth a human look (`review_tools.select_tiles`),
and writes `data/review/review_sheet_<date>_<time>.html` (gitignored, images are
embedded, so the file works on its own and can be opened by double-clicking).

    python development\\build_review_sheet.py
    python development\\build_review_sheet.py --only 12_16_03     # one video
    python development\\build_review_sheet.py --controls 12 --max 80

Running the full pipeline over ~9,000 1080p frames takes about ten minutes.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "camera_node" / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from camera.capture import load_config  # noqa: E402
from camera.process_video import process_video_file  # noqa: E402
from review_tools import Row, select_tiles  # noqa: E402

TEMPLATE = Path(__file__).resolve().parent / "templates" / "review_sheet.html"
THUMB_LONG_SIDE = 380


def _jpeg_b64(img: np.ndarray) -> str:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if not ok:
        raise RuntimeError("could not JPEG-encode a thumbnail")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def render_pair(frame: np.ndarray, region) -> tuple[str, str]:
    """(plain, with-detector-outline) thumbnails of one graded tile, cut from the
    full frame with some surrounding belt so a chipped corner is seen in context.
    The outline is exactly what the corner check measured: the tile's contour
    (red) against its own minimum-area rectangle (blue)."""
    x, y, w, h = region.bbox
    pad = max(24, int(0.12 * max(w, h)))
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(frame.shape[1], x + w + pad), min(frame.shape[0], y + h + pad)
    crop = frame[y0:y1, x0:x1].copy()

    annotated = crop.copy()
    shift = np.array([[[x0, y0]]], dtype=np.int32)
    cv2.drawContours(annotated, [region.contour - shift], -1, (0, 0, 255), 3)
    box = cv2.boxPoints(cv2.minAreaRect(region.contour)).astype(np.int32) - shift[0]
    cv2.polylines(annotated, [box], True, (255, 140, 0), 2)

    scale = THUMB_LONG_SIDE / max(crop.shape[:2])
    size = (max(1, int(crop.shape[1] * scale)), max(1, int(crop.shape[0] * scale)))
    return (
        _jpeg_b64(cv2.resize(crop, size, interpolation=cv2.INTER_AREA)),
        _jpeg_b64(cv2.resize(annotated, size, interpolation=cv2.INTER_AREA)),
    )


def collect(videos: list[Path], config: dict) -> list[tuple[Row, dict]]:
    """Run the pipeline on each video; return (Row, extra) per graded tile, where
    `extra` holds the thumbnails and where in the video the tile was."""
    out: list[tuple[Row, dict]] = []
    for path in videos:
        t0 = time.time()
        info: dict = {}
        fps = cv2.VideoCapture(str(path)).get(cv2.CAP_PROP_FPS) or 30.0

        def on_tile(record, region, frame, frame_index, _path=path, _fps=fps) -> None:
            img, img_det = render_pair(frame, region)
            row = Row(
                video=_path.name, seq=record.seq, fill=record.corner.fill_ratio,
                extent=record.corner.missing_extent_fraction, corner_broken=record.corner.corner_broken,
                crack_severity=record.crack.severity, crack_length_px=record.crack.crack_length_px,
                grade=record.grade,
            )
            out.append((row, {"img": img, "img_det": img_det, "frame": frame_index, "time_s": frame_index / _fps}))

        process_video_file(str(path), config, save_snapshots=False, info=info, on_tile=on_tile)
        print(f"  {path.name}: {info['crossings']} tiles in {time.time() - t0:.0f}s "
              f"(line {info['line_orientation']}, belt in {info['belt_frames']}/{info['frames']} frames)", flush=True)
    return out


def build(videos_dir: Path, out_path: Path, only: str | None, controls: int, limit: int) -> Path:
    videos = sorted(p for p in videos_dir.glob("*") if p.suffix.lower() in {".mp4", ".avi", ".mov", ".mkv", ".m4v"})
    if only:
        videos = [p for p in videos if only in p.name]
    if not videos:
        raise SystemExit(f"no videos found in {videos_dir}" + (f" matching {only!r}" if only else ""))
    print(f"Running the shipped pipeline over {len(videos)} video(s)...", flush=True)
    collected = collect(videos, load_config())

    rows = [r for r, _ in collected]
    chosen = select_tiles(rows, controls=controls, limit=limit)
    index = {id(r): extra for r, extra in collected}
    items = []
    for n, (row, reasons) in enumerate(chosen):
        extra = index[id(row)]
        items.append({
            "id": f"t{n + 1:03d}", "video": row.video, "video_short": row.video.replace("WIN_", "").replace("_Pro.mp4", "").replace(".mp4", "")[:28],
            "seq": row.seq, "frame": extra["frame"], "time_s": extra["time_s"], "grade": row.grade,
            "fill": row.fill, "extent": row.extent, "corner_broken": row.corner_broken,
            "crack_severity": row.crack_severity, "crack_length_px": row.crack_length_px,
            "reasons": reasons, "img": extra["img"], "img_det": extra["img_det"],
        })

    created = datetime.now().strftime("%Y-%m-%d %H:%M")
    sheet_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    payload = json.dumps({"id": sheet_id, "created": created, "items": items}).replace("<", "\\u003c")
    html = TEMPLATE.read_text(encoding="utf-8").replace("__DATA_JSON__", payload)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    kinds = {}
    for _, reasons in chosen:
        kinds[reasons[0]] = kinds.get(reasons[0], 0) + 1
    print(f"\n{len(rows)} tiles graded, {len(items)} on the sheet: {kinds}", flush=True)
    print(f"Wrote {out_path}  ({out_path.stat().st_size / 1_048_576:.1f} MB)", flush=True)
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--videos", type=Path, default=REPO_ROOT / "data" / "videos", help="folder of videos (default data/videos)")
    ap.add_argument("--out", type=Path, default=None, help="output .html (default data/review/review_sheet_<date>_<time>.html)")
    ap.add_argument("--only", default=None, help="only videos whose name contains this text")
    ap.add_argument("--controls", type=int, default=8, help="how many clean tiles to include as controls (default 8)")
    ap.add_argument("--max", type=int, default=60, dest="limit", help="most tiles on the sheet (default 60)")
    args = ap.parse_args()
    out = args.out or REPO_ROOT / "data" / "review" / f"review_sheet_{datetime.now():%Y-%m-%d_%H-%M-%S}.html"
    build(args.videos, out, args.only, args.controls, args.limit)


if __name__ == "__main__":
    main()
