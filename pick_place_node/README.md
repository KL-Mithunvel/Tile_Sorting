# Pick-and-Place Node

Code for the Cartesian gantry's machine-control layer (`project_charter.md` §7.4
Decision, `Automation_Architecture.md` §5.6, `documents/requirements/requirements.md`
FR-18). **Newly decided this session: target hardware is an Arduino UNO Q** — previously
"hardware not yet fixed" in the project docs (now updated, see
`Automation_Architecture.md` §5.6).

Folder layout follows the Arduino App Bricks convention (see `app.yaml`), matching
`acoustic_node/` — see that node's `README.md` for the general pattern and its caveats
about App Lab not yet being verified on real hardware.

## Status (2026-10-12)

**Built and tested on the laptop (no hardware):**

| Module (`python/pick_place/`) | Role |
|---|---|
| `protocol.py` | Encode commands, parse MCU lines into typed events (incl. closed-loop `pos` encoder fields and `LOST_STEPS`/`SKEW_Y` alarms) |
| `coordinate_model.py` | Named pickup + 4 stacks -> XYZ, stack-height bookkeeping, config safety validation |
| `sequencer.py` | `{source, destination}` -> ordered move list (safe Z first), retry unit, park |
| `tile_feed.py` | Receives the Pi's finals (`destination`, optional `source`), FIFO, de-dup, ack |
| `mock_state.py`, `worker.py`, `dashboard.py`, `twin_prototype.py` | Digital-twin HMI on a mock gantry |
| `mock_job_driver.py` | Mock stand-in for the job runner: takes queued tiles, plans them with the real sequencer, drives the mock gantry, updates real stack counts |

**Run the twin with tiles flowing** (from `pick_place_node/python/`): 

```bash
python -m pick_place.twin_prototype --demo                    # synthetic tiles, no Pi
python -m pick_place.twin_prototype --station-host <PI_IP>    # the Pi's real finals
```

The page (port 5050) shows the conveyor end (pickup), the four stacks with live
counts, the current job (tile, grade, from -> to, step n/9, command, gripped or not),
the pick queue and feed state, and the tile spec. API: `/api/layout`, `/api/job`,
`/api/feed`. Jobs run only in **Jobs** mode; a jog or vacuum click switches to Manual and
pauses the job where it is. Motion is simulated; the plan, stack heights and counts are
the real ones.

**Not built:** the MCU firmware (`sketch/sketch.ino` is an empty stub — step generation,
homing, limits, e-stop, encoders, the line parser), `job_runner.py`, a serial backend
(`gantry_backend.py` is only the seam; its `serial` branch raises), and the sequencer
consumer of `FinalQueue`. `python/main.py` is still an App Lab stub.

Layout of the line: the **conveyor** delivers each tile to one fixed pickup point
(`locations.pickup`); the gantry places it on one of **four separate stacks**
(`stacks.bin_1`..`bin_4`, the destination names the Pi sends). Tile: 82.55 mm square,
12 mm, 300 g. Every position in `config.yaml` is a placeholder until taught-in.

## Bench bring-up

`bench_tests/stepper_smoke_test/` — a standalone `arduino-app-cli` app that pulses
X, Y+A(clone), and Z to confirm all 4 CNC Shield V3 stepper motors move (checklist
task 0.5, `documents/pick_place_todo.md`). Not the real firmware. Push with
`tools\uno_q\push-stepper-test.bat`; see that folder's `README.md`.

`bench_tests/io_smoke_test/` — a standalone `arduino-app-cli` app with a browser GUI
for the E-stop loop, the 3 limit switches, and the vacuum solenoid (checklist tasks
0.1/0.6). Not the real firmware. Push with `tools\uno_q\push-io-test.bat`; see that
folder's `README.md`, including a real discrepancy it found in the wiring doc's stated
LOW/HIGH trigger polarity.

## HMI / digital twin dashboard (design in progress, 2026-08-11)

Full feature sketch for this node's local HMI (control panel, status panel, live digital
twin visualization) is in `documents/project/Pick_Place_Sorting_Subsystem.md`. Current
work: building and testing the digital twin visualization standalone (mock/simulated
position data, no real `GantryWorker` yet) before any motion-control code or full node
integration.
