# Claude Log

## 2026-07-08/09 — Acoustic capture module: mic trigger + FFT plot
- Set up `venv/` at repo root; installed `sounddevice`, `numpy`, `scipy`, `matplotlib`, `pyyaml`, `pytest`; froze to `requirements.txt`.
- Built `acoustic/` package: `config.yaml` (device/sample-rate/trigger tunables), `signal_processing.py` (pure `compute_rms`/`compute_fft`/`dominant_frequency`), `capture.py` (`TriggerDetector` pure trigger-state logic + `AudioCapture` thin `sounddevice` wrapper), `plotting.py` (waveform + FFT matplotlib view), `live_monitor.py` (CLI: `--list-devices`, `--calibrate`, default continuous-listen mode).
- Split hardware I/O from pure logic deliberately so trigger detection is unit-testable without a mic — `tests/test_capture.py` exercises `TriggerDetector` with synthetic blocks, `tests/test_signal_processing.py` exercises FFT/RMS with synthetic sine waves. 11 tests, all passing.
- Found and fixed a real bug during testing: `dominant_frequency` only excluded the DC bin (index 0), but a large DC offset leaks across several low bins even after Hann windowing. Fixed by ignoring all bins below 20 Hz (matches the audible range the project cares about) instead of just bin 0.
- Also caught and fixed a bug in `live_monitor.run_monitor()` before it shipped: used `queue.get(timeout=0.5)` inside the listen loop, which would have raised `queue.Empty` and killed the whole monitor after half a second of silence. Switched to a blocking `get_clip()` — `KeyboardInterrupt` still works fine on a blocking queue read.
- Ran `--list-devices` as a smoke test: confirmed `sounddevice`/PortAudio sees this machine's mic array (several Realtek "Microphone Array" entries, one with a 16000 Hz default sample rate — flagged in `CLAUDE.md` as the thing to check first if `AudioCapture.start()` ever fails to open at 44100 Hz).
- Filled in `.CLAUDE/CLAUDE.md` (Project Overview, Running the System, Architecture, Key Modules, Data Files, Platform Constraints, Deployment Notes, Known Technical Debt, Development Rules) to reflect the acoustic module as the only thing built so far; deleted Schema Reference / Key Conventions sections as not-yet-applicable (no DB, no encoded fields yet).
- Decisions made (per user): continuous auto-trigger workflow (not manual single-shot); live-display only for now, WAV/data saving deferred to later; fresh venv at repo root.
- Left incomplete, needs the user's hands: Windows Microphone Enhancements (AGC/noise suppression) have not been confirmed disabled — instructions were given but not verified. `--calibrate` and a full live trigger-to-plot run against the real mic have not been executed (both need an interactive mic session I can't run). `trigger.rms_threshold` in `config.yaml` is a placeholder, not a calibrated value.
- Added `project/hardware.md`, `project/software.md`, `project/communication.md`, `project/control-flow.md` to `TODO.md` as planned future breakout docs from `project_charter.md` (not created yet).

## 2026-07-10 — Documentation architecture overhaul + requirements spec + college deliverables
- Restructured docs per user request: moved repo-root `project/` → `documents/project/` (git mv, history preserved); added `documents/mech/`, `documents/electrical/`, `documents/programming/` as scaffolding (README + `.gitkeep` placeholders, no real content yet — none exists to move in); added `documents/requirements/`.
- Drafted `documents/requirements/requirements.md`: a real FR-xx/NFR-xx requirements spec extracted from `project_charter.md` and `Automation_Architecture.md`, with per-requirement traceability back to charter sections. Marked as a first pass, not yet exhaustively reviewed against the charter.
- Removed the "About This Repository" / "How to Start a New Project" sections from `.CLAUDE/CLAUDE.md` — these described the meta template-library repo, not this project instance, and contradicted the rest of the file (a pre-existing copy-paste artifact from templating, cleaned up as part of this pass).
- Captured new architecture decisions the user described mid-session and folded them into `documents/project/project_charter.md` and `Automation_Architecture.md`: (1) dimensional inspection must sample corners + center, not just one point, because kiln firing causes uneven shrinkage/expansion (corners deviate more than center) — added to charter §6.3; (2) camera-only dimensional measurement flagged as an open option since the camera already extracts tile contour; (3) sorting/handling mechanism confirmed as a Cartesian gantry pick-and-place under machine control, not left as one option among several — charter §7.4 Decision; (4) charter §9.3 (Tile ID) now points to `Automation_Architecture.md` §8's encoder-based conveyor tracking as the authoritative mechanism, since §9.3 alone only defines the ID format, not the tracking approach the user was actually asking about ("robust way to know which tile is which" was already well-covered in `Automation_Architecture.md`, just not cross-referenced from the charter).
- Added `deliverables/` (college coursework — separate from `documents/`, which is engineering-only): populated with the real VIT Chennai BMEE497J/BMHA497J Project 1 guidelines the user received by email, not a generic placeholder — `deliverables/VIT_Project1_Guidelines.md` (full rubrics + schedule), and per-review folders (`review_0/`, `review_1/`, `review_2/`, `guide_evaluation/`, `final_report/`, `logbook/`) each mapping their rubric to what already exists in `documents/`.
- Folded in a second email (HOD instructions, received 2026-07-06) into `deliverables/review_0/README.md`: Zeroth Review Form is a **hardcopy**, due on/before 2026-07-13, hard deadline (marks reduced if missed); after guide approval on the form, guide + project title must also be updated and approved in VTOP separately; recorded project coordinator contacts.
- Updated `.CLAUDE/CLAUDE.md`: new "Documentation Architecture" section indexing `documents/`/`deliverables/`, new Development Rule 4 (file new artifacts into the matching folder in the same commit), all `project/*.md` path references repointed to `documents/project/*.md`, Known Technical Debt updated to flag `requirements.md` as unreviewed-for-completeness.
- Updated `TODO.md`: path references repointed to `documents/project/...`; added items for populating the still-empty `documents/` subfolders, the missing sustainability-goals treatment (needed for Review 1's rubric), starting the daily logbook, and the encoder-tracking implementation gap.
- Filled in the actual Review 0 form content once the user provided it: `deliverables/review_0/Review_0_Form.md` (team of 3 — Mithunvel KL 23BMH1029, Kanigalpula Teja Pavan Sai Adinarayana Gupta 23BMH1007, Madhumitha Vemula 23BMH1019; guide Dr. Jegadeeshwaran R 50204; project proposed for a real client, Sree Murugan Tile Works). Added this team/guide/client context to `.CLAUDE/CLAUDE.md` Project Overview and a grounding line in charter §2 (Problem Statement), since it directly strengthens the Review 0 "Relevance & Problem Identification" rubric criterion.
- Left incomplete: no real CAD/electrical/programming content exists yet (scaffolding only); `requirements.md` hasn't been checked section-by-section against the full charter; sustainability-goals content doesn't exist anywhere yet (needed for Review 1); Review 0 form still needs guide + HoD signature on the physical hardcopy and the VTOP update. None of these changes have been committed to git yet — left for the user to review first.
- Per user follow-up, moved `deliverables/` inside `documents/` (now `documents/deliverables/`) rather than a repo-root sibling — updated all cross-references in `.CLAUDE/CLAUDE.md`, `TODO.md`, and `documents/README.md` accordingly. `documents/deliverables/README.md`'s framing note updated to reflect it's a subfolder distinguished by content type, not a top-level sibling.
- Started design work per the user's request, beginning with the camera subsystem: captured as decisions in `documents/project/project_charter.md` §6.1 ("Role as First Station"), §7.4, and §8.2, plus `Camera_Sorting_Subsystem.md` and `Automation_Architecture.md` — (1) camera is first in the inspection line and is the node that announces tile presence + visual result to the master, opening that tile's record, and owns the running tile count; (2) camera can supply a secondary belt-speed estimate from frame motion, but the conveyor encoder stays the authoritative tracking reference (open question left unresolved: whether camera doubles as the encoder-zero entry sensor or a separate one still exists); (3) the pick-and-place gantry will run a custom machine-control layer — master issues high-level semantic place commands, a dedicated layer translates to axis motion — explicitly not an off-the-shelf CNC/G-code controller. Added corresponding FR-16/FR-17/FR-18 to `documents/requirements/requirements.md` and follow-up tasks to `TODO.md`.
- Committed the full documentation restructuring (`22e7ef5`) after the user confirmed it should go in, then asked the user a round of pitfall/risk questions (timeline realism, hardware/tile access, team roles, commit-now) before continuing — answers: aiming for a full multi-station bench demo by Review 1 (2026-08-19), camera dataset + real tile samples already in hand (hardware/rig still to build), team roles not yet split, commit approved and done.
- Captured the higher-level monitoring architecture and new acoustic/compute hardware decisions the user described: (1) two-tier monitoring — each station (camera/acoustic/dimensional) gets its own local live-status display, plus a master/overall dashboard, both views over the same per-tile data; production data (grade counts, defect breakdown) explicitly meant for real industrial reuse, not just prototype debugging — charter §14 Monitoring Architecture, requirements.md FR-19/FR-20. (2) Acoustic tapping mechanism changed from the charter's original push-pull solenoid striker to a **ball-drop impactor**: a laser/ToF sensor detects the tile, an Arduino UNO Q triggers the ball release, the ball strikes the tile under gravity, and the same UNO Q processes the resulting sound before reporting to master — charter §6.2 Decision (kept the solenoid-striker section as background/rationale rather than deleting it), requirements.md FR-21. Flagged as still open: the release mechanism itself (electromagnet/solenoid-gate/servo), ball mass/drop height, reload method, and that several other charter sections (§17, §19, §20, §25) still describe the old solenoid-strikes-tile-directly design and haven't been reconciled yet. (3) Compute hardware: master = user's own PC (decided); camera node and acoustic node = Arduino UNO Q each, with an open option to swap either (not both) to Raspberry Pi; conveyor/motion control = Arduino Mega (decided); pick-and-place controller board not yet chosen — `Automation_Architecture.md` §5.1–§5.6, requirements.md FR-10a. Added corresponding `TODO.md` items for all the open decisions above.
- User pasted a export from a separate personal project-tracking assistant (a parallel "SMTW/" note with its own frontmatter, task list, rubrics, and decision log for this same project). Cross-checked it against this repo: mostly consistent (same VIT guidelines, team, dates), and it's already behind this repo's decisions (still lists "individual vs group" and hardware/SBC choice as open, both resolved here). It did surface one real, previously undocumented gap: **tile size/weight range from SMTW has never been obtained**, which blocks concrete conveyor width, gantry travel, gripper sizing, dimensional tolerances, and ball-drop energy calibration — added to `documents/requirements/requirements.md` Open Items, `TODO.md`, and `.CLAUDE/CLAUDE.md` Known Technical Debt. Also added: project type classification ("prototype design + experimentation & analysis") to `deliverables/review_0/README.md`, and a TODO to turn charter §20's generic example BOM into a real costed procurement list now that UNO Q/Mega/master-PC choices are confirmed.

## 2026-08-03 — Multi-UNO Q / Arduino App Bricks restructure + Review 1 content draft

Session started with the user asking to (1) discuss and draft Review 1 content using
only known technical information, leaving gaps explicit; (2) adopt a new architecture
direction — every UNO Q station gets its own project, structured so it can be opened in
Arduino App Lab the same way as Arduino's own example apps (linked
`app-bricks-examples/.../03-led-matrix-animation-mcu`); (3) update this log with a clear
record of what was done and when, for reuse in the college logbook submission.

**Requirements clarification (asked before making changes, per user's own "ask me if
unclear" instruction):**
- Review 1 output format: user chose **narrative content organized by rubric category**
  (not a fixed form — Review 1 is an expert-panel review, unlike Review 0's hardcopy
  form), to be turned into a report/slides later.
- Physical UNO Q hardware: **only the one existing lab board** (hostname `KLM`,
  `172.20.10.2`) — the multi-node plan is a software/repo-structure decision made ahead
  of procuring more boards, not confirmation of new hardware in hand.
- Node scope for this phase: **camera, acoustic, and pick-and-place** each get their own
  UNO Q. Measurement/dimensional stays an open hardware decision (unchanged from before).
  Conveyor stays on the Arduino Mega — the UNO Q assignment is pick-and-place only, not a
  Mega replacement.
- Existing `acoustic/` module (dev-laptop-tested, 11 passing unit tests): user chose to
  **migrate it now** into the new App Bricks-shaped `acoustic_node/`, rather than leaving
  it at the repo root and building new nodes alongside it.

**Research performed before writing anything:** fetched Arduino's
`app-bricks-examples` repo (`core-and-foundational/02-led-matrix/03-led-matrix-animation-mcu`)
via WebFetch to confirm the real folder shape — `app.yaml` (name/description/icon
metadata), `sketch/` (`sketch.ino`, `sketch.yaml`, and any supporting headers),
`python/main.py` (imports `arduino.app_utils.App`, calls `App.run()`). GitHub's API was
rate-limited for direct `git ls-tree`/`contents` calls from this network, so the
structure was confirmed via the rendered directory listings and raw file fetches
instead — sufficient to confirm the three-part shape and the `main.py` pattern, but the
exact `sketch.yaml` schema and what `App.run()` actually orchestrates were **not**
independently verified against a real App Lab session (recorded as an open item, not
guessed at further).

**Repo restructuring (mechanical changes, then re-verified against the test suite):**
- `git mv`'d `acoustic/{__init__.py,config.yaml,capture.py,signal_processing.py,
  plotting.py,live_monitor.py}` → `acoustic_node/python/acoustic/` unchanged (no code
  edits — only location changed, since the package's own internal imports are relative
  to itself). Removed the emptied `acoustic/` directory and its stray `__pycache__`.
- Added `pytest.ini` (`pythonpath = acoustic_node/python`) so `tests/test_capture.py` and
  `tests/test_signal_processing.py` keep importing `from acoustic.capture import ...`
  unchanged, with no test-file edits needed.
- Added `acoustic_node/app.yaml`, `acoustic_node/python/main.py` (App Lab entry point,
  explicitly flagged as an unverified stub — not wired to `acoustic/capture.py` yet),
  `acoustic_node/sketch/sketch.ino` + `sketch.yaml` (empty stubs for the laser/ToF
  trigger + ball-drop release logic — deliberately not hardcoding pins or mechanism
  choices, since those are still undecided per `requirements.md` FR-21 Open Items), and
  `acoustic_node/README.md` explaining what's real (the migrated Python package) vs.
  stub (everything App-Lab-related) and how to still run it (`cd acoustic_node/python`,
  same CLI commands as before).
- Scaffolded `camera_node/` and `pick_place_node/` with the same `app.yaml`/`sketch/`
  /`python/main.py` shape — both explicitly empty stubs, no real code, since neither
  module exists yet.
- **Verification, not just assertion:** discovered the local `.venv` (PyCharm default,
  distinct from the `venv/` name `.CLAUDE/CLAUDE.md` had documented) only had `pip`
  installed — `requirements.txt` had never actually been installed into it despite the
  log's earlier claim of "11 tests, all passing." Installed `requirements.txt`, then ran
  `pytest tests/ -v` — **all 11 tests pass unchanged** after the move — and smoke-tested
  `python -m acoustic.live_monitor --list-devices` from the new location to confirm the
  CLI entry point still works. Flagged the stale `venv/` vs `.venv/` naming mismatch as a
  new Known Technical Debt item rather than silently fixing/renaming it, since it wasn't
  part of what was asked.
- Updated `tools/uno_q/push.bat` (new remote paths under
  `acoustic_node/python/acoustic/`, now also pushes `pytest.ini`) and
  `tools/uno_q/README.md` (path references, plus a note that only one physical board
  exists and `push.bat` only pushes `acoustic_node/` since it's the only node with real
  code).

**Documentation updates to match:**
- `Automation_Architecture.md` §5.6: pick-and-place controller changed from "hardware
  not yet fixed" to **decided — Arduino UNO Q** (2026-08-03). Added new §5.7 "Node Code
  Delivery Convention (App Bricks)" documenting the `<node>/{app.yaml,sketch/,python/}`
  shape, which nodes have it, the reference-implementation status of `acoustic_node/`,
  and an explicit callout that only one physical board exists so far — the three-folder
  structure is not confirmation of three physical boards. Also noted the control network
  itself (MQTT over wired Ethernet, §12/§23) is unchanged — the App Bricks convention is
  about code organization, not the wire protocol between nodes.
- `documents/requirements/requirements.md`: updated FR-10a (pick-and-place = UNO Q, now
  fully Confirmed rather than "confirmed except pick-and-place board"); added new FR-22
  for the App Bricks node convention, status "Confirmed structure; App Lab runtime
  behavior not yet verified on hardware"; added two Open Items (App Lab unverified;
  pick-and-place mechanical specifics still undecided even though the controller board
  is now chosen).
- `.CLAUDE/CLAUDE.md`: updated Project Overview (entry point path, App Bricks summary),
  Running the System (commands now run from `acoustic_node/python/`), Architecture (new
  node-folder table + updated Key Modules paths), Simulation vs Real Mode, Deployment
  Notes (one-board-vs-three-node-folders distinction), Known Technical Debt (three new
  items: App Lab unverified, node folders ahead of hardware, `venv`/`.venv` mismatch),
  and a new Development Rule 5 (new hardware-station code must use the `<station>_node/`
  App Bricks shape, not a bare repo-root package like the old `acoustic/`).
- `TODO.md`: moved the acoustic-module and doc-restructuring work into Done with today's
  date; updated the camera/pick-and-place/acoustic-trigger Not Started items to reflect
  the new scaffolding (empty stubs now exist, still nothing real); removed the
  now-resolved "decide pick-and-place hardware" item and replaced it with the narrower
  remaining question (axis count/motor/gripper/travel limits); added a new item to
  verify the App Lab entry-point pattern against real hardware; added Review 1 to In
  Progress.
- `documents/deliverables/review_1/README.md`: added a "Status: content drafted" section
  pointing at the new `Review_1_Content.md` and summarizing its open items.

**Review 1 content draft** (`documents/deliverables/review_1/Review_1_Content.md`, new
file): full narrative content under all five rubric categories (Literature Survey/Need
Analysis/Objectives incl. Sustainability, Methodology/Technical Design/Feasibility,
Implementation/Progress/Technical Quality, Results/Analysis/Sustainability Impact,
Presentation & Communication), pulling directly from the charter, architecture doc,
requirements spec, and TODO/log — with every genuinely missing piece called out as
**OPEN — need your input** rather than invented:
- No literature survey exists anywhere in the project docs — listed candidate research
  angles (vision-based ceramic defect detection, acoustic/tap-test NDT, ToF/laser
  dimensional inspection) rather than fabricating citations.
- No sustainability goals exist anywhere — offered draft starting points (reduced
  material waste from better grading, reduced manual-labor fatigue exposure, digital
  quality records) explicitly labeled as not-yet-decided, not asserted as settled.
- No costed BOM exists — charter §20's BOM is generic/category-level; asked whether to
  draft real costed numbers now that UNO Q ×3/Mega/master-PC are confirmed, or whether
  the user is sourcing pricing directly.
- No Phase 1 results exist — acoustic module has never been calibrated against a real
  mic noise floor or run against real good/defective tiles yet; flagged this as blocking
  the Results category entirely until that lab work happens.
- Surfaced one scope question that materially changes remaining workload: whether a full
  multi-station bench demo is actually expected by 2026-08-19 (as an earlier session's
  log records as the stated aim) or whether a strong acoustic-only proof-of-concept plus
  a clear forward plan is acceptable for this review.

**Left incomplete / needs the user's hands:** literature survey sources, sustainability
goals sign-off, a real costed BOM, all Phase 1 lab data (mic calibration + tap testing
on labeled tiles), the Review 1 scope-vs-timeline question above, team role/presentation
split, and actually opening/testing any of the three node folders in real Arduino App
Lab against the lab UNO Q board (the App Bricks shape has never been run, only modeled
on Arduino's published examples). None of this session's changes have been committed to
git yet.

**Follow-up in the same session — college daily logbook back-filled:** the user asked
again, explicitly, for a dated log of what was done and when — pointing at
`documents/deliverables/logbook/`, the actual VIT-guidelines-mandated daily logbook
(`../VIT_Project1_Guidelines.md` §5), which had been scaffolded back on 2026-07-10 but
never populated ("No entries yet"). This is a different artifact from this file — this
log is the technical dev log with design rationale and file-level detail;
`documents/deliverables/logbook/Logbook.md` needs to be the compact, guide-signable,
dated activity record the college actually asks for. Created it with one row per work
session covered above (2026-07-08/09, 2026-07-10, 2026-07-28, 2026-08-03), each with a
plain-language activity summary, the resulting deliverable, and a blank signature column
for the guide. Updated `documents/deliverables/logbook/README.md` to point at it, and
`TODO.md` to reflect that the digital draft exists but still needs the guide's actual
signature on a physical copy.

This session ended with the user asking to commit the accumulated changes. Before doing
so, reviewed the diff of every modified/new file for anything sensitive and found
`README.md` (repo root) contained what looked like a live SSH credential
(`ssh arduino@172.20.10.2` / a password) pasted directly into it — not gitignored, about
to enter git history. Flagged it to the user rather than committing or silently
stripping it; user confirmed removing it. Cleared `README.md` back to empty (matching
its last-committed state) before staging anything — the real credential already lives in
`tools/uno_q/secrets.bat`, which is gitignored, so nothing was lost. Then staged and
committed everything reviewed above as commit `9bad0ce`.

## 2026-08-07 — Camera vision pipeline: tile isolation, crack/corner measurement, WiFi dashboard

User asked for the first real `camera_node/` build-out: a GUI dashboard showing how many
tiles cross the belt and what crack/corner defects each one has, with the camera feed
streamed over WiFi. Camera hardware isn't ready yet (a separate PC-style webcam will
eventually connect to the UNO Q). User described the intended defect-detection approach
directly: isolate the tile from the rest of the frame, then grayscale + an edge algorithm
to find cracks — tiles are brown earthen terracotta. They also mentioned an earlier
attempt using an existing crack-recognition example (from Arduino's own App Lab
examples) hadn't worked well.

**Clarified before writing code:** asked about camera hardware readiness, whether sample
tile photos existed to tune thresholds against, and GUI tech (web vs. desktop). First
round answered camera hardware (separate webcam, will connect to the UNO Q, rig not
ready) but the user wanted to clarify the other two before answering — turned out they
*do* have sample photos (crack/corner-break/good tiles), just from a different camera
than the one that'll actually be used, and not accessible on this machine. They also
reframed the actual need: an earlier model only output a grade classification; what's
needed is whether a crack exists *and how big it is* — a measurement, not just a
category. Re-asked with that context: confirmed **classical CV measurement** (grayscale
+ edge detection + contour analysis, matching what the user described) over training a
new ML/CNN classifier — directly gives a size/severity number and doesn't need much
training data, unlike a classifier. Photos being inaccessible here meant building with
explicitly placeholder/provisional thresholds (same pattern as the acoustic module's
`rms_threshold`), not guessing at "final" values. Dashboard tech (web via Flask) wasn't
re-asked — decided directly, since "stream via WiFi" and the project's own existing
recommended stack (charter §21: Flask/Streamlit) both point the same way, and stated the
assumption in-repo rather than spending another question round on it.

**Built `camera_node/python/camera/`**, mirroring `acoustic_node`'s pure-logic/hardware-
wrapper split (Development Rule 1):
- `segmentation.py` — `segment_tile()`: HSV color-range threshold + largest-contour
  selection isolates the tile from the background. Pure, no I/O.
- `crack_detection.py` — `detect_cracks()`: grayscale → Gaussian blur → Canny edges →
  keep only contours that are both long *and* thin (elongated) enough to be a crack
  rather than surface texture or a compact defect; measures length from the winning
  contour's `minAreaRect`, grades severity (minor/major) against a length threshold.
  Pure, no I/O — this is the actual "how big" measurement the user asked for.
- `corner_detection.py` — `detect_broken_corner()`: ratio of the tile's own contour area
  to its bounding rectangle's area; a low ratio means a corner is missing, and gives a
  missing-area measurement. Deliberately does not localize *which* corner — that needs
  real broken-tile photos to validate against, which aren't available yet; said so
  explicitly in the docstring rather than guessing.
- `tile_tracker.py` — `TileTracker`: debounced presence/absence state machine (mirrors
  `TriggerDetector`'s cooldown/pre-trigger pattern, applied to boolean tile-presence
  instead of an RMS threshold), counts tiles crossing the frame. This is a local,
  camera-frame-based count for the station's own dashboard — explicitly documented as
  *not* the conveyor's authoritative encoder-based tracking (`Automation_Architecture.md`
  §8), which is a different mechanism for a different problem.
- `pipeline.py` — `process_tile()`: wires the three detectors into one `TileRecord` +
  a first-pass rule-based grade (any broken corner or major crack → Reject, minor crack
  → Grade B, else Grade A) — explicitly the camera station's own local grade, not the
  master's fused final grade (FR-13).
- `capture.py` (`WebcamCapture`, thin `cv2.VideoCapture` wrapper) + `worker.py`
  (`CameraWorker` background thread + `SharedState` thread-safe latest-value store) —
  the hardware/glue layer. OpenCV has no callback-driven capture like `sounddevice`, so
  this spins its own thread rather than getting one for free; kept the pure detection
  modules untouched by that concern.
- `dashboard.py` + `templates/dashboard.html` — Flask app: `/video_feed` (MJPEG stream),
  `/api/status` (JSON: tile count + recent tiles with crack/corner detail), `/`
  (dashboard page: live feed, running count, results table). Serves on `0.0.0.0` so it's
  reachable from any browser on the same WiFi, not just the camera node itself — this is
  the camera station's "station-local monitor" (charter §14, FR-19), now implemented.
- `live_dashboard.py` — CLI entry point, same shape as `acoustic_node`'s
  `live_monitor.py`.
- `config.yaml` — every segmentation/detection/dashboard tunable, all commented as
  provisional placeholders for brown terracotta, not calibrated.

**Verification, not just assertion:** installed `opencv-python-headless` and `Flask`
into `.venv` (froze into `requirements.txt`, keeping the existing alphabetical/pinned
style). Wrote 15 new synthetic-image/sequence unit tests
(`tests/test_camera_segmentation.py`, `test_camera_crack_detection.py`,
`test_camera_corner_detection.py`, `test_camera_tile_tracker.py`) — e.g. a rectangle
drawn in a known color to verify segmentation finds the correct bbox/area, a drawn line
to verify crack length measurement and severity grading, a circle to verify the
aspect-ratio filter rejects compact (non-crack) blobs, a pentagon with one corner clipped
to verify broken-corner detection and its missing-area measurement, and boolean presence
sequences to verify the tile counter debounces noise and counts multiple tiles
separately. Added `camera_node/python` to `pytest.ini`'s `pythonpath`. Full suite: 26/26
passing (11 acoustic + 15 camera). Additionally ran a full synthetic end-to-end smoke
test (image → `segment_tile()` → `process_tile()` → `SharedState` → Flask test client
hitting `/api/status` and `/`) to confirm the whole pipeline and dashboard wire together
correctly, not just each piece in isolation — a rectangle with a drawn line correctly
came back as `crack_detected=True, severity='major', grade='Reject'` through the actual
API response.

**Documentation updated to match:** `camera_node/README.md` rewritten from the
"nothing built yet" stub to describe what's real, what's still stub (`sketch/`, the App
Lab `python/main.py` entry), how to run it, and an explicit "Known limitations" section
(untuned thresholds, crack "type" = severity not taxonomy, corner detection doesn't
localize, frame-based count isn't the encoder tracking, streaming video is an
intentional exception to the "compact results only" master-communication principle).
`.CLAUDE/CLAUDE.md`: Project Overview, Running the System, Architecture (new camera data
flow + threading-model subsections, full file table), Key Modules (one entry per camera
file), Data Files, Platform Constraints, and Known Technical Debt all updated; added
`*.jpg`/`*.jpeg`/`*.png` to `.gitignore` (gap noticed while writing the Data Files
section — camera frames/photos would have had nowhere to be excluded once dataset
saving starts). `documents/requirements/requirements.md`: added FR-23 (measure, don't
just classify — and why), FR-24 (segmentation), FR-25 (WiFi dashboard implementing
FR-19), plus three new Open Items reiterating that none of the thresholds are validated
and the webcam path is untested end-to-end. `TODO.md`: moved the camera build into Done;
replaced the old "camera module not started" line with the specific remaining work
(threshold tuning against the user's photos once accessible, real-webcam end-to-end run,
wiring to the UNO Q once the rig exists, crack-type/corner-localization as future work).

**Left incomplete / needs the user's hands:** every detection threshold needs tuning
against real tile photos (the user's existing photos weren't accessible on this machine
— get them here, or hand them over, before trusting any crack/corner result); the whole
pipeline has never run against a real webcam, only synthetic images; the webcam-to-UNO-Q
physical wiring doesn't exist yet; crack-type taxonomy and corner localization are
explicitly deferred. None of this session's changes have been committed to git yet.
