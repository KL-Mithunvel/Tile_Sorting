# Daily Logbook — Automated Tiles Sorting and Packing System

Per `../VIT_Project1_Guidelines.md` § 5: daily logbook of project-related activities,
signed regularly by the guide, producible on request or at any scheduled review.

**This is the digital record of activities**, back-filled from `.CLAUDE/CLAUDE-LOG.md`
(the project's technical development log, which already records what was done and why
per session). It is a drafting/reference copy, not a substitute for the physical
guide-signed logbook — use it to fill that in, and get the guide's signature there
regularly, per the college guidelines.

Team: Mithunvel KL (23BMH1029), Kanigalpula Teja Pavan Sai Adinarayana Gupta
(23BMH1007), Madhumitha Vemula (23BMH1019). Guide: Dr. Jegadeeshwaran R (50204).

---

| Date | Activities Performed | Output / Deliverable | Guide's Signature |
|---|---|---|---|
| 2026-07-08 / 2026-07-09 | Set up Python venv and dependencies (sounddevice, numpy, scipy, matplotlib, pyyaml, pytest). Built the acoustic capture module: RMS-threshold trigger detection with pre-trigger buffering, FFT/RMS/dominant-frequency signal processing, waveform+spectrum plotting, CLI (`--list-devices`, `--calibrate`, continuous monitor mode). Found and fixed two real bugs during testing (DC-bin leakage in dominant-frequency detection; a queue-timeout bug that would have killed the monitor loop after silence). | Working acoustic capture pipeline (`acoustic/`, later moved to `acoustic_node/python/acoustic/` — see 2026-08-03); 11 unit tests, all passing (`tests/`); project's `.CLAUDE/CLAUDE.md` filled in to describe the module. | |
| 2026-07-10 | Restructured project documentation: moved `project/` into `documents/project/`, added `documents/{mech,electrical,programming,requirements}/` and `documents/deliverables/` (college coursework). Drafted a formal requirements specification (FR-xx/NFR-xx) traceable to the project charter. Populated `documents/deliverables/` with the actual VIT Chennai BMEE497J/BMHA497J guidelines and per-review folders. Captured and recorded several architecture decisions: camera station is first in the inspection line and owns tile presence/count reporting; acoustic tapping mechanism changed to a laser/ToF-triggered ball-drop impactor (from a push-pull solenoid striker); sorting/handling mechanism confirmed as a Cartesian gantry pick-and-place with a custom (non-CNC) machine-control layer; two-tier monitoring architecture (per-station local display + master dashboard) defined; compute hardware assigned (master = own PC, camera/acoustic = Arduino UNO Q, conveyor = Arduino Mega). Identified an open gap: SMTW's actual tile size/weight range has never been obtained. Filled in and drafted the Review 0 form content. Committed the documentation restructuring to git. | `documents/` restructure; `documents/requirements/requirements.md`; `documents/deliverables/` (guidelines + review folders + Review 0 form draft); updated `project_charter.md` / `Automation_Architecture.md` with the decisions above; git commit `22e7ef5`. | |
| 2026-07-28 | Set up SSH/VNC development tooling for the lab Arduino UNO Q board (hostname `KLM`, reachable over a local hotspot). Built scripts to push code to the board, open a terminal, and start/stop a tunneled VNC desktop session. Diagnosed and fixed a stale-VNC-session bug that was causing TigerVNC to ignore the requested screen resolution. | `tools/uno_q/` (push/ssh/vnc scripts + README); working SSH-key and password auth to the board. | |
| 2026-08-03 | Discussed and decided a new architecture direction: every Arduino UNO Q station (camera, acoustic, pick-and-place) is organized as an Arduino App Bricks project (`app.yaml` + `sketch/` + `python/`), matching Arduino's own App Lab example convention, so each can be opened and developed as an App Lab app. Migrated the existing, tested acoustic module into this shape (`acoustic_node/`) with no logic changes — re-verified all 11 tests still pass and the CLI still runs from its new location. Scaffolded `camera_node/` and `pick_place_node/` as stubs (no real code yet). Decided the pick-and-place controller hardware (previously undecided) is also an Arduino UNO Q. Updated the architecture, requirements, and TODO documents to match, and updated the UNO Q dev-tooling scripts for the new file paths. Drafted Review 1 (Expert Panel Review) content across all five rubric categories, using only information already established in the project's design documents, and explicitly flagged what's still missing: a literature survey, sustainability-goals treatment, a real costed bill of materials, and Phase 1 lab results (none of which exist yet). | `acoustic_node/`, `camera_node/`, `pick_place_node/` (new App Bricks-shaped folders); `pytest.ini`; updated `Automation_Architecture.md`, `documents/requirements/requirements.md`, `tools/uno_q/`; `documents/deliverables/review_1/Review_1_Content.md` (new); this logbook. | |

---

## Notes for keeping this current

- Add a new row each time a work session produces something worth recording — don't
  batch multiple days into one entry once this is being kept live going forward.
- `.CLAUDE/CLAUDE-LOG.md` stays the detailed technical log (design rationale, bugs found,
  file-level changes); this logbook should stay closer to what the college guidelines
  actually ask for — a dated record of activity, compact enough for the guide to sign
  off on regularly, not a full technical diff.
