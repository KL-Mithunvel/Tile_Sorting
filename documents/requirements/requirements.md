# Requirements Specification — Automated Tile Sorting System

Formal functional (FR) and non-functional (NFR) requirements, extracted from
`documents/project/project_charter.md` and `documents/project/Automation_Architecture.md`.
Each requirement cites the charter/architecture section it traces back to — read that
section for full rationale and hardware options; this document exists so requirements
have stable IDs to reference from code, tests, and college deliverables.

Status legend: **Confirmed** (decided direction) · **Open** (documented but not decided).

---

## 1. Functional Requirements

### 1.1 Inspection

| ID | Requirement | Traceability | Status |
|---|---|---|---|
| FR-01 | The system shall inspect every tile using three independent methods: camera (visual), acoustic (tap test), and dimensional (measurement). | Charter §1, §4.3–§4.5 | Confirmed |
| FR-02 | The camera station shall detect visible cracks, corner breaks/chips, missing edge portions, shape/outline defects, and (where applicable) glaze/print/color defects. | Charter §6.1; `Camera_Sorting_Subsystem.md` | Confirmed |
| FR-03 | The acoustic station shall detect internal/hidden cracks and structural defects not visible to the camera, via a repeatable controlled-impact tap test and frequency-domain analysis of the resulting sound. | Charter §6.2 | Confirmed |
| FR-04 | The dimensional station shall measure length, width, thickness, squareness, edge straightness, warpage, and surface height variation against tolerance. | Charter §6.3 | Confirmed |
| FR-05 | Dimensional inspection shall sample multiple points per tile (minimum: all four corners plus center) and report corner-to-center deviation, because kiln firing causes uneven shrinkage/expansion — corners commonly deviate from nominal size more than the center does. A single-point or outline-only measurement is not sufficient. | Charter §6.3 (Purpose) | Confirmed |
| FR-06 | Dimensional measurement may be performed using the existing calibrated camera station (outline/length/width/corner-vs-center via contour analysis) instead of dedicated ToF/laser hardware. Thickness and true flatness/warpage still require a distance sensor regardless. | Charter §6.3 (Recommended Prototype Approach) | Open — pending real tile samples + calibrated rig |
| FR-16 | The camera station shall be positioned first in the inspection sequence (before acoustic and dimensional). On detecting a tile, it shall report tile presence plus its visual result to the master, opening that tile's record; it shall also maintain the running tile count. | Charter §6.1 (Role as First Station); Automation_Architecture.md §2 | Confirmed |
| FR-17 | The camera station may additionally provide an estimated belt speed derived from frame-to-frame tile motion. This is a secondary, cross-check value only — the conveyor encoder is the authoritative speed/position reference (FR-12) and camera-derived speed shall not be used for tile tracking. | Charter §6.1 (Role as First Station) | Confirmed |

### 1.2 Distributed Processing & Data Flow

| ID | Requirement | Traceability | Status |
|---|---|---|---|
| FR-07 | Camera, acoustic, and dimensional inspection shall each run on their own independent compute node ("station node"), processing raw sensor data locally. | Automation_Architecture.md §2, §4 | Confirmed |
| FR-08 | Each station node shall send only a compact processed result (e.g. `{tile_id, station, grade, confidence}`) to the master — not raw images, audio, or point clouds, during normal operation. | Automation_Architecture.md §4 | Confirmed |
| FR-09 | A master computer shall aggregate all station results for a given tile, compute the final grade, create/maintain the database entry, and issue the handling command. | Automation_Architecture.md §2, §5; Charter §9.2, §9.4 | Confirmed |
| FR-10 | Station nodes and the master shall communicate over a common wired network using a shared messaging protocol (MQTT, per current architecture). | Automation_Architecture.md §2 | Confirmed |

### 1.3 Tile Identity & Tracking

| ID | Requirement | Traceability | Status |
|---|---|---|---|
| FR-11 | Every tile shall receive a unique tile ID on entry to the system; every station result shall be tagged with that ID so results cannot be mixed between tiles. | Charter §9.3; Automation_Architecture.md §3 | Confirmed |
| FR-12 | Because station nodes run asynchronously, the system shall track which physical tile is at which station using conveyor encoder position (entry encoder count + station distance), not elapsed time — time-based tracking is explicitly ruled out (fails under slip, speed change, jam, e-stop, or handling delay). | Automation_Architecture.md §8 | Confirmed |

### 1.4 Grading, Handling, and Logging

| ID | Requirement | Traceability | Status |
|---|---|---|---|
| FR-13 | The system shall combine visual, acoustic, and dimensional results into a single grade (e.g. Grade A/B/C/Reject) per tile. | Charter §3, §10 | Confirmed |
| FR-14 | Sorted/handled tiles shall be moved by a Cartesian gantry performing pick-and-place under machine control, directed by the master's final grade + position. This is the confirmed handling mechanism for both prototype and production — not just a production-phase upgrade. | Charter §7.4 (Decision, 2026-07-10) | Confirmed |
| FR-18 | The gantry shall be controlled via a custom machine-control layer that accepts high-level, semantic commands from the master (e.g. grade/slot to place at) and translates them into low-level axis motion — not an off-the-shelf CNC/G-code controller (GRBL/Mach3/LinuxCNC or similar). | Charter §7.4 (Control architecture decision, 2026-07-10); §8.2 | Confirmed |
| FR-15 | Every tile shall be logged to a database with inspection results (image ref, acoustic features, dimensional readings), final grade, timestamp, and running production counts per grade. | Charter §4.9, §9.4 | Confirmed |

---

## 2. Non-Functional Requirements

| ID | Requirement | Traceability | Status |
|---|---|---|---|
| NFR-01 | The acoustic tap mechanism shall strike each tile with consistent force, position, angle, and contact time to keep results repeatable across tiles. | Charter §6.2 | Confirmed |
| NFR-02 | Repeatability shall be validated by tapping/measuring the same tile multiple times and tracking variation in dominant frequency, ring duration, visual score, dimension values, and final grade. | Charter §22.3 | Confirmed |
| NFR-03 | The system shall track visual/acoustic/dimensional detection accuracy, false accept/reject rate, sorting accuracy, throughput (tiles/min), and uptime. | Charter §22.2 | Confirmed |
| NFR-04 | Any threshold or parameter calibrated on prototype/substitute hardware (e.g. laptop mic instead of the measurement mic + solenoid tapper) is provisional and must be explicitly labeled as such until recalibrated on the real acoustic station. | Charter §6.2, §15.2, §18.1; `.CLAUDE/CLAUDE.md` Development Rule 3 | Confirmed |
| NFR-05 | The machine shall provide an emergency stop, guarding around moving parts and the solenoid hammer, no exposed pinch points, and a lockout procedure for maintenance. | Charter §17 (Mechanical Safety) | Confirmed |
| NFR-06 | Electrical design shall include proper grounding, fuses/circuit breakers, isolated power supplies, a protected solenoid driver circuit, and an enclosure for electronics. | Charter §17 (Electrical Safety) | Confirmed |
| NFR-07 | The system shall stop automatically on tile jam, sensor failure, or sorting actuator failure, and shall have overcurrent protection on motors/solenoids. | Charter §17 (Operational Safety) | Confirmed |

---

## 3. Open Items

These are documented decisions still pending — do not treat as resolved:

- **FR-06** — whether dimensional measurement moves fully to camera-based methods, or keeps dedicated ToF/laser hardware for thickness/flatness alongside it.
- Encoder-based tile tracking (FR-12) is architecturally decided but not yet implemented or tested against a live conveyor.
- No acoustic threshold, camera defect model, or dimensional tolerance value in this system has been calibrated on production hardware yet (NFR-04) — see `.CLAUDE/CLAUDE.md` Known Technical Debt.

Track implementation status of these against `TODO.md`, not this document — this file is
the requirement, `TODO.md` is the work to satisfy it.
