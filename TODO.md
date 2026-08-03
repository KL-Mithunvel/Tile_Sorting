# TODO

## In Progress
- [ ] Review 0 (VIT Zeroth Review Form, hardcopy) — content drafted (`documents/deliverables/review_0/Review_0_Form.md`), still needs guide + HoD signature on the physical hardcopy, submission on/before 2026-07-13, then update guide + project title in VTOP. See `documents/deliverables/review_0/README.md`.
- [ ] Review 1 (Expert Panel Review, 2026-08-19 tentative) — narrative content drafted per rubric category (`documents/deliverables/review_1/Review_1_Content.md`, 2026-08-03). Still missing: literature survey (none written), sustainability goals treatment, and Phase 1 results/analysis (lab work not done yet) — see that file's open questions. Needs guide approval well in advance per college guidelines.

## Done
- [x] Set up venv + requirements.txt (sounddevice, numpy, scipy, matplotlib, pyyaml, pytest)
- [x] Build acoustic capture module: RMS-trigger detection, pre-trigger buffer, FFT + waveform plotting, CLI (`acoustic/`)
- [x] Unit tests for signal processing and trigger logic (`tests/`) — 11 passing
- [x] Fill in root `CLAUDE.md` (`.CLAUDE/CLAUDE.md`) for the acoustic module's current state
- [x] Restructure docs: moved `project/` → `documents/project/`, added `documents/{mech,electrical,programming,requirements}/` and `documents/deliverables/` (college coursework), drafted `documents/requirements/requirements.md`, updated `.CLAUDE/CLAUDE.md` accordingly (2026-07-10)
- [x] Set up SSH/VNC dev tooling for the lab Arduino UNO Q board (`tools/uno_q/`) — push code, terminal, and a fixed-resolution VNC desktop over an SSH tunnel; fixed a stale-session bug that was making TigerVNC ignore `-geometry` (2026-07-28)
- [x] Adopted the Arduino App Bricks folder convention (`app.yaml` + `sketch/` + `python/`) for every UNO Q station; migrated the working `acoustic/` package into `acoustic_node/python/acoustic/` unchanged (verified via `pytest.ini`'s `pythonpath` — all 11 tests still pass, CLI re-verified with `--list-devices`); scaffolded `camera_node/` and `pick_place_node/` as stubs; decided pick-and-place controller hardware = Arduino UNO Q (was undecided); updated `tools/uno_q/push.bat`/README, `Automation_Architecture.md` §5.6/§5.7, `requirements.md` FR-10a/FR-22 accordingly (2026-08-03)

## Not Started
- [ ] Run `python -m acoustic.live_monitor --calibrate` against the real built-in mic and set a real `trigger.rms_threshold` in `acoustic/config.yaml` (currently a placeholder value)
- [ ] Confirm Windows Microphone Enhancements (AGC/noise suppression) are disabled before trusting any calibration numbers
- [ ] Full end-to-end smoke test: `python -m acoustic.live_monitor` triggering on a real tap/clap and showing the plot
- [ ] Add WAV + spectrum saving to disk once ready to start building a labeled dataset (deferred by explicit decision — live-display-only for now)
- [ ] Camera / visual inspection module (charter §6.1) — no real code yet; `camera_node/` folder scaffolded (App Bricks shape: `app.yaml`, `sketch/`, `python/main.py` stubs, 2026-08-03) but empty. Scoped as the first station in the line: also owns tile presence detection/announcement to master, running tile count, and a secondary belt-speed estimate (encoder remains authoritative — see requirements.md FR-16/FR-17)
- [ ] Decide whether the camera station also serves as the encoder-zero entry sensor, or a separate dedicated entry sensor still exists ahead of it (open question, charter §6.1)
- [ ] Design the gantry's custom machine-control layer (high-level place command → low-level axis motion translation) — no real code yet; `pick_place_node/` folder scaffolded (App Bricks shape, 2026-08-03) but empty. Explicitly not using an off-the-shelf CNC/G-code controller (requirements.md FR-18, charter §7.4/§8.2)
- [ ] Decide pick-and-place axis count, motor/gripper hardware, and gantry travel limits — controller board is now decided (Arduino UNO Q, `Automation_Architecture.md` §5.6, 2026-08-03), but the mechanical/motion specifics are not
- [ ] Decide ball-drop release mechanism (electromagnet vs solenoid gate vs servo latch), ball mass/drop height, and reload/reset method for the acoustic station (charter §6.2 Decision, requirements.md FR-21)
- [ ] Reconcile charter sections that still describe a solenoid directly striking the tile (§17 Safety, §19 Prototype Build Plan, §20 BOM, §25 Conclusion) with the ball-drop decision, once the release mechanism is fixed
- [ ] Decide whether camera or acoustic node (or neither) moves from Arduino UNO Q to Raspberry Pi (Automation_Architecture.md §5.2/§5.3)
- [ ] Build the acoustic station's laser/ToF trigger → UNO Q ball-drop release logic (requirements.md FR-21) — `acoustic_node/sketch/sketch.ino` scaffolded as an empty stub (2026-08-03); real trigger/release logic not written (release mechanism, ball mass/drop height, pin assignments all still undecided)
- [ ] Verify the Arduino App Bricks/App Lab entry-point pattern (`arduino.app_utils.App.run()` in each node's `python/main.py`) actually works against the lab UNO Q board — currently an unverified stub copied from Arduino's own examples in all three node folders (requirements.md FR-22)
- [ ] Design per-station local monitor displays (camera/acoustic/dimensional) plus the master overall dashboard (charter §14 Monitoring Architecture, requirements.md FR-19); structure logged data (grade counts, defect breakdown) for real industrial reuse, not just prototype debugging (FR-20)
- [ ] Dimensional inspection module (charter §6.3) — not started; also needs a decision on camera-only measurement vs dedicated ToF/laser hardware (see `documents/requirements/requirements.md` FR-06)
- [ ] Solenoid tapper + real acoustic station hardware integration (charter §7.3) — not started
- [ ] Control layer / conveyor / sorting (charter §7.1, §7.4, §8) — not started; sorting/handling mechanism is now decided as a Cartesian gantry pick-and-place (charter §7.4 Decision), not just one option among several
- [ ] Database logging + dashboard (charter §9.4, §14) — not started
- [ ] Define/implement encoder-based tile tracking (`documents/project/Automation_Architecture.md` §8) — architecturally decided, not yet built or tested against a live conveyor
- [ ] Write `documents/project/hardware.md` — consolidated hardware architecture doc (sensors, actuators, wiring, BOM) across all subsystems. `documents/project/Acoustic_Sorting_Subsystem.md` and `documents/project/Camera_Sorting_Subsystem.md` already cover per-subsystem hardware in detail — check whether this should be a cross-subsystem summary/index over those rather than a new write-up from scratch
- [ ] Write `documents/project/software.md` — software architecture doc (module boundaries, interfaces, threading model) once more than the acoustic module exists
- [ ] Write `documents/project/communication.md` — inter-module/inter-device communication doc (Pi↔master PC, MQTT/Modbus/OPC UA choices). `documents/project/Automation_Architecture.md` §2 already sketches the master/node network topology (MQTT, per-station nodes) — this doc should likely expand on that rather than duplicate it
- [ ] Write `documents/project/control-flow.md` — machine operation sequence / state machine doc (charter §16, and the per-station sequences already detailed in `documents/project/Acoustic_Sorting_Subsystem.md` §2.1) once the conveyor/control layer is designed
- [ ] Populate `documents/mech/`, `documents/electrical/`, `documents/programming/` — currently scaffolding only (README + `.gitkeep`), no real CAD/electrical/flowchart content yet
- [ ] Get tile size/weight range from SMTW's actual product line — blocks conveyor width, gantry travel, gripper sizing, dimensional tolerances, and ball-drop energy calibration (see `documents/requirements/requirements.md` Open Items)
- [ ] Turn charter §20's generic example BOM into a real costed procurement list now that camera/acoustic = UNO Q, conveyor = Mega, master = user's PC are confirmed (`documents/project/project_charter.md` §20; pick-and-place board still open)
- [ ] Add a sustainability-goals treatment somewhere in the project docs — Review 1 rubric (`documents/deliverables/review_1/README.md`) requires it and nothing currently addresses it
- [ ] Get the guide's actual signature on the daily logbook entries — digital draft back-filled through 2026-08-03 (`documents/deliverables/logbook/Logbook.md`), still needs the physical/signed copy per the college guidelines
