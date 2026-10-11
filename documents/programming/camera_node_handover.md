# Camera node — handover (state at 2026-10-11)

Read this to pick the camera/vision work up cold. The long-form record is
`.CLAUDE/CLAUDE.md` (Key Modules, Known Technical Debt) and `.CLAUDE/CLAUDE-LOG.md`; the
task list is `TODO.md`. This page is the short version: where it stands, how to run it,
what the data is, what was measured, what is broken, and what to do next.

## 1. Where it stands

The camera station's pipeline now works end to end **on a laptop**, on a real webcam and on
five real belt videos that include **real damaged tiles**. It has never run on the Raspberry
Pi, and none of its quality checks (crack, corner, model) has been validated against
human-checked labels — there are none yet.

| Works, and was checked | Not working / not validated |
|---|---|
| Tiles found only on the belt (belt ROI, shape gate) — wood table, a sandal, a person no longer count | **Crack check**: flags ~1/3 of intact photos; reads the embossed *back* of a tile as cracks |
| Each tile keeps its identity; each crosses the line once (92 tiles / 5 videos, all forward, none repeated) | **Corner check**: misses visibly chipped tiles (needs ~5 in of chip to fire at 0.83/0.22) |
| Trigger line is automatic: perpendicular to the belt, at the frame middle, any camera orientation | **Grade model** (ONNX): ~30 % confidence on this camera, so it never counts |
| 1080p @ 30 fps from the real webcam; pipeline 24 ms/frame (41 fps) on the laptop | **Pi 5 path**: `PiCameraCapture`, `install_pi.sh`, systemd unit — never run |
| Dashboard: source switching (camera / recording / file), record, replay, frame rates, one A/B/C/Reject grade | **Sharpest-frame pick** is on but moves crack flags (8→16 major) because the crack check is uncalibrated |
| Offline tools: review sheet, threshold tuning, corner trade-off, labelled dataset export | **No human labels** exist for any belt tile yet |

## 2. How to run things (Windows, venv is `.venv`)

```powershell
.venv\Scripts\activate
cd camera_node\python
python -m camera.live_dashboard                       # live camera (config.yaml)
python -m camera.live_dashboard --device 1            # a specific USB camera
python -m camera.live_dashboard --video data/videos/<file>.mp4 --loop
python -m camera.live_dashboard --list-devices
cd ..\..
python -m pytest tests/ -q                            # 462 tests; if it dies silently set OPENBLAS_NUM_THREADS=1
python development\camera_probe.py                    # what THIS camera really does (turns it on ~1 min)
python development\build_review_sheet.py              # ~10 min -> data\review\review_sheet_*.html (label tiles by eye)
python development\tune_from_labels.py <export.json>  # threshold advice from your labels (prints only)
python development\export_tile_dataset.py             # ~7 min -> data\tile_dataset\<date_time>\ (pictures + labels)
python development\corner_sensitivity.py              # the corner threshold trade-off in numbers
```

Pi: `camera_node/deploy/install_pi.sh` then `sudo systemctl start tile-camera` — **untested**.

## 3. Data (all under the gitignored `data/`)

| Path | What | Made by |
|---|---|---|
| `data/videos/` | 5 videos: the phone clip (576x1024, green belt) + four 1920x1080 laptop-webcam recordings (teal belt, include broken tiles/shards) | you |
| `data/recordings/<date_time>/` | dashboard "Record" output: raw `video.mp4` + `meta.json` (measured fps) | the dashboard |
| `data/review/review_sheet_*.html` | 46 borderline tiles to label (fine / chipped / cracked / both / unsure) | `build_review_sheet.py` |
| `data/tile_dataset/2026-10-11_17-01-07/` | **92 graded tiles**: `tiles/` (exact crop, PNG), `context/` (with belt), `labels.csv/.json`, `manifest.json`, `config_used.yaml`. Labels = pipeline readings + empty `human_label` | `export_tile_dataset.py` |
| `data/camera_captures/` | per-tile snapshots from live runs | the worker |
| `data/9x9-*` etc. | ~380 intact-tile photos on a checkerboard (the original calibration set) | earlier |

Tile ids (`WIN_20261011_12_16_03_t006`) are stable across runs, so human labels can be merged
into any export: `export_tile_dataset.py --merge-labels <export.json> --into <dataset folder>`.

## 4. Things that were measured (not assumed)

- **Blur = belt speed x exposure.** Tiles move 14.5 px/frame = 435 px/s (1080-px frame, 30 fps).
  The webcam's auto exposure is 1/64 s -> ~7 px smear (a hairline crack is 1-3 px). Manual 1/128 s gave a
  nearly black image (brightness 26/255) in room light, and the driver **rejects gain changes**.
  Fix = ~8x more light on the belt (1/256 s -> ~2 px), not software.
- Webcam delivers ~30 fps at 640x480, 1280x720 and 1920x1080 alike. Old config (640x480 @ 15) wasted it.
- Per-frame pipeline at 1080p: belt ROI ~13 ms + segmentation ~8 ms; grading a tile 1.7 ms; total
  24 ms/frame. Offline 16-22 fps was H.264 *decoding*. Pi will be several times slower.
- Sharpest-frame pick: graded frame 1.30x sharper (median), sharper for 91 % of tiles, blurrier for 0 %;
  same 92 tiles counted; graded position median 38 px from the line (max 64).
- Corner threshold trade (378 intact photos): 0.83/0.22 rejects 1.9 % of good tiles and catches chips >= 5.0 in;
  0.88/0.15 -> 3.7 % / 4.0 in; 0.92/0.10 -> 5.6 % / 2.7 in.
- Belt colour: green (hue 60-75) in the phone clip, **teal (86-93)** in the webcam recordings -> belt hue range 45-95.
  Tile hue floor 6 -> 3 (a red colour cast cost an intact tile its grade).

## 5. Next steps, in order

1. **Owner:** label the review sheet (~10 min), export, run `tune_from_labels.py` and merge labels into the dataset.
2. Recalibrate the **crack check** from labels (+ decide how to treat the embossed back face); set the **corner**
   operating point; then decide whether `trigger_line.sharpest_band` stays on.
3. **Labelling mode** on the dashboard (press 1-5 per tile as it passes) to collect ground truth quickly.
4. **Regression check**: re-run `data/videos/` and diff per-tile grades against a saved dataset baseline.
5. **Dimensional check** from the camera (width/height/squareness via the outline + checkerboard px/mm) — FR-06.
6. Live **blur estimate** on the dashboard; brighten the belt.
7. Retrain / re-measure the grade model on station-camera tiles once tier labels exist.
8. **Pi 5**: image it, run `install_pi.sh`, measure fps at 1080p (consider a downscaled belt-ROI search), use a CSI
   camera with real exposure/gain control, lock exposure once lighting is fixed.
9. Beyond the camera: closed-loop belt speed, sending each tile's result to the master, database logging.

## 6. Decisions waiting on the owner

- Which corner threshold pair to ship (a chipped tile shipped vs good tiles rejected).
- Keep `sharpest_band: 0.06` on (sharper picture, ~4 extra false Rejects/92 until cracks are recalibrated) or off.
- Dashboard control exposure: `dashboard.controls_enabled` is **true** and the dashboard listens on 0.0.0.0, so anyone on
  the WiFi can switch source or record; set false on a shared station.

## 7. Gotchas for the next session

- In this repo's Bash tool, a Python heredoc containing `\n`, `\b`, `\t`, `\v` **mangles paths and f-strings**
  (an earlier doc edit wrote control characters into `development/README.md`; fixed). Use the file-writing tool for
  anything with backslashes, and scan with a control-character check before committing docs.
- Never probe a camera that is streaming (Windows: the second open kills the live capture). Scans already skip it.
- Starting the camera turns its light on; test recordings contain the room — delete them (`data/recordings/`).
- `.venv` is the venv here (older notes say `venv`); `python` may not be on the Bash PATH — call `.venv/Scripts/python.exe`.
- Sibling test modules can't be imported from tests; each test file carries its own small helpers.
- The dashboard must be stopped when finished (port 5000); background launches show a harmless "failed" notice when stopped.
