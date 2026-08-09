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
  debounced tile-crossing counter, and a Flask dashboard that streams the live camera
  feed + results over WiFi. Covered by `tests/test_camera_*.py` (15 tests, synthetic
  images — see `pytest.ini`, which adds `camera_node/python` to `sys.path`).
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
| `camera/pipeline.py` | `process_tile()` — wires the three detectors above into one `TileRecord` per tile, plus a first-pass rule-based grade | No — pure glue |
| `camera/capture.py` | `WebcamCapture` — thin OpenCV `VideoCapture` wrapper | Yes — not unit-tested, smoke-tested only |
| `camera/worker.py` | `CameraWorker` — background thread: capture → segment → track → (on tile departure) run the pipeline, publish into `SharedState` | Yes — real hardware I/O + threading, not unit-tested |
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
Ctrl+C to stop. Tests run from the repo root as always: `python -m pytest tests/ -v`.

## Known limitations — read before trusting any result

- **Every threshold in `config.yaml` is a placeholder**, not calibrated against real
  brown terracotta tile photos — none were available on this machine when this was
  built (see `.CLAUDE/CLAUDE.md` Known Technical Debt). Re-tune `segmentation.hsv_lower`
  /`hsv_upper` especially — that range was guessed, not sampled from a real tile.
- **Crack "type" here means severity (minor/major) + length**, not a fine-grained
  taxonomy (hairline vs. structural, edge vs. center). Distinguishing crack types more
  precisely needs real labeled photos to design against.
- **Corner detection reports *that* a corner is broken and *how much* area is missing,
  not *which* corner.** Localizing the specific corner was deliberately left out until
  there are real broken-corner photos to validate against — see the docstring in
  `corner_detection.py`.
- **Tile counting here is a local, camera-frame-based count** for this station's own
  dashboard (the camera station owns the running tile count per `project_charter.md`
  §6.1). It is not the conveyor's authoritative encoder-based tracking
  (`Automation_Architecture.md` §8), which is a separate mechanism for correlating a
  result to a specific physical tile across all three inspection stations.
- The Flask dashboard streaming full video frames over the network is an intentional
  exception to `Automation_Architecture.md` §4's "send only compact results to the
  master" principle — that principle is about the station→master grading pipeline over
  MQTT, not this station-local live-monitor view (`project_charter.md` §14, FR-19).
