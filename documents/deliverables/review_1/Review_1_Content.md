# Review 1 — Expert Panel Review — Content Draft

**Due: 2026-08-19 (tentative)** · 40 marks · rubric: `../VIT_Project1_Guidelines.md` § Review 1

This is narrative content organized under the five Review 1 rubric categories, drawn
from what's already decided in `documents/project/project_charter.md`,
`documents/project/Automation_Architecture.md`, `documents/requirements/requirements.md`,
and `TODO.md`/`.CLAUDE/CLAUDE-LOG.md`. It is **not** a fixed-field form like Review 0's —
Review 1 is an expert-panel review, so this is meant to be turned into a written report
and/or slides, not submitted as-is. Sections marked **OPEN — need your input** have no
source material anywhere in the project docs; those need you (and likely the guide)
before this is presentation-ready.

| Field | Details |
|---|---|
| Team | Mithunvel KL (23BMH1029), Kanigalpula Teja Pavan Sai Adinarayana Gupta (23BMH1007), Madhumitha Vemula (23BMH1019) |
| Tentative Project Title | Automated Tiles Sorting and Packing System |
| Guide | Dr. Jegadeeshwaran R (50204) |
| Client | Sree Murugan Tile Works (SMTW) |

---

## 1. Literature Survey, Need Analysis & Objectives (incl. Sustainability Goals) — 10 marks

### Need analysis (source: `project_charter.md` §1–§2)

This project is proposed for Sree Murugan Tile Works, a clay tile manufacturer where
inspection, grading, sorting, and packing are presently done entirely by manual labor.
Manual inspection has documented limitations that motivate the project:

- Human judgment varies between operators.
- Internal cracks are often not visible to the eye (needs acoustic/hidden-defect testing,
  not just visual).
- Repetitive inspection work causes operator fatigue.
- Data logging is usually incomplete or manual — no traceable quality record.
- High-speed production lines need consistent, repeatable grading decisions.
- Sorting and packing after inspection is itself labor-intensive.

### Objectives (source: `project_charter.md` §3)

- Automatically detect visible cracks, corner breaks, chips, shape defects, and surface
  defects (camera station).
- Detect hidden cracks/structural defects through acoustic response (acoustic station).
- Measure tile dimensions and compare against tolerance, including corner-vs-center
  deviation from uneven kiln shrinkage (dimensional station).
- Grade tiles into Grade A / Grade B / Grade C / Reject.
- Count and log every tile, storing image/acoustic/dimensional data, grade, timestamp,
  and sorting decision per tile.
- Sort tiles automatically via a Cartesian gantry pick-and-place mechanism and prepare
  sorted tiles for packing.
- Reduce dependence on manual quality inspection while keeping inspection conditions
  repeatable (controlled tile movement, repeatable acoustic tap).

### Literature survey

**OPEN — need your input.** No literature survey exists anywhere in the project docs.
The rubric wants a "comprehensive critical review" at the top band — this needs actual
papers/standards, not just this project's own design rationale. Candidate areas, given
what the system already does:

- Machine-vision defect detection for ceramics/tiles (crack, chip, corner-break
  detection via classical CV vs. CNN/segmentation — charter §6.1 already lists both as
  options).
- Acoustic/tap-test non-destructive testing for hidden cracks in ceramic or brittle
  materials (resonance/damping-based defect detection — charter §6.2, §11 already
  describe the intended feature set: dominant frequency shift, damping, ring decay).
- Dimensional/ToF or laser-based inspection for shape/warpage tolerance in fired
  ceramics.
- Existing industrial tile sorting lines (if any published case studies exist) for
  context on throughput/accuracy benchmarks to compare against.

I can help search for and summarize papers once you tell me which of these (or other)
angles you want covered, and how many sources the guide expects.

### Sustainability goals

**OPEN — need your input**, flagged since `documents/deliverables/review_1/README.md`
already notes nothing currently addresses this. Nothing below is a decided project
position — it's a starting point for you and the guide to confirm or replace:

- **Reduced material waste**: more consistent, multi-modal grading (visual + acoustic +
  dimensional) catches hidden defects that manual inspection misses, reducing both
  false-accepts (defective tiles shipped) and false-rejects (good tiles scrapped
  unnecessarily).
- **Reduced manual labor exposure**: automating a repetitive, fatigue-prone manual task;
  could be framed around worker safety/ergonomics rather than job displacement.
- **Digital quality records**: replaces paper-based or no logging with structured
  production data (FR-20), enabling data-driven process improvement at the kiln/firing
  stage over time (e.g. correlating defect rates with batches).
- **Energy/throughput**: not yet analyzed — would need real throughput numbers from
  Phase 1 testing to say anything quantitative here.

---

## 2. Methodology, Technical Design & Feasibility Analysis — 10 marks

### System architecture (source: `Automation_Architecture.md`, `project_charter.md` §5)

Distributed architecture: each inspection station is an independent compute node that
processes its own raw sensor data locally and sends only a compact result
(`{tile_id, station, grade, confidence}`) to a master PC over MQTT/wired Ethernet. The
master fuses all three station results into a final grade and commands the
pick-and-place gantry.

```text
Camera Node → Acoustic Node → Measurement Node → Master PC → Pick-and-Place Gantry
                                                       ↑
                                          Conveyor / Motion Controller
```

### Compute assignment (source: `Automation_Architecture.md` §5, `requirements.md` FR-10a)

| Role | Hardware | Status |
|---|---|---|
| Master | User's own PC | Confirmed |
| Camera node | Arduino UNO Q | Confirmed (may move to Raspberry Pi) |
| Acoustic node | Arduino UNO Q | Confirmed (may move to Raspberry Pi) |
| Measurement node | Not yet decided | Open — camera-only measurement is one option under evaluation |
| Conveyor / motion control | Arduino Mega | Confirmed |
| Pick-and-place controller | Arduino UNO Q | Confirmed 2026-08-03 |

**Code delivery convention (new, 2026-08-03):** every UNO Q station's code is organized
as an Arduino **App Bricks** project (`app.yaml` + `sketch/` + `python/`), matching
Arduino's own example convention, so each can be opened directly in Arduino App Lab. This
is a repo-organization decision made ahead of hardware — only one physical UNO Q board
exists so far.

### Three-method inspection approach (source: charter §1, §6)

| Method | Detects | Sensor |
|---|---|---|
| Camera | Surface cracks, corner damage, edge chips, shape/outline defects | USB/Pi camera + fixed LED lighting |
| Acoustic | Hidden/internal cracks, structural weakness | Mic + laser/ToF-triggered ball-drop impactor |
| Dimensional | Length, width, thickness, flatness, warpage, corner-vs-center deviation | ToF/laser sensors, or camera contour (open option) |

### Key design decisions and their rationale (source: charter, all dated 2026-07-10 unless noted)

- **Ball-drop impactor over solenoid striker** (§6.2): a laser/ToF sensor triggers a
  ball release; the ball strikes the tile under gravity rather than a solenoid directly
  striking it. Chosen for repeatable impact energy without direct mechanical wear on a
  striker tip. Release mechanism (electromagnet/solenoid gate/servo latch), ball
  mass/drop height still open.
- **Cartesian gantry pick-and-place over pneumatic pushers/servo diverters** (§7.4):
  chosen for both prototype and production (not just a later upgrade), gentler on
  fragile ceramic tiles, and supports stacking/packing directly.
- **Custom machine-control layer over off-the-shelf CNC/G-code controller** (§7.4, §8.2):
  master issues semantic commands (grade/slot), a dedicated layer on the gantry side
  translates to axis motion — decouples sorting logic from gantry kinematics, avoids
  depending on a machining-oriented toolchain (GRBL/Mach3/LinuxCNC) not built for
  pick-and-place.
- **Encoder-based conveyor tracking over time-based tracking** (`Automation_Architecture.md`
  §8): time delay is explicitly ruled out because it fails under conveyor slip, speed
  change, jams, e-stop, or handling delay.
- **Multi-point dimensional sampling** (§6.3): kiln firing causes uneven
  shrinkage/expansion, so corners deviate from nominal size more than the center —
  single-point measurement is insufficient; the system samples all four corners plus
  center.

### Feasibility / cost analysis

**OPEN — need your input.** `project_charter.md` §20 has a generic example BOM
(category-level, not costed), and `TODO.md` already flags turning it into a real costed
procurement list as outstanding now that UNO Q ×3 / Mega / master-PC are confirmed. This
review would score better with actual sourced prices, not just component categories — do
you want me to draft a costed BOM from what's confirmed so far (I can look up
representative prices for UNO Q, Mega, sensors, etc.), or is procurement pricing
something you're getting directly from suppliers/SMTW?

---

## 3. Implementation, Progress & Technical Quality — 10 marks

### What's actually built (source: `TODO.md`, `.CLAUDE/CLAUDE-LOG.md`)

- **Acoustic capture module** (`acoustic_node/python/acoustic/`): mic-trigger detection
  (`TriggerDetector` — rolling RMS threshold, pre-trigger ring buffer, cooldown),
  FFT/RMS/dominant-frequency signal processing, waveform+spectrum plotting, CLI
  (`--list-devices`, `--calibrate`, continuous monitor mode). Hardware I/O deliberately
  isolated from pure logic so the trigger/signal-processing code is unit-testable
  without a microphone.
- **11 unit tests, all passing** (`tests/test_capture.py`, `tests/test_signal_processing.py`),
  covering trigger logic with synthetic audio blocks and FFT/RMS with synthetic sine
  waves.
- **Formal requirements spec** (`documents/requirements/requirements.md`): 22 functional
  requirements + 7 non-functional requirements, each traceable to a charter/architecture
  section.
- **Dev tooling for the lab UNO Q board** (`tools/uno_q/`): SSH/VNC access, code push
  script, tunneled remote desktop.
- **Repo restructured onto Arduino App Bricks convention** (2026-08-03): acoustic module
  migrated into `acoustic_node/` unchanged and re-verified (all 11 tests still pass, CLI
  re-smoke-tested); `camera_node/` and `pick_place_node/` scaffolded for future work.

### Current build phase (source: charter §12.1)

Phase 1: Laboratory Proof of Concept. Goal: prove that camera, acoustic, and dimensional
data can separate good and bad tiles, using manual tile placement (no conveyor
automation yet). **Only the acoustic half of Phase 1 exists right now** — camera,
dimensional, control, decision/grading, sorting, and database layers are not started.

### Milestone status against the phased build plan (source: `TODO.md`)

| Milestone | Status |
|---|---|
| Acoustic capture + FFT pipeline | Done, tested |
| Real-mic calibration (`--calibrate` against the actual noise floor) | Not done — placeholder threshold only |
| Camera / visual inspection module | Not started |
| Dimensional inspection module | Not started |
| Conveyor / control layer | Not started |
| Database logging + dashboard | Not started |
| Pick-and-place mechanism | Not started (controller hardware now decided) |

**OPEN — need your input** (or already know and can restate for me): what's the actual
target for "implementation, progress" as scored at this review — is a full multi-station
bench demo expected by 2026-08-19 (which `.CLAUDE/CLAUDE-LOG.md` records as the stated
aim from an earlier session), or is a strong acoustic-only proof-of-concept plus a clear
plan for the rest acceptable? That materially changes how much build work needs to
happen before the review vs. how much can be presented as "designed, not yet built."

---

## 4. Results, Analysis & Sustainability Impact — 6 marks

**OPEN — no results exist yet.** This category needs actual Phase 1 lab data: acoustic
signatures from real good vs. defective tiles, repeatability numbers, and — if
camera/dimensional get built in time — comparable data from those stations. None of the
following has happened yet:

- Real-mic calibration against the actual noise floor (Windows Microphone Enhancements
  not yet confirmed disabled either — current RMS/FFT numbers, if any are collected
  before that, are pipeline smoke-test data only, not valid signal characterization).
- Tapping/recording real tiles (good and defective) to see whether acoustic features
  actually separate them.
- Any dataset collection (`project_charter.md` §22.1 calls for labeled good/cracked/
  broken-corner/warped samples).

Sustainability impact scoring here depends on the sustainability goals in §1 above being
defined first, then ideally backed by a number (e.g. estimated reduction in false-reject
scrap rate from a real test batch) — not available yet either.

---

## 5. Presentation & Communication Skills — 4 marks

This category is scored live at the panel (organization, clarity, confident question
handling) — not something to pre-write as content. Worth deciding as a team, ahead of
2026-08-19:

- Who presents which section (ties into the not-yet-decided team role split flagged in
  `.CLAUDE/CLAUDE-LOG.md`).
- Whether to lead with the working acoustic demo live, or with the system architecture
  first — usually stronger to show working code/data before the full future-scope
  design.
- Rehearsing answers for the obvious panel questions this draft surfaces: why ball-drop
  over solenoid, why a custom gantry layer instead of GRBL, and what's actually
  demonstrable today vs. still on paper.

---

## Summary of open items before this is presentation-ready

1. Literature survey — needs real sources, not in project docs anywhere.
2. Sustainability goals — draft starting points offered above, need your/guide's
   confirmation.
3. Costed BOM — generic categories exist (§20), no real prices yet.
4. Phase 1 results — no data collected yet; needs real-mic calibration + tap testing on
   labeled tile samples before 2026-08-19.
5. Scope clarification — how much of camera/dimensional/control/database needs to exist
   by review time vs. presented as planned.
6. Team role split and presentation assignments — not yet decided.
