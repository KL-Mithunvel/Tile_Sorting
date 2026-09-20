# Camera Node

Code for the visual inspection station — first in the physical line
(`project_charter.md` §6.1, `Camera_Sorting_Subsystem.md`, `Automation_Architecture.md`
§5.2).

**Target hardware: a Raspberry Pi 5 (16 GB RAM) running Raspberry Pi OS Desktop
(64-bit)**, with either a CSI Raspberry Pi camera module or a USB webcam. Decided
2026-09-20 — see `Automation_Architecture.md` §5.2 for the full reasoning. The short
version: of the camera and acoustic stations, only acoustic actually uses the UNO Q's
real-time MCU half (ToF trigger → solenoid ball-drop → capture window), while only
camera is compute-bound (per-frame segmentation + a per-tile CNN/ViT classifier). So the
camera station took the Pi and the acoustic station kept the UNO Q.

The rig isn't physically built yet, and **no Pi has been set up** — everything below has
only ever run against a dev laptop's webcam on Windows.

## This node is not an App Bricks project

`acoustic_node/` and `pick_place_node/` follow Arduino's App Bricks convention
(`app.yaml` + `sketch/` + `python/`) because they run on an UNO Q. This node doesn't, and
as of 2026-09-20 its `app.yaml` and `sketch/` were removed — they described an MCU this
station will never have. `Automation_Architecture.md` §5.7 records the amendment: the
convention applies to a station only if that station's code actually runs on an UNO Q.

```text
camera_node/
  python/          # the service: main.py + the camera/ package
  deploy/          # Raspberry Pi install: systemd unit, install script, pinned deps
  README.md
```

`python/` keeps its name even though there's no `sketch/` to contrast it with any more —
`pytest.ini`'s `pythonpath`, every documented command, and every path reference in
`documents/` already point at it. `python/main.py` is now a real systemd service entry
point (a thin shim over `camera.live_dashboard.main()`), not an App Lab `App.run()` stub.

## What's real vs. not

- `python/camera/` is a working, tested vision pipeline: tile isolation, crack
  detection/measurement, broken-corner detection/measurement, a debounced tile-crossing
  counter, per-tile snapshot capture, an offline video-file processing tool, and a Flask
  dashboard streaming the live feed + results over the network. Covered by
  `tests/test_camera_*.py` (synthetic images — see `pytest.ini`, which adds
  `camera_node/python` to `sys.path`).
- `camera/capture.py`'s **`PiCameraCapture` has never run** — written 2026-09-20 against
  the Picamera2 API, with no CSI module attached. `WebcamCapture` is the only live
  backend that has been exercised at all, and only on a Windows laptop.
- `deploy/` has never run. See `deploy/README.md`.

## Capture backends

`create_capture(config)` picks one from `config.yaml`'s `camera.backend`:

| `backend` | Class | Use |
|---|---|---|
| `"auto"` (default) | whichever applies | CSI module if libcamera reports one, else USB. Lets one config file work on the dev laptop and on the Pi unchanged. |
| `"usb"` | `WebcamCapture` | USB/UVC camera via OpenCV. Set `camera.api_preference: "v4l2"` on the Pi. |
| `"picamera2"` | `PiCameraCapture` | CSI Raspberry Pi camera module via Picamera2. Pi only; the import is lazy so this module still loads on Windows. |

```bash
python -m camera.live_dashboard --list-devices   # what can this machine open?
python -m camera.live_dashboard --backend usb    # override config for one run
```

Two Pi-specific traps, both handled in config rather than code:

- **Channel order.** libcamera names formats by packing order, so Picamera2's `"RGB888"`
  already returns B,G,R — what OpenCV wants, no conversion. If colours look inverted on
  the bench, flip `camera.picamera2.swap_rb`; a wrong channel order would silently
  destroy the calibrated HSV segmentation range rather than throw.
- **Auto-exposure / AWB.** Left on, the sensor re-exposes per tile and the HSV range
  stops meaning anything. Lock exposure/gain/white balance in
  `camera.picamera2.controls` once the enclosure lighting is final — there's a worked
  example in `config.yaml`.

## How the pipeline works

Mirrors `acoustic_node`'s hardware/logic split (`.claude/CLAUDE.md` Development Rule 1):

| File | Role | Hardware I/O? |
|---|---|---|
| `camera/config.yaml` | All tunables — capture backend, device index, HSV segmentation range, Canny thresholds, crack/corner thresholds, dashboard host/port | — |
| `camera/segmentation.py` | `segment_tile()` — isolate the tile from the background via an HSV color-range threshold | No — pure, synthetic-image tested |
| `camera/crack_detection.py` | `detect_cracks()` — grayscale → Gaussian blur → Canny edges → keep only long/thin contours → measure length, grade severity | No — pure, synthetic-image tested |
| `camera/corner_detection.py` | `detect_broken_corner()` — contour area vs. its own bounding rectangle's area, plus a distance-transform check on how deep any gap reaches; gives a missing-area/depth measurement | No — pure, synthetic-contour tested |
| `camera/tile_tracker.py` | `TileTracker` — debounced presence/absence state machine, counts tiles crossing the frame | No — pure, synthetic-sequence tested |
| `camera/line_trigger.py` | `LineCrossingDetector` — fires once when a tile's centre crosses a configured line, in normalized (resolution-independent) coordinates, with a hysteresis deadband against segmentation jitter | No — pure, synthetic-sequence tested |
| `camera/grading_model.py` | `TileGradeModel` — optional ONNX cosmetic grade-tier classifier (3A/3B/4/5) run as a *second opinion*. `from_config()` returns None when disabled/absent so the station runs without it | Partly — `preprocess()` + config handling unit-tested; the ONNX session is not |
| `camera/pipeline.py` | `process_tile()` — wires the three detectors into one `TileRecord` per tile, plus a first-pass rule-based grade and (optionally) the model's prediction alongside it. `tile_record_to_dict()` — shared JSON serialization, used by both the dashboard and `process_video.py` | No — pure glue, `tile_record_to_dict()` + the model integration unit-tested |
| `camera/snapshot.py` | `save_tile_snapshot()` — writes a departed tile's photo to `data/camera_captures/` (config: `capture_snapshots`), named by its sequence number | Yes — I/O only, not unit-tested |
| `camera/capture.py` | `WebcamCapture` (USB/UVC via OpenCV), `PiCameraCapture` (CSI via Picamera2), `VideoFileCapture` (saved file), and `create_capture()` / `list_cameras()` | Yes — not unit-tested, and `PiCameraCapture` not even smoke-tested |
| `camera/worker.py` | `CameraWorker` — background thread: capture → segment → track → (on the configured trigger) run the pipeline + save a snapshot, publish into `SharedState`. See **Capture trigger** below for which frame gets graded | Yes — real hardware I/O + threading, not unit-tested |
| `camera/process_video.py` | `process_video_file()` + CLI — offline version of `worker.py`'s loop against a saved video file: total tile flow count and a numbered per-tile result, with an optional `--report file.json` | Yes — reads a file; not unit-tested, smoke-tested against a synthetic video |
| `camera/dashboard.py` + `camera/templates/dashboard.html` | Flask app: `/video_feed` (MJPEG stream), `/api/status` (JSON: tile count + recent results), `/` (dashboard page) | Serves over the network (`host: 0.0.0.0`), smoke-tested via Flask's test client |
| `camera/live_dashboard.py` | CLI entry point (`--list-devices`, `--backend`, `--no-dashboard`), mirrors `acoustic_node`'s `live_monitor.py` | — |
| `main.py` | systemd service entry point — thin shim over `live_dashboard.main()` so the service and the CLI can't drift apart | — |

**Why classical CV instead of a trained model:** an earlier attempt using a pretrained
crack-classification example (from Arduino's own App Lab examples) only produced a grade
category, not a measurement — see `.claude/CLAUDE-LOG.md`, 2026-08-07. This pipeline
measures crack length and missing-corner area directly from contours instead, which is
what this project actually needs ("is there a crack, and how big is it").

A trained classifier is now *optionally* wired in as well (`camera/grading_model.py`,
2026-09-20), but it does not replace any of the above — see **Capture trigger** and
**Optional ONNX grade model** below.

## Capture trigger

`config.yaml`'s `processing_trigger` decides **which frame of a tile gets graded**. The
two settings grade different frames, so the same footage gives different measurements
under each — that is expected, not a bug.

| Setting | Fires when | Use it when |
|---|---|---|
| `"line_crossing"` (default) | the tile's bounding-box centre crosses `trigger_line` | The real conveyor. Every tile is photographed at the same point in the frame, so scale, framing and motion blur are comparable between tiles. |
| `"departure"` | the tile has fully left the frame; grades the largest-area sighting seen while it was crossing | Footage where the camera's mounting isn't fixed yet, since it needs no line to be positioned. |

`trigger_line` is configured in **normalized** coordinates (0.0–1.0 of the frame), so one
value stays correct at any camera resolution. `direction` should match the conveyor's
actual travel so a tile nudged backwards isn't graded twice, and `hysteresis` is a
deadband: without it, a tile sitting on the line jitters a pixel either side between
frames from segmentation noise and fires repeatedly.

The tile *count* follows the trigger — crossings in `"line_crossing"` mode, `TileTracker`
departures in `"departure"` mode — while `TileTracker` runs in both, since it owns the
presence debounce either way.

## Optional ONNX grade model

`camera/grading_model.py` runs the model staged into `camera_node/models/` by
`camera_models/cam_edge/stage_model.py`. **Off by default** (`grading_model.enabled:
false`) and imported lazily, so a station without `onnxruntime` installed runs exactly as
before rather than failing to start.

**It is a second opinion, not a replacement.** It classifies the *cosmetic grade tier*
(3A/3B/4/5) of an intact tile and **cannot see cracks or broken corners at all** — those
stay `crack_detection.py` and `corner_detection.py`'s job. The station's decision remains
the rule-based `grade`; the model's answer is recorded next to it as `model_grade` so the
two can be compared on real tiles instead of one silently overriding the other.

Currently staged: `yolo26n-cls`, 6.2 MB, measured **82.9%** on a 76-image val split (95%
CI 72.9–89.7, i.e. roughly one tile in six is misgraded — do not treat a single
prediction as authoritative). Re-verified end-to-end through this module on 2026-09-20:
82.89%, 100% agreement with `cam_edge`'s own runtime, and a byte-identical preprocessing
tensor. Per-tile inference measured 2.5 ms (p50) on the dev laptop at 2 threads.

Classes, preprocessing recipe and provenance are read from the sidecar `models/*.json`
rather than re-typed into `config.yaml`, so they can't drift from the weights.

> The preprocessing in this module is **deliberately copied**, not imported, from
> `camera_models/cam_edge/runtime.py` — `camera_node` must not import from
> `camera_models/` (dev-only tooling, gitignored `runs/`). If you change one, change both
> and re-run `camera_models/cam_edge/evaluate.py` to prove the accuracy still holds.
> Note that the resize filter alone is worth ~8 accuracy points: Pillow's antialiased
> BILINEAR, not `cv2.INTER_LINEAR`.

## Running it on the dev laptop

```bash
cd camera_node/python
python -m camera.live_dashboard
```

Then open `http://<this machine's IP>:5000/` from any browser on the same network (or
`http://localhost:5000/` locally) — live video feed, running tile count, and a table of
recent tiles with crack severity/length and corner-broken/missing-area. Each departed
tile's photo is saved to `data/camera_captures/` (gitignored) as
`tile_<seq>_<timestamp>.jpg`, unless `capture_snapshots.enabled: false`. Ctrl+C to stop.

Tests run from the repo root as always: `python -m pytest tests/ -v`.

## Running it on the Pi station

See **`deploy/README.md`** for the full procedure. Short version:

```bash
./camera_node/deploy/install_pi.sh        # apt deps + systemd unit
cd camera_node/python
/usr/bin/python3 -m camera.live_dashboard --list-devices
sudo systemctl start tile-camera
journalctl -u tile-camera -f
```

Because it's the Desktop image, the Pi can also display its own dashboard on an attached
monitor at `http://localhost:5000/` — `deploy/README.md` has a Chromium kiosk autostart
snippet.

## Processing a recorded video instead of a live camera

```bash
cd camera_node/python
python -m camera.process_video path/to/footage.mp4
python -m camera.process_video path/to/footage.mp4 --report report.json
```

Runs the same segmentation/tracker/pipeline as the live dashboard against a saved video
file, then prints the total tile flow count for that clip plus a numbered, graded row per
tile. `--report` also writes the full per-tile results as JSON. This is the offline
counterpart to the live dashboard's `tile_count` — same local, per-run count, not the
conveyor's authoritative encoder-based tracking (see Known Limitations below).

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
  re-derive both once the Pi's camera mounting position is fixed.
- **The Pi camera path is unverified end to end.** `PiCameraCapture`, `deploy/`, the
  systemd unit, and every claim about libcamera's channel order were written without
  hardware present. Expect to debug them.
- **Exposure/white-balance locking is not done.** Until `camera.picamera2.controls` is
  set for the real enclosure lighting, the calibrated HSV range can drift tile to tile —
  a failure mode the laptop webcam pointed at a desk never showed.
- **No load test on Pi hardware.** `target_fps: 15` with per-frame segmentation and JPEG
  re-encoding has never been measured on a Pi 5, with or without a kiosk browser also
  consuming the MJPEG stream.
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
  the full reasoning. Still does not localize *which* corner is broken, and neither check
  has been validated against a real broken-corner photo yet (only one real crack photo has
  turned up so far — see `development/README.md`).
- **Tile counting here is a local, camera-frame-based count** for this station's own
  dashboard (the camera station owns the running tile count per `project_charter.md`
  §6.1). It is not the conveyor's authoritative encoder-based tracking
  (`Automation_Architecture.md` §8), which is a separate mechanism for correlating a
  result to a specific physical tile across all three inspection stations.
- The Flask dashboard streaming full video frames over the network is an intentional
  exception to `Automation_Architecture.md` §4's "send only compact results to the
  master" principle — that principle is about the station→master grading pipeline over
  MQTT, not this station-local live-monitor view (`project_charter.md` §14, FR-19).
