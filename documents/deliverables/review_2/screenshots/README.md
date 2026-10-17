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

## AI insights, one fault at a time (added 2026-10-17, same synthetic rules)
Seven master GUIs were started side by side (ports 8101-8107), each fed an hour of **invented** tiles that behave
normally for the first three quarters and then change in exactly one way. On each page the rule-based check flagged
only that change, and *Explain these* was pressed so the real local model (`llama3.2:3b`, Ollama, on the laptop GPU)
answered. Every page has the same layout: top = the **rule-based finding** (always on, no AI); middle = checks on the
Pi's stored record (empty here: nothing synced); bottom = the **model's explanation** with its response time and the
warning "AI-generated ... can be wrong". The coloured badge is the severity (WARNING / NOTE).

| File | The one fault planted | What the rule-based finding says | What the model answered (and how good it is) |
|---|---|---|---|
| `insights_fault_1_reject_rate` | Share of tiles rejected rises from about 5 % to 40 % | "Reject rate has risen": 22 % lately (10 of 45) against 7 % before (9 of 135) | Restates the numbers, suggests checking the camera and the pick-and-place gantry. The causes it names are guesses. 21 s |
| `insights_fault_2_hold_rate` | 35 % of recent tiles end as "Hold" (need a person) | "More tiles are being held for a person": 33 % (15 of 45) against 0 % | Explains what a hold is, tells the operator to check the belt and gantry for blockages. Generic. 17 s |
| `insights_fault_3_no_tap_timeouts` | 25 % of recent tiles never get a tap result and time out | "More tiles are timing out without a tap": 27 % (12 of 45) against 0 % | Says the data is thin; blames the acoustic station or damaged tiles. It words the fault as tiles "not responding to the tap", which is not quite what a timeout means (no tap result arrived). 14 s |
| `insights_fault_4_camera_sound_disagree` | The sound result disagrees with the camera on most tiles | "Camera and sound disagree more than before": match 29 % (13 of 45) against 89 % (120 of 135) | Repeats the finding, suggests cleaning/checking the sensors and calibration, says the sample is small. 12 s |
| `insights_fault_5_slow_transit` | Time from camera to final grade rises from 2.6 s to 6.0 s | "Tiles take longer from camera to final grade": median 6.0 s against 2.6 s (2.3x) | Suggests the belt slowed or the taps are late (this wording comes from the rule-based text itself) and says to check belt speed and tap timing. 14 s |
| `insights_fault_6_low_throughput` | Tiles arrive less often: 1.3/min instead of 3.0/min | "Fewer tiles are coming through" (a NOTE, not a warning): 1.3/min against 3.0/min | Fine if the belt was stopped on purpose, otherwise check the camera view and the belt. It states the same finding twice. 16 s |
| `insights_fault_7_alarm_burst` | The same alarm (`camera_silent`) is raised six times in 15 minutes | "Alarm 'camera_silent' keeps repeating": 6 alarms in 15 min | Explains the alarm and suggests a dirty sensor, which is a guess (a "silent" camera is more likely a stopped feed). 14 s |

What these pictures prove and do not prove: the rule-based detector flags each kind of change by itself on
synthetic data; the model puts it in plain words in 12-21 s once loaded (56 s the first time, while it loads). They
do **not** show anything about a real line. The model often guesses a cause the data does not contain, which is why
the page labels every answer as AI-generated.

Regenerate (from the repo root, venv active; Ollama running with `llama3.2:3b`): start one master per fault, then capture.
```
python development/launch_one_fault.py reject_rate 8101     # hold_rate 8102, timeouts 8103, agreement 8104,
                                                            # transit 8105, throughput 8106, alarm_burst 8107
python development/capture_screens.py insights_fault
```

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
