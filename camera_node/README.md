# Camera Node

Code for the visual inspection station — first in the physical line
(`project_charter.md` §6.1, `Camera_Sorting_Subsystem.md`, `Automation_Architecture.md`
§5.2). Planned target hardware: a webcam connected to an Arduino UNO Q (open option to
move the compute to a Raspberry Pi instead — see `Automation_Architecture.md` §5.2). The
rig isn't physically built yet — everything below runs against a dev laptop's webcam for
now (device index 0 by default, same dev-first pattern `acoustic_node/` used before real
hardware existed).

Folder layout follows the Arduino App Bricks convention (see `app.yaml`), matching
`acoustic_node/` — see that node's `README.md` for the general pattern and its caveats
about App Lab not yet being verified on real hardware. `sketch/` here is still an
unimplemented stub; everything described below is on the Python/Linux side (`python/`).

## What's real vs. stub here

- `python/camera/` is a working, tested vision pipeline (added 2026-08-07):
  tile isolation, crack detection/measurement, broken-corner detection/measurement, a
  debounced tile-crossing counter, per-tile snapshot capture, an offline video-file
  processing tool, and a Flask dashboard that streams the live camera feed + results over
  WiFi. Covered by `tests/test_camera_*.py` (23 tests, synthetic images — see
  `pytest.ini`, which adds `camera_node/python` to `sys.path`).
- `sketch/` and `python/main.py` (the App Lab entry point) are still stubs, same as
  `acoustic_node/` — not yet run against real App Lab or the UNO Q board.

## How the pipeline works

Mirrors `acoustic_node`'s hardware/logic split (`.CLAUDE/CLAUDE.md` Development Rule 1):

| File | Role | Hardware I/O? |
|---|---|---|
| `camera/config.yaml` | All tunables — device index, HSV segmentation range, Canny thresholds, crack/corner thresholds, dashboard host/port | — |
| `camera/segmentation.py` | `segment_tile()` — isolate the tile from the background via an HSV color-range threshold | No — pure, synthetic-image tested |
| `camera/crack_detection.py` | `detect_cracks()` — grayscale → Gaussian blur → Canny edges → keep only long/thin contours → measure length, grade severity | No — pure, synthetic-image tested |
| `camera/corner_detection.py` | `detect_broken_corner()` — contour area vs. its own bounding rectangle's area; a low fill ratio means a corner is missing, and gives a missing-area measurement | No — pure, synthetic-image tested |
| `camera/tile_tracker.py` | `TileTracker` — debounced presence/absence state machine, counts tiles crossing the frame | No — pure, synthetic-sequence tested |
| `camera/pipeline.py` | `process_tile()` — wires the three detectors above into one `TileRecord` per tile, plus a first-pass rule-based grade. `tile_record_to_dict()` — shared JSON/API serialization, used by both the dashboard and `process_video.py` | No — pure glue, `tile_record_to_dict()` unit-tested |
| `camera/snapshot.py` | `save_tile_snapshot()` — writes a departed tile's photo to `data/camera_captures/` (config: `capture_snapshots`), named by its sequence number | Yes — I/O only, not unit-tested |
| `camera/capture.py` | `WebcamCapture` — thin OpenCV `VideoCapture` wrapper. `VideoFileCapture` — same shape, reads a saved video file instead (for `process_video.py`) | Yes — not unit-tested, smoke-tested only |
| `camera/worker.py` | `CameraWorker` — background thread: capture → segment → track → (on tile departure) run the pipeline + save a snapshot, publish into `SharedState`. Keeps the *largest-area* sighting of each tile while it's crossing (not just the last frame before departure), since that's typically the most centered/least-blurred view | Yes — real hardware I/O + threading, not unit-tested |
| `camera/process_video.py` | `process_video_file()` + CLI — offline version of `worker.py`'s loop against a saved video file instead of a live webcam: reports total tile flow count and a numbered per-tile crack/corner result for each tile that crossed, with an optional `--report file.json` | Yes — reads a file, not a live device; not unit-tested, smoke-tested against a synthetic video (see `development/`) |
| `camera/dashboard.py` + `camera/templates/dashboard.html` | Flask app: `/video_feed` (MJPEG stream), `/api/status` (JSON: tile count + recent tile crack/corner results), `/` (dashboard page) | Serves over the network (`host: 0.0.0.0`), not unit-tested (smoke-tested via Flask's test client) |
| `camera/live_dashboard.py` | CLI entry point, mirrors `acoustic_node`'s `live_monitor.py` | — |

**Why classical CV instead of a trained model:** an earlier attempt using a pretrained
crack-classification example (from Arduino's own App Lab examples) only produced a grade
category, not a measurement — see `.CLAUDE/CLAUDE-LOG.md`, 2026-08-07. This pipeline
measures crack length and missing-corner area directly from contours instead, which is
what this project actually needs ("is there a crack, and how big is it").

## Running it today (dev laptop webcam, no rig, no UNO Q)

```bash
cd camera_node/python
python -m camera.live_dashboard
```

Then open `http://<this machine's IP>:5000/` from any browser on the same WiFi network
(or `http://localhost:5000/` on the same machine) — live video feed, running tile count,
and a table of recent tiles with crack severity/length and corner-broken/missing-area.
Each departed tile's photo is also saved to `data/camera_captures/` (gitignored),
named `tile_<seq>_<timestamp>.jpg`, unless `capture_snapshots.enabled: false` in
`config.yaml`. Ctrl+C to stop. Tests run from the repo root as always:
`python -m pytest tests/ -v`.

## Processing a recorded video instead of a live webcam

```bash
cd camera_node/python
python -m camera.process_video path/to/footage.mp4
python -m camera.process_video path/to/footage.mp4 --report report.json
```

Runs the same segmentation/tracker/pipeline as the live dashboard against a saved video
file, then prints the total tile flow count for that clip plus a numbered, graded row per
tile (see `process_video.py` in the table above). `--report` also writes the full
per-tile results as JSON. This is the offline counterpart to the live dashboard's
`tile_count` — same local, per-run count, not the conveyor's authoritative
encoder-based tracking (see Known Limitations below).

## Known limitations — read before trusting any result

- **`config.yaml`'s segmentation/crack/corner thresholds were calibrated 2026-08-11**
  against 311 real terracotta tile photos in `data/` (see `development/README.md`,
  `development/analyze_dataset.py`) — no longer blind guesses. But: every photo used was
  a close-up calibration shot of a **known-intact** tile, so this only established a
  false-positive floor (thresholds loose enough not to flag a healthy tile), not
  true-positive sensitivity to real cracks/broken corners — there are no damaged-tile
  photos yet to validate against. And `segmentation.min_tile_area_px` /
  `crack_detection.border_margin_px` are absolute pixel values tied to the
  resolution/distance those photos were taken at, not the live 640x480 pipeline's —
  re-derive both once the conveyor-mounted camera's real position is fixed. See the
  comments in `config.yaml` and `development/README.md` for the full reasoning.
- **Crack "type" here means severity (minor/major) + length**, not a fine-grained
  taxonomy (hairline vs. structural, edge vs. center). Distinguishing crack types more
  precisely needs real labeled photos to design against.
- **Corner detection used to under-catch diagonal chips — fixed 2026-08-11.** The
  original `fill_ratio` check (contour area / ideal-rectangle area) mathematically misses
  diagonal/triangular corner chips — the realistic way ceramic actually breaks — because a
  triangular chip removes much less *area* than a square notch reaching the same distance
  into the tile. Confirmed empirically: a chip whose two legs each reach halfway across the
  tile's edge only dropped `fill_ratio` to ~0.87, above the production threshold (0.83), so
  it would not have been flagged. Fixed by adding `max_missing_extent_fraction` — a second,
  independent check on how *deep* the gap between the tile's actual shape and its ideal
  rectangle reaches (via a distance transform), which does catch it. `CornerResult` now
  also reports `missing_depth_px`/`missing_area_sq_inches`/`missing_depth_inches` — real
  physical measurements of the break, using the tile's own known size (`tile_size_inches:
  9.0` in `config.yaml`) as the scale reference. See `corner_detection.py`'s docstring for
  the full reasoning. Still does not localize *which* corner is broken — see that
  docstring — and neither check has been validated against a real broken-corner photo yet
  (only one real crack photo has turned up so far, in the tuning photos — see
  `development/README.md`).
- **Tile counting here is a local, camera-frame-based count** for this station's own
  dashboard (the camera station owns the running tile count per `project_charter.md`
  §6.1). It is not the conveyor's authoritative encoder-based tracking
  (`Automation_Architecture.md` §8), which is a separate mechanism for correlating a
  result to a specific physical tile across all three inspection stations.
- The Flask dashboard streaming full video frames over the network is an intentional
  exception to `Automation_Architecture.md` §4's "send only compact results to the
  master" principle — that principle is about the station→master grading pipeline over
  MQTT, not this station-local live-monitor view (`project_charter.md` §14, FR-19).
