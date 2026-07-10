# TODO

## In Progress
- [ ] Review 0 (VIT Zeroth Review Form, hardcopy) — content drafted (`documents/deliverables/review_0/Review_0_Form.md`), still needs guide + HoD signature on the physical hardcopy, submission on/before 2026-07-13, then update guide + project title in VTOP. See `documents/deliverables/review_0/README.md`.

## Done
- [x] Set up venv + requirements.txt (sounddevice, numpy, scipy, matplotlib, pyyaml, pytest)
- [x] Build acoustic capture module: RMS-trigger detection, pre-trigger buffer, FFT + waveform plotting, CLI (`acoustic/`)
- [x] Unit tests for signal processing and trigger logic (`tests/`) — 11 passing
- [x] Fill in root `CLAUDE.md` (`.CLAUDE/CLAUDE.md`) for the acoustic module's current state
- [x] Restructure docs: moved `project/` → `documents/project/`, added `documents/{mech,electrical,programming,requirements}/` and `documents/deliverables/` (college coursework), drafted `documents/requirements/requirements.md`, updated `.CLAUDE/CLAUDE.md` accordingly (2026-07-10)

## Not Started
- [ ] Run `python -m acoustic.live_monitor --calibrate` against the real built-in mic and set a real `trigger.rms_threshold` in `acoustic/config.yaml` (currently a placeholder value)
- [ ] Confirm Windows Microphone Enhancements (AGC/noise suppression) are disabled before trusting any calibration numbers
- [ ] Full end-to-end smoke test: `python -m acoustic.live_monitor` triggering on a real tap/clap and showing the plot
- [ ] Add WAV + spectrum saving to disk once ready to start building a labeled dataset (deferred by explicit decision — live-display-only for now)
- [ ] Camera / visual inspection module (charter §6.1) — not started. Now scoped as the first station in the line: also owns tile presence detection/announcement to master, running tile count, and a secondary belt-speed estimate (encoder remains authoritative — see requirements.md FR-16/FR-17)
- [ ] Decide whether the camera station also serves as the encoder-zero entry sensor, or a separate dedicated entry sensor still exists ahead of it (open question, charter §6.1)
- [ ] Design the gantry's custom machine-control layer (high-level place command → low-level axis motion translation) — not started; explicitly not using an off-the-shelf CNC/G-code controller (requirements.md FR-18, charter §7.4/§8.2)
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
- [ ] Add a sustainability-goals treatment somewhere in the project docs — Review 1 rubric (`documents/deliverables/review_1/README.md`) requires it and nothing currently addresses it
- [ ] Start the daily logbook (`documents/deliverables/logbook/`) — required by the college guidelines, signed regularly by the guide
