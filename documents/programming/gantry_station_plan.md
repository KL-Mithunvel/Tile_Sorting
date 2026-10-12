# Gantry station: MQTT reporting, remote commands, firmware - build plan

Created 2026-10-12. Baseline before starting: 768 tests passing on the laptop.
Scope: everything on the UNO Q / gantry side of the station contract that does not
exist yet, in the order it can be built and verified. Nothing here has run on the
UNO Q, the Pi, a real broker or real motors; each phase says what its test proves.

Contract additions (agreed with the owner 2026-10-12; the table in
`station_coordination.md` section 4 gets the same rows when phase 7 lands):

| Topic | Direction | QoS / retain | Payload |
|---|---|---|---|
| `tile/<id>/placed` | UNO Q -> Pi, master | 1 / no | `tile_id, station:"gantry", outcome:"placed"\|"failed", source, destination, reason, tile_in_cup, stack_count, attempt, ts` |
| `station/gantry/status` | UNO Q -> Pi, master | 0 / **retained**, last-will `{"state":"offline"}` | `state, level, phase, fault, alarm, homed, fsm, vacuum, link, paused, queue_depth, active_tile, completed, failed, stacks{}, backend, ts` |
| `machine/alarm` (existing) | UNO Q -> master | 1 / no | `ts, level:"alarm", kind:"gantry_<kind>", message, tile_id` |
| `station/gantry/cmd` | master -> UNO Q | 1 / no | `{"id":..., "cmd":"pause"\|"resume"\|"acknowledge"\|"reset_stack"\|"home"\|"recover", "stack":"bin_1"}` |
| `station/gantry/cmd/result` | UNO Q -> master | 1 / no | `id, cmd, ok, reason, ts` |

Safety split for commands: `pause / resume / acknowledge / reset_stack` never move the
machine and are on by default; `home / recover` move it, so they need
`gantry.station.motion_commands_enabled: true` (default false). The e-stop stays local.

## Phases

| # | Phase | Deliverable | Proven by |
|---|---|---|---|
| 1 | Gantry reporter | `pick_place/station_link.py`: `GantryReporter` (placed / status / alarms), last-will + retained publish in `PahoFeedBus` | `tests/test_pick_place_station_link.py` over an in-memory bus |
| 2 | Pi side consumes it | coordinator handles `placed` + gantry status (health, alarm, "never placed" timeout), store columns, contract test, fake peers | `tests/test_fusion_coordinator.py`, `test_station_contract.py` against the real reporter |
| 3 | Master sees it | collector stores placement + gantry status; plant model shows the gantry; master can send gantry commands | `tests/test_master_*.py` |
| 4 | Gantry commands | `GantryCommands` handler with gating, results published, wired to runner / backend / model | station_link tests |
| 5 | Headless service | `python -m pick_place.gantry_station` (+ twin uses the same link), `main.py`, config block | test with `--backend sim` + in-memory bus end to end |
| 6 | Firmware core | portable C++ `gantry_core` (parser, FSM, trapezoid planner, homing, limits, closed loop, vacuum) behind a HAL, built on the laptop and run against the SAME scenarios as `SimulatedMcu` | conformance tests (needs a host C++ compiler - see the phase notes) |
| 7 | Sketch + docs | `sketch.ino` HAL (pins, k_timer step ISR, limits, e-stop, vacuum, serial), bench README, contract docs, CLAUDE.md, TODO | not compiled for the board; reviewed against the core |

## Status (end of 2026-10-12)

| # | Phase | State |
|---|---|---|
| 1 | Gantry reporter | built, 24 tests |
| 2 | Pi side consumes it | built (coordinator, store, demo peers) |
| 3 | Master sees it | built (collector, plant, `/api/command/gantry`) |
| 4 | Gantry commands | built (in `station_link.py`) |
| 5 | Headless service | built (`gantry_station.py`, `main.py`, twin attaches the same link) |
| 6 | Firmware core | built; conformance-tested on a PC with zig's clang (51 tests; skipped without a C++ compiler) |
| 7 | Sketch + docs | sketch syntax-checked against stub headers only; docs updated |

Found on the way: a self-deadlock in the reporter with the mock driver (status must not be built inside
`on_job_done`); the runner did not PING during a motion, which the firmware watchdog would have turned
into `LINK_TIMEOUT` on every long move; closed-loop skew compared encoder readings from different
instants.

## What stays unverified after all seven

A real broker, the UNO Q, paho-mqtt in the App Lab container, the board's Serial
transport to the Python side, `k_timer` behaviour (checklist 0.6), limit-switch
polarity, vacuum-confirm sensor wiring, AS5600 reads through the mux, steps/mm.
The firmware core is verified on the laptop; the sketch around it is not.
