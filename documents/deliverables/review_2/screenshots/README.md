# Review 2 - GUI screenshots

Taken 2026-10-16 with **no hardware attached**. Every GUI ran in its demo / simulated mode or on old
recorded data, so **all numbers, tiles and faults on these pictures are synthetic or from earlier laptop
runs - none is a measurement from the real line.** Say so in the report where one is used.

## What each picture is

### Master PC (`master_node`, :8000, `--demo`, synthetic line)
| File | Shows |
|---|---|
| `master_plant_3d` | Plant 3D view: stations list, line control (Start/Stop line, feed, hold-all, sound vote), live final-grade tally. A station is selected. |
| `master_plant_view_camera` / `_acoustic` / `_gantry` / `_bins` / `_master` | The six camera-angle presets of the 3D plant |
| `master_open_guis_menu` | The "Open GUIs" menu linking to every other GUI |
| `master_analysis` | Throughput, reject rate, grade mix, bins, camera-vs-sound agreement, camera-to-final time |
| `master_troubleshoot` | Attention list, per-station uptime timeline, recent state changes and alarms (demo injects camera faults) |
| `master_control_panel_nodes` | Control panel (Nodes page): start/stop services per node (simulated SSH backend) |
| `master_insights_ai_error` | AI insights page after pressing *Explain these*: the local Ollama is running but the model is not installed, so the **real error path** is shown (HTTP 404 model not found) |
| `master_pi_record_tiles` / `_tile_detail` / `_integrity` | Pi record: 901 tiles from an old synced record (copy), tile timeline, and the "check the copy is complete" result |
| `master_classic` | The original single-page view |

### Pi station (`acoustic.live_station --demo`, :5001)
`station_run` (operator kiosk: last tile, tally, recent tiles) and `station_service` (service/health view).

### Camera node (`camera.live_dashboard --video ... --loop`, :5000)
`camera_dashboard` - replaying a real belt recording (`data/videos/WIN_20261011_12_16_03_Pro.mp4`): live
overlay with the trigger line, last captured tile with crack/corner/model/grade stages, recent tiles table.
Grades are the **unvalidated** rule-based ones (nobody has checked them by eye).

### Pick-and-place digital twin (`twin_prototype --backend sim --demo`, :5050)
`pickplace_twin_idle` (needs homing), `pickplace_twin_running_jobs` (real job runner + protocol against the
simulated MCU, placing demo tiles), `pickplace_twin_top_view`.

### Acoustic-Analysis desktop app (synthetic clips, no microphone)
`acoustic_analysis_01..07_*` - the guided workflow (Collect setup / listen / review, Library, Analyze, Export,
Settings). `acoustic_analysis_screen_*` - the remaining screens (Home, Learn, Slice, Compare, Filters, Noise,
Calibrate, Monitor, Record, Label).

### Image annotator and labelling sheet
`annotator_1..4_*` - the Tkinter tile annotator on a **copy** of the 92-tile belt dataset (tile, context view,
pipeline readings, zoom). `review_sheet_labelling_html` - the borderline-tile review sheet.

## Not captured
Tile parameter tuner (`development/tile_param_tuner.py`), the matplotlib desktop twin, anything on the Pi, the
UNO Q or the Nano (no hardware), the real microphone/striker flow.

## Regenerate
Start the GUIs (from the repo root, venv active), then run the capture tools:
```
cd acoustic_node/python && python -m acoustic.live_station --demo
cd master_node/python   && python -m master.live_master --demo --ai ollama     # add --pi-url for the Pi record
cd camera_node/python   && python -m camera.live_dashboard --video <abs path to video> --loop
cd pick_place_node/python && python -m pick_place.twin_prototype --backend sim --demo
python development/capture_screens.py            # web GUIs; list in development/screen_list.py
Acoustic-Analysis/venv/Scripts/python development/capture_acoustic_analysis.py
python development/capture_annotator.py <COPY of a tile dataset folder>
```
The Pi-record pages need a master with a mirror attached (the plain `--demo` master deliberately has none so
fake tiles can never reach the real record); these shots used a scratch copy of `data/master/pi_mirror.db`.
