# Pick-and-Place Control Protocol & Motion-Control Plan

Design pass, 2026-08-31. Defines how the Cartesian gantry's **machine-control layer**
(`project_charter.md` §7.4, `Automation_Architecture.md` §5.6, `requirements.md` FR-14 /
FR-18) is split between the UNO Q's two brains and how the two halves talk to each other.
Prompted by the owner obtaining a **CNC Shield V3** (4× TMC2208 sockets, Uno form factor)
and two candidate test sketches from a teammate.

**Status: design only.** `pick_place_node/sketch/sketch.ino` is still an empty stub and
`pick_place_node/python/pick_place/` currently contains only the digital-twin mock
(`mock_state.py`, `worker.py`, `dashboard.py` — see
`documents/project/Pick_Place_Sorting_Subsystem.md`). Nothing in this document has been
run on hardware. Every pin number, timing constant, and speed here is a placeholder — see
Open Items. Companion hardware/wiring doc:
`documents/electrical/schematics/pick_place_hardware_connections_plan.md`.

---

## 1. Scope

This document covers **how the gantry moves a tile from A to B** — the control-system
architecture, the MCU↔Linux command protocol, and the build order. It does **not** cover
the mechanical gantry design, motor sizing, or the physical wiring (that's the companion
hardware doc), nor the HMI/digital-twin dashboard (that's
`Pick_Place_Sorting_Subsystem.md`).

The gantry is a **3-axis Cartesian stepper gantry** (X, Y, Z), with the Y beam driven by
**two motors** wired in parallel (see §4 and the hardware doc). It is **not** an
articulated arm. End effector: a vacuum cup (single on/off, not variable suction —
`Pick_Place_Sorting_Subsystem.md` §2).

---

## 2. Architecture decision — custom firmware, not GRBL

**Considered and rejected: flashing GRBL / grblHAL and streaming G-code from Linux.**

| Reason | Detail |
|---|---|
| No mainline MCU port | grblHAL has STM32F4/F7/H7 targets; the UNO Q's MCU is a Cortex-M33 (STM32U585 per Arduino's UNO Q spec — **verify on the board**). Porting GRBL to it is a project in itself. |
| Conflicts with an existing decision | `requirements.md` FR-18 and `project_charter.md` §7.4/§8.2 **explicitly** forbid an off-the-shelf CNC/G-code controller. The gantry takes semantic grade/slot commands from the master. |
| Bridge model mismatch | A raw G-code serial stream fights the App Bricks Python↔MCU bridge that the other nodes use. |

**Chosen: a small custom real-time motion controller** on the MCU sketch, using a proven
stepper-stepping library for the timing-critical part, plus a purpose-built line
protocol. This matches FR-18, the App Bricks node shape, and this repo's
"pure logic + thin hardware wrapper" rule (`.claude/CLAUDE.md` Development Rule 1).

---

## 3. Responsibility split

The dividing line: **anything that must not be late or interrupted lives on the MCU;
anything that can tolerate tens of milliseconds of latency lives on Linux.**

### 3.1 MCU / sketch side (STM32, `pick_place_node/sketch/sketch.ino`)

Hard real-time and safety. **Knows only axes, coordinates in mm, the gripper, and
safety state — nothing about tiles, grades, slots, or job order.**

| Responsibility | Notes |
|---|---|
| Step-pulse generation for X, Y, Z | With **trapezoidal acceleration**. Y and its second motor (A socket) get identical STEP/DIR — cloned in hardware (§4), so firmware drives 3 logical axes. |
| Homing cycle | Per axis: fast seek to limit switch → back off → slow re-touch → set that axis zero. Safe order: Z up first, then X, then Y. |
| Limit-switch monitoring | Always active, including mid-move. Trigger → controlled decel + latched `ALARM`; requires re-home to clear. |
| E-stop input | Immediate driver disable (EN high) + latched `ALARM`. Hardware also cuts motor PSU independently (hardware doc §5). |
| Soft limits | Once homed, reject or clip any target outside the configured envelope. |
| Position tracking | Steps → mm per axis. **Single source of truth** for gantry position. |
| Gripper / vacuum | Switch the output; read the vacuum-confirm sensor; `VACUUM_TIMEOUT` fault if confirmation not seen within a timeout. |
| Jog primitives | Move-axis-by-delta and continuous-jog-while-commanded. |
| Machine FSM | `BOOT → NEEDS_HOMING → HOMING → IDLE → MOVING → JOGGING → PICKING → PLACING → ALARM`. |
| Telemetry out | Live XYZ, state, vacuum flag, last fault, per-command acks, ~2 Hz heartbeat. |
| Link watchdog | Linux silent for > `LINK_TIMEOUT_MS` mid-motion → controlled stop + hold. |

### 3.2 Linux side (Qualcomm, `pick_place_node/python/pick_place/`)

Planning and orchestration. **Knows tiles, grades, slots, jobs, and bin geometry.**

| Responsibility | Notes |
|---|---|
| Coordinate model | Named locations (`pickup`, `grade_a_stack`, `grade_b_stack`, `reject_bin`, …) → XYZ. All in `config.yaml`. |
| Stack-height bookkeeping | Each place into a stack shifts the next drop Z by one tile thickness. Needs SMTW tile thickness — placeholder until then. |
| Pick-and-place sequencer | Expand `{tile_id, source, destination}` into an ordered move list (§5.4). |
| Move dispatch | Feed the MCU one motion at a time; wait for `done`/`err`; advance. Never more than one motion in flight. |
| Fault handling | Retry a failed pick once; else report failure to master, park at safe Z, await operator. |
| Master interface | Receive the semantic command (`Automation_Architecture.md` §5.6 JSON — MQTT `machine/robot/command` in the full architecture, a local queue for Phase 1); return the completion/failure response + node heartbeat. |
| HMI / digital twin | The existing Flask dashboard + `GantryWorker`/`SharedState`, now with a real backend alongside `MockGantrySimulator` (`Pick_Place_Sorting_Subsystem.md`). |
| Calibration helpers | steps/mm per axis, backlash, teach-in of named positions by jogging. |

---

## 4. Hardware context that shapes the protocol

Full detail in the hardware doc; the parts that affect this design:

- **CNC Shield V3, GRBL-standard pinout.** X: STEP D2 / DIR D5. Y: STEP D3 / DIR D6.
  Z: STEP D4 / DIR D7. Shared enable: D8 (active-LOW). Limits: D9 (X), D10 (Y), D11 (Z).
- **4th driver socket (A) is cloned in hardware**, not driven by code. Place the shield's
  Y→A clone jumpers so both Y-side motors receive the same STEP/DIR. This gives up
  independent auto-squaring — the gantry is squared mechanically at assembly. The
  teammate sketch's `#define A_STEP A6 / A_DIR A7` is **wrong** (those are analog-in only
  on a classic Uno) and is not used.
- **The teammate sketches are a starting point, not the design.** Both use blocking
  `delayMicroseconds` bit-bang loops with no acceleration, no homing, and no ability to
  react to a limit switch or e-stop mid-move. The tested single-motor sketch is a fine
  first bring-up step (task 0.3); the firmware work is making it non-blocking, ramped, and
  multi-axis.
- **TMC2208 in standalone mode** — microstepping set by MS1/MS2 jumpers, current by the
  Vref pot. Pick 1/16 and calibrate steps/mm against it.
- **The UNO Q MCU runs Zephyr** (`arduino:zephyr:unoq`), not classic Arduino. Sketches
  deploy via `arduino-app-cli app` (App Lab) — confirmed working on the board 2026-09-01
  (a `pinprobe` diagnostic app built, flashed the STM32U585, and ran). The classic
  Arduino API compiles. MCU↔Linux comms use `Arduino_RouterBridge.h` (`Bridge.begin()`,
  `Bridge.provide("name", fn)` on the sketch, `Bridge.call("name", args)` from Python) —
  see the `examples:unoq-pin-toggle` example on the board. This is the real bridge the
  §5.1 protocol runs over eventually (the ASCII framing still applies — it just travels
  as bridge calls instead of a UART).
- **Logic levels measured & resolved 2026-09-01** (hardware doc §6): MCU is 3.3 V; shield
  V_IO is 5 V; endstop/analog inputs are safe as-is; STEP/DIR/EN driven direct at 3.3 V
  with an empirical check at first motor bring-up. No level shifter planned.

---

## 5. The control protocol

### 5.1 Transport

**Line-based ASCII, one message per line (`\n`-terminated), sequence-numbered.**

Rationale: works over a plain USB-serial cable **now** (the App Bricks Python↔MCU bridge
is still unverified on this project's board — same blocker as `acoustic_node/main.py`),
and over the bridge later with the identical parser. Trivial to log, replay, diff, and
unit-test with synthetic strings (Development Rule 1). No binary framing, no checksums in
v1 (USB serial is reliable enough; add a CRC field later if the bridge proves noisy).

The Python side owns an abstract `GantryBackend` interface with two implementations —
`SerialGantryBackend` now, `BridgeGantryBackend` later — both emitting the same parsed
events, so nothing above the backend changes when the transport does.

### 5.2 Linux → MCU (commands)

Format: `<seq> <VERB> [args...]`, `seq` a monotonically increasing integer.

| Command | Meaning |
|---|---|
| `<seq> PING` | Liveness check. |
| `<seq> HOME ALL` / `HOME X` / `HOME Y` / `HOME Z` | Run the homing cycle. |
| `<seq> MOVE X<mm> Y<mm> Z<mm> F<mm_per_min>` | Absolute linear move. Any axis omitted holds. Rejected if not homed or target outside envelope. |
| `<seq> JOG <axis><±mm> F<mm_per_min>` | Relative single-axis move (jog button). |
| `<seq> VAC ON` / `VAC OFF` | Switch the vacuum cup; `VAC ON` waits for the confirm sensor. |
| `<seq> STOP` | Controlled decel to a stop and hold. Does **not** clear position or require re-home. |
| `<seq> GETPOS` | Request an immediate `pos` line. |
| `<seq> GETSTATE` | Request an immediate `state` line. |
| `<seq> CLEARALARM` | Clear a latched `ALARM` (only succeeds if the cause is gone; still requires a subsequent `HOME`). |
| `<seq> SETENV X<min> X<max> Y<min> Y<max> Z<min> Z<max>` | Push the soft-limit envelope from `config.yaml` at startup. |
| `<seq> SETSPEED <axis> MAX<mm_per_min> ACC<mm_per_s2>` | Push per-axis speed/accel limits from config. |

### 5.3 MCU → Linux (responses and async telemetry)

| Line | Meaning |
|---|---|
| `ok <seq>` | Command accepted and started. |
| `err <seq> <REASON>` | Command rejected or failed. Reasons: `BAD_CMD`, `NOT_HOMED`, `OUT_OF_BOUNDS`, `BUSY`, `VACUUM_TIMEOUT`, `ALARM_ACTIVE`. |
| `done <seq> X<mm> Y<mm> Z<mm>` | A motion command (`MOVE`/`JOG`/`HOME`) finished; final position given. |
| `pos X<mm> Y<mm> Z<mm>` | Position report. ~10–20 Hz while moving, on request otherwise. |
| `state <FSM> VAC=<0/1> HOMED=<0/1>` | Machine-state report. On change and on request. |
| `alarm <REASON>` | Latched fault: `LIMIT_X_MIN`, `LIMIT_X_MAX`, `LIMIT_Y_*`, `LIMIT_Z_*`, `ESTOP`, `LINK_TIMEOUT`. All motion stopped, drivers may be disabled. |
| `hb <uptime_ms>` | Heartbeat, ~2 Hz, always. |
| `log <text>` | Free-text debug (ignored by the sequencer, shown in the HMI event log). |

### 5.4 Protocol rules

1. **Every command** gets an immediate `ok <seq>` or `err <seq> <REASON>`.
2. **Motion commands** additionally get a later `done <seq> …` (or an `err`/`alarm`).
3. **One motion in flight at a time.** Linux sends the next motion only after `done`.
   A motion command arriving while one is active → `err <seq> BUSY`.
4. **`STOP` and `CLEARALARM` are always accepted**, even mid-motion.
5. **Any `alarm` latches.** It clears only on `CLEARALARM` *and* a successful subsequent
   `HOME`. The e-stop latch additionally requires the physical loop to be reclosed.
6. **Unknown or malformed command** → `err <seq> BAD_CMD`, no state change.
7. **Watchdog:** no command (or `PING`) for `LINK_TIMEOUT_MS` while moving → the MCU
   raises `alarm LINK_TIMEOUT` and stops. Linux sends `PING` at ~1 Hz when idle.

### 5.5 Example: one pick-and-place cycle

Master → Linux node:
```json
{ "command_id": "CMD000042", "tile_id": "T000042",
  "action": "pick_place", "source": "pickup_position", "destination": "grade_B_stack" }
```

Linux sequencer → MCU (each line sent only after the previous `done`):
```
101 MOVE Z200.0 F4000                 ; raise to safe travel height
102 MOVE X500.0 Y100.0 F8000          ; XY over the pickup point
103 MOVE Z18.0 F2000                  ; descend to the tile
104 VAC ON                            ; grip; waits for confirm sensor
105 MOVE Z200.0 F3000                 ; lift
106 MOVE X100.0 Y350.0 F8000          ; XY over grade-B stack
107 MOVE Z42.0 F2000                  ; descend to current stack-top height
108 VAC OFF                           ; release
109 MOVE Z200.0 F3000                 ; retract
```
On `done 109 …`, Linux increments the grade-B stack height, then replies to the master:
```json
{ "command_id": "CMD000042", "tile_id": "T000042", "status": "completed" }
```
On `err 104 VACUUM_TIMEOUT`: retry the pick once (re-issue 103–104); if it fails again,
park at safe Z and reply `{ "status": "failed", "reason": "pick_failed" }`.

---

## 6. Python module plan

All new pure modules follow Development Rule 1 — hardware I/O isolated, logic
synthetic-input-testable. Under `pick_place_node/python/pick_place/`:

| Module | Kind | Responsibility | Tested |
|---|---|---|---|
| `protocol.py` | pure | Encode commands → lines; parse lines → typed events (`Ok`, `Err`, `Done`, `Pos`, `State`, `Alarm`, `Heartbeat`, `Log`). No I/O. | `tests/test_pick_place_protocol.py`, synthetic strings |
| `sequencer.py` | pure | `plan_pick_place(source, dest, coord_model, stacks) -> list[Command]`. Includes safe-Z ordering, stack-height math, retry-branch shape. No I/O. | `tests/test_pick_place_sequencer.py`, synthetic locations |
| `coordinate_model.py` | pure | Named-location table + stack state (`place_height(stack)`, `record_placed(stack)`), loaded from `config.yaml`. | `tests/test_pick_place_coordinate_model.py` |
| `gantry_backend.py` | HW wrapper | `GantryBackend` ABC + `SerialGantryBackend` (pyserial: write line, read lines, surface parsed events on a queue). Mirrors `camera/capture.py`. | smoke test only |
| `bridge_backend.py` | HW wrapper | `BridgeGantryBackend` — same interface, App Bricks bridge transport. Added in Phase 2. | smoke test only |
| `worker.py` (extend) | HW wrapper | `GantryWorker` gains a real backend path alongside `MockGantrySimulator`, selected by `config.yaml`. Publishes into the existing `SharedState`. | not unit-tested |
| `job_runner.py` | pure-ish | Drives a `sequencer` plan against a `GantryBackend`: send, await `done`/`err`, advance, handle retry/park. Logic testable against a fake in-memory backend. | `tests/test_pick_place_job_runner.py` with a fake backend |

`main.py` (App Lab entry point) stays a stub until Phase 2 task 2.2.

---

## 7. Execution plan — phased, who does what

Role tags: **[FW]** firmware owner · **[SW]** Linux/Python owner · **[MECH]**
mechanical/wiring owner · **[Claude]** assistant-written code · **[Together]** needs the
assembled gantry + the team. Map the tags to the 3-person team as suits;
`[SW]` is expected to be the owner.

### Phase 0 — Bench bring-up (motors on the bench, no gantry frame)

| # | Task | Owner |
|---|---|---|
| 0.1 | Set each TMC2208 Vref for its motor's rated current; set MS1/MS2 jumpers to 1/16; fit the Y→A clone jumpers; wire a separate 12–24 V motor PSU with a single common ground to the board; wire the 3 limit switches (NC to GND); wire a hardware e-stop that cuts the motor PSU **and** pulls an MCU pin low. (Hardware doc §3–§5.) | [MECH] |
| 0.2 | **Verify logic levels** — confirm whether the shield ties VIO to 5 V while the STM32 drives 3.3 V, and whether the STM32 endstop inputs are 5 V-tolerant. Add a level shifter or feed shield logic from 3.3 V if needed. **Do before applying power.** | [MECH] |
| 0.3 | **Prove a non-blocking, accelerated stepper library builds and runs on the UNO Q Arduino core** — try FastAccelStepper → AccelStepper → hand-written timer ISR, in that order. *Highest-risk item; do it first.* Deliverable: one motor doing a smooth accel/decel move while `loop()` still prints limit-switch state every 100 ms. Start from the teammate's tested single-motor sketch. | [FW] + [Claude] |
| 0.4 | Add X/Y/Z, the machine FSM, and the §5.2/§5.3 ASCII parser over **USB serial**. Bench-test from a laptop serial terminal (`MOVE`, `GETPOS`, `STOP`). | [FW] + [Claude] |
| 0.5 | Homing cycle for all 3 axes + soft-limit envelope (`SETENV`) + e-stop/limit → `ALARM` path. Test each limit by hand-triggering the switch mid-move. | [FW] + [Claude] |
| 0.6 | Vacuum output + confirm-sensor read + `VACUUM_TIMEOUT` fault. | [FW] |

### Phase 1 — Linux side against real firmware over USB serial

| # | Task | Owner |
|---|---|---|
| 1.1 | `protocol.py` + unit tests. | [Claude] |
| 1.2 | `coordinate_model.py` + `sequencer.py` + unit tests (incl. stack-height increment, retry branch). | [Claude] |
| 1.3 | `gantry_backend.py` — `SerialGantryBackend`. | [Claude] |
| 1.4 | `job_runner.py` + tests against a fake backend. | [Claude] |
| 1.5 | Extend `GantryWorker`/`config.yaml` — real backend selectable; dashboard + digital twin now visualize the real gantry. | [Claude] |
| 1.6 | Extend `config.yaml`: named locations, stack pitch (placeholder until SMTW data), safe Z, per-axis speed/accel, envelope, serial port. | [Claude] + [SW] |
| 1.7 | Teach-in: jog to the pickup point and each stack, capture positions into config. *Needs the assembled gantry.* | [Together] |
| 1.8 | End-to-end dry runs — feed the `Automation_Architecture.md` §5.6 example JSON through `job_runner` → firmware → real tile moved to the right stack → response back. | [Together] |

### Phase 2 — Bridge + master integration

| # | Task | Owner |
|---|---|---|
| 2.1 | Once the App Bricks Python↔MCU bridge API is confirmed on the board (same blocker as `acoustic_node/main.py` / `requirements.md` FR-22), add `bridge_backend.py` as a drop-in `GantryBackend`. Keep serial as the debug fallback. | [Claude] |
| 2.2 | Wire `pick_place_node/python/main.py` for real: master command in (MQTT `machine/robot/command` or local queue) → `job_runner` → backend → response + node heartbeat out. | [Claude] + [SW] |
| 2.3 | Integrate with the camera/acoustic/master pipeline: real fused grade → real slot → real placement. | [Together] |

### Cross-cutting (not blocked by the phases)

- **[MECH]** Square the gantry beam at assembly (A axis is hardware-cloned, not
  auto-squared). Fix homing-switch positions and measured travel limits → feeds
  `config.yaml` and the firmware envelope. Resolves `TODO.md` "Decide pick-and-place axis
  count, motor/gripper hardware, and gantry travel limits".
- **[All]** **Get SMTW tile size + weight now.** Still blocks stack pitch, vacuum-cup
  sizing, Z speeds, and whether a cup can lift the tile at all. (`TODO.md`,
  `requirements.md` Open Items.)
- **Docs**, filed in the same commits (`.claude/CLAUDE.md` rule 4): update this file as
  the protocol firms up; keep the hardware doc's pin table in sync with `sketch.ino`;
  update `Automation_Architecture.md` §5.6, `pick_place_node/README.md`,
  `.claude/CLAUDE.md` Architecture, and `TODO.md` once code lands.

---

## 8. Config additions (`pick_place_node/python/pick_place/config.yaml`)

Sketch of the new sections (values are placeholders):

```yaml
gantry:
  backend: "mock"            # "mock" | "serial" | "bridge"
  serial:
    port: "COM7"             # re-verify per machine, same caveat as camera device_index
    baud: 115200
  envelope_mm:               # pushed to the MCU via SETENV; real values after teach-in
    x: {min: 0, max: 600}
    y: {min: 0, max: 400}
    z: {min: 0, max: 200}
  speed:                     # pushed via SETSPEED; provisional until calibrated
    x: {max_mm_min: 8000, accel_mm_s2: 400}
    y: {max_mm_min: 8000, accel_mm_s2: 400}
    z: {max_mm_min: 4000, accel_mm_s2: 300}
  safe_z_mm: 200
  link_timeout_ms: 500

locations:                   # captured by teach-in (task 1.7)
  pickup:         {x: 500, y: 100, z: 18}
  grade_a_stack:  {x: 100, y: 100}
  grade_b_stack:  {x: 100, y: 350}
  reject_bin:     {x: 550, y: 350}

stacks:
  tile_thickness_mm: 12      # PLACEHOLDER — blocked on SMTW data
  base_z_mm: 20
  max_height: 20
```

---

## 9. Open items

- **Stepper library support on the UNO Q Arduino core is unconfirmed** (task 0.3). If
  neither FastAccelStepper nor AccelStepper works, the fallback is a hand-written timer
  ISR running a multi-axis DDA/Bresenham stepper — more work, so resolve this early.
- **Logic-level compatibility** between the 5 V shield and 3.3 V STM32 I/O is unverified
  (task 0.2, hardware doc §6).
- **App Bricks bridge API** for Python↔MCU messaging is still unverified on this board
  (`requirements.md` FR-22) — Phase 1 deliberately uses USB serial to avoid being blocked
  on it.
- **No coordinated multi-axis (true linear) interpolation in v1** — `MOVE` may move axes
  independently to the target as long as each respects its own accel limit. For
  pick-and-place (Z up, XY over, Z down) that's acceptable. Revisit if diagonal moves
  near obstacles are ever needed.
- **steps/mm, backlash, homing offsets, and all speeds/accels** are placeholders until
  calibrated on the built gantry (Development Rule 3 — provisional-until-recalibrated).
- **Master transport** (MQTT vs local queue) for Phase 1 is not fixed — `job_runner`
  should not care; keep the master adapter thin.
- **SMTW tile size/weight** — blocks stack pitch, cup sizing, Z speed. Unchanged blocker.

---

## Cross-references

- Decision record: `documents/project/project_charter.md` §7.4 / §8.2,
  `documents/project/Automation_Architecture.md` §5.6,
  `documents/requirements/requirements.md` FR-14 / FR-18.
- Hardware / wiring: `documents/electrical/schematics/pick_place_hardware_connections_plan.md`.
- HMI / digital twin: `documents/project/Pick_Place_Sorting_Subsystem.md`.
- Node code: `pick_place_node/` (`sketch/sketch.ino` MCU, `python/pick_place/` Linux).
- App Bricks / bridge caveat: `acoustic_node/README.md`, `acoustic_node/python/main.py`.
- Pattern references in-repo: `acoustic_node/python/acoustic/tap_sequencer.py` (pure FSM
  mirroring a sketch), `camera_node/python/camera/capture.py` / `worker.py` (HW wrapper +
  `SharedState`).
