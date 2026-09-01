# Pick-and-Place — Task & Test Checklist

Working checklist for building and bringing up the Cartesian gantry pick-and-place
station. Created 2026-09-01.

**Relationship to other docs:**

- Design / architecture / protocol: `documents/programming/pick_place_control_protocol.md`
- Wiring / pin table / power: `documents/electrical/schematics/pick_place_hardware_connections_plan.md`
- HMI / digital twin: `documents/project/Pick_Place_Sorting_Subsystem.md`
- This file = the granular "what to actually do and check", phase by phase. The
  repo-root `TODO.md` keeps only the one-line summary pointer; detail lives here.

**Legend:** `[ ]` to do · `[~]` in progress · `[x]` done · `[!]` blocked.
Tag owners as needed — **[FW]** firmware · **[SW]** Linux/Python · **[MECH]** mechanical/wiring.

> ⚠️ **Golden rules for this station**
> 1. **Never plug/unplug a stepper driver or a motor with power on** — it destroys the driver.
> 2. **Set every driver's current (Vref) before the first motor move.**
> 3. **Resolve the 3.3 V / 5 V logic-level question (§0.3) before the first power-on.**
> 4. **Motor power (12 V, LRS-150-12) is a separate supply from logic** — common ground at one point only.
> 5. Bench-test with the motors **off the gantry** first; only mount them once single-axis motion is proven.

---

## Phase 0 — Bench bring-up (motors on the bench, no gantry frame)

### 0.1 Hardware — known vs. still to confirm

**Known (2026-09-01):**

| Item | Spec |
|---|---|
| Board / MCU | Arduino UNO Q — MCU is **STM32U585 / Cortex-M33** running **Zephyr** (core `arduino:zephyr:unoq`), confirmed on the lab board 2026-09-01. Deploy via `arduino-app-cli app` (see §0.6). Classic Arduino API compiles. |
| Shield | CNC Shield V3.10 (Robu SKU 12202) — A4988/DRV8825 board, TMC2208 drops in |
| Motors ×4 | 42HM48-1684 NEMA 17 — 1.68 A/phase, 1.8°/step (200/rev), 4.4 kg·cm ≈ 0.43 N·m, bipolar 4-wire, round shaft |
| Drivers ×4 | TMC2208 — 1.4 A RMS continuous, 2 A peak, standalone stealthChop2 + microPlyer |
| Motor PSU | Mean Well **LRS-150-12** — 12 V, 12.5 A, 150 W, built-in OLP/OVP/OTP/SCP. Ample (need ~4.5 A). **Resolved.** |
| Microstep | **1/16** — TMC2208 = both MS jumpers closed (hardware doc §4a). **Decided.** |
| Vref target | **~1.6 V** (≈ 1.13 A on a 0.11 Ω module) — pending the Rsense check below |
| Logic levels | **Measured & resolved 2026-09-01 (§0.3):** MCU 3.3 V, shield V_IO 5 V, inputs safe as-is, STEP/DIR/EN driven direct at 3.3 V. No level shifter. |
| Cycle-speed note | 12 V (not 24 V) limits high-speed torque — keep `MAX_FEED` moderate; 24 V is a drop-in upgrade if needed |

**Still to confirm:**

- [ ] **TMC2208 Rsense** — resistors near the chip marked `R110` (0.11 Ω, standard → `I_RMS ≈ Vref × 0.71`, so 1.6 V ≈ 1.13 A, **OK**) or `R150` (0.15 Ω → 1.6 V ≈ 0.83 A, **too weak, raise it**). Recorded: Rsense = ______ Ω → target Vref = ______ V
- [ ] **A-axis clone-jumper positions** on the V3.10 (from its silkscreen / Robu page). Sketch: __________

### 0.2 Voltage & continuity checks (multimeter)

**Done 2026-09-01** (with the `pinprobe` app, §0.3): MCU output HIGH = **3.3 V**; shield "5V" pin = **5.0 V**; endstop pins D9/D10/D11 (pullup) = **3.3 V**; A0–A3 (pullup) = **3.3 V**.

**Still to do — needs the 12 V PSU wired (drivers still out):**

- [ ] **Shield VMOT** pin = ~12 V after wiring the LRS-150 to the shield's motor `+ / –` (PSU on, no drivers seated). Also covers "is the PSU output right". Actual: ______
- [ ] **Common ground**: continuity between LRS-150 GND and UNO Q GND, joined at **exactly one** point.
- [ ] **Inline fuse** (~8–10 A) fitted on the LRS-150 V+ to the shield. No destructive test — the LRS-150's own OLP/SCP is the backstop.

### 0.3 Logic-level: 3.3 V MCU vs 5 V shield — **measured 2026-09-01, mostly resolved**

Measured with the `pinprobe` app on the board (`~/ArduinoApps/pinprobe`, sketch holds
D2–D7 OUTPUT HIGH, D8 EN HIGH, D9–D11 + A0–A3 INPUT_PULLUP):

| Point | Reading | Meaning |
|---|---|---|
| D2 (MCU output HIGH) → GND | **3.3 V** | UNO Q / STM32U585 drives 3.3 V logic; inputs not 5 V-tolerant |
| Shield "5V" pin → GND | **5.0 V** | Driver V_IO rail is 5 V (from the UNO Q 5 V pin) |
| D9 (INPUT_PULLUP, no switch) → GND | **3.3 V** | Shield has **no 5 V pull-up** on the endstop lines |
| A3 (INPUT_PULLUP) → GND | **3.3 V** | Same for the analog / e-stop / vac-sense pins |

**Conclusions:**

- **MCU inputs (D9/D10/D11 endstops, A0 e-stop, A3 vac-sense): SAFE — no action.** They
  sit at 3.3 V on the internal pull-up; the shield does not drive them to 5 V. Wire NC
  switches signal→GND, use `INPUT_PULLUP`. No level shifter, no clamp.
- **MCU outputs (STEP/DIR/EN → TMC2208 at V_IO = 5 V): drive direct at 3.3 V.** 3.3 V vs
  the 3.5 V datasheet V_IH is marginal on paper but works in practice (Schmitt trigger
  trips ~2.5 V; 3.3 V boards run these drivers routinely).
  - [ ] **Empirical gate at §0.5:** first motor must step cleanly and reliably through
    direction changes over a 10-min run. If flaky → fallback: lift the shield's 5 V
    header pin and jumper the shield logic rail to the UNO Q **3V3** pin (reversible,
    everything then at 3.3 V), *or* a 74AHCT125 on STEP/DIR/EN.
- **Never wire the TMC2208 DIAG/INDEX pins to the MCU** (5 V outputs). Not used.

*(The `pinprobe` app can be removed once bring-up is done: `arduino-app-cli app destroy
user:pinprobe`. Restore the motor-test sketch with `arduino-app-cli app restart
user:cnc`.)*

### 0.4 Driver setup — Vref, microstepping, clone jumpers

- [ ] Set the **MS jumpers** for 1/16 on **all 4** drivers — TMC2208 table: **MS1 closed + MS2 closed = 1/16** (open/open = 1/8, *not* full step; see hardware doc §4a). Leave the A4988 MS3 row empty. Recorded: __________
- [ ] Fit the **A-socket clone jumpers** to clone **Y** (§0.1). Do **not** wire A_STEP/A_DIR in firmware. The teammate sketch's `A_STEP A6 / A_DIR A7` is wrong — not used.
- [ ] For **each** driver (X, Y, Z, A), with the driver seated, **motor disconnected**, VMOT present, EN as per module guidance:
  - [ ] Target Vref from §0.1 (≈ **1.6 V** for a 0.11 Ω module → ~1.13 A). Same for all four; A **must equal Y**. Target Vref: ______ V
  - [ ] Turn the trim pot, measuring Vref at the pot wiper / Vref test point with a multimeter (GND probe on a shield GND). Set to target.
  - [ ] **Y and A must be set identical.**
  - [ ] Label the driver with its axis (sticker / marker).
- [ ] Confirm PDN/UART pin is **left floating** on all drivers (standalone mode).
- [ ] Power down. Connect motors — **identify each coil pair first** with a continuity check (two wires that beep together = one coil); plug so A-pair and B-pair land on the socket's A1/A2 and B1/B2. Wrong pairing = grind/no-move.
- [ ] Power up. **Re-check Vref** on each (values shift once loaded).
- [ ] Run one motor for ~10 min; check **driver temperature** by hand / IR — too hot to touch → lower Vref or add heatsink/fan. Record: __________

### 0.5 First motion — single axis (start from the teammate's *tested* single-motor sketch)

*Deploy path (confirmed 2026-09-01):* the teammate's sketch is already on the board as
app `user:cnc` (`~/ArduinoApps/cnc/sketch/sketch.ino`). Edit it, then
`arduino-app-cli app restart ~/ArduinoApps/cnc -v` to rebuild + reflash (~90 s). Serial:
`arduino-app-cli monitor` (needs a real TTY). See §0.6.

- [ ] Flash the tested single-motor sketch (`app restart user:cnc`); confirm X motor turns forward/back.
- [ ] **Also confirms the 3.3 V STEP/DIR question (§0.3):** the motor must step cleanly and reliably through direction changes over the 10-min run. If it misses steps at 3.3 V drive → apply the §0.3 fallback (feed shield logic from 3V3, or a 74AHCT125).
- [ ] **Microstep / steps-per-rev sanity:** set `stepsPerRev = 200`, mark the shaft, run one "rev", measure actual degrees turned. `true_steps_per_rev = 200 × 360 / measured_degrees`. Record: __________  (confirms the MS jumpers do what you think).
- [ ] Direction check: does `X_DIR HIGH` move the axis the direction you'll call "+"? Note which way each axis needs: X ____ Y ____ Z ____
- [ ] Confirm the motor does **not** skip/stall at the intended `stepDelay` under a hand-applied load. Reduce speed or raise Vref if it does.

### 0.6 Firmware bring-up — `pick_place_node/sketch/sketch.ino`  **[FW]**

*Toolchain (confirmed 2026-09-01):* MCU runs **Zephyr** (`arduino:zephyr:unoq`), deployed
via `arduino-app-cli app` on the board over SSH — `app new <name>`, edit
`~/ArduinoApps/<name>/{sketch/sketch.ino, python/main.py}`, `app restart ~/ArduinoApps/<name> -v`
(compile + SWD flash + start the Python container, ~90 s). Classic Arduino API works.
MCU↔Linux bridge = `#include <Arduino_RouterBridge.h>` → `Bridge.provide("fn", cfunc)` on
the sketch, `Bridge.call("fn", args)` from Python (example on the board at
`/var/lib/arduino-app-cli/examples/platform_unoq/unoq-pin-toggle`). The `pinprobe` app
(`~/ArduinoApps/pinprobe`) is a working reference for a bare pin-driving sketch.

- [ ] **Prove a non-blocking accelerated stepper library builds & runs under Zephyr on the UNO Q.** Try in order: FastAccelStepper → AccelStepper → hand-written timer ISR → Zephyr-native stepper. *(Zephyr, not AVR/mbed — library compatibility is the open risk.)*
  - [ ] Library selected & compiles: __________
  - [ ] Test: one motor does a smooth accel → cruise → decel move **while** `loop()` still prints the limit-switch pin state every 100 ms (proves non-blocking).
- [ ] Decide the **project sketch home**: work in `~/ArduinoApps/<app>/sketch/` on the board vs. keep the source of truth in the repo's `pick_place_node/sketch/` and push. (Mirrors the acoustic node's unresolved App-Lab-vs-repo question.)
- [ ] Add **X, Y, Z** axes (Y drives its pins; A is cloned in hardware).
- [ ] Implement the **machine-state FSM**: `BOOT → NEEDS_HOMING → HOMING → IDLE → MOVING → JOGGING → PICKING → PLACING → ALARM`.
- [ ] Implement the **ASCII line protocol** parser over **USB serial** (see `pick_place_control_protocol.md` §5.2 / §5.3): `PING`, `MOVE`, `JOG`, `VAC`, `STOP`, `GETPOS`, `GETSTATE`, `HOME`, `CLEARALARM`, `SETENV`, `SETSPEED`.
  - [ ] Every command → immediate `ok <seq>` / `err <seq> <REASON>`.
  - [ ] Motion commands → later `done <seq> X.. Y.. Z..`.
  - [ ] `pos` stream at ~10–20 Hz while moving; `hb` at ~2 Hz always; `state` on change.
  - [ ] Malformed input → `err <seq> BAD_CMD`, no state change.
  - [ ] Second motion while one is active → `err <seq> BUSY`.
- [ ] Implement **`STOP`** (controlled decel, keeps position, no re-home needed).
- [ ] Implement **position tracking** (steps → mm using `STEPS_PER_MM_*`).

### 0.7 Homing, limits, soft limits  **[FW]**

- [ ] Wire the 3 limit switches (**NC to GND**, internal pull-up). Confirm each reads correctly when pressed and when the wire is unplugged (both = "triggered", fail-safe).
- [ ] Implement the **homing cycle** per axis: fast seek (`HOME_SEEK`) toward `HOME_DIR_*` → hit switch → back off `HOME_BACKOFF` → slow re-touch (`HOME_TOUCH`) → set axis zero. Order: **Z up first**, then X, then Y.
  - [ ] Test `HOME Z`, `HOME X`, `HOME Y`, `HOME ALL`. Each ends with `done` and `HOMED=1`.
- [ ] Implement **soft limits** from `SETENV`: any `MOVE` outside the envelope → `err OUT_OF_BOUNDS`, no motion. `MOVE` before homed → `err NOT_HOMED`.
- [ ] Implement **limit-hit-during-motion** → controlled stop + `alarm LIMIT_<axis>_<end>`; latched; requires `CLEARALARM` + re-home.
  - [ ] Test: start a long move, hand-trigger the limit switch mid-move → gantry stops, alarm emitted, further motion refused until cleared + re-homed.
- [ ] Implement **`SETSPEED`** (per-axis max feed + accel from config).

### 0.8 E-stop  **[MECH] + [FW]**

- [ ] Select an **e-stop contactor/relay** rated for the motor PSU, fail-open (coil de-energised = motor power cut).
- [ ] Wire: NC mushroom button → contactor coil path, so pressing it **physically removes VMOT**. Test: press → measure VMOT drops to 0.
- [ ] Wire a second NC contact of the button → `ESTOP_SENSE` (A0), `INPUT_PULLUP`. Test: press → pin reads LOW.
- [ ] Firmware: `ESTOP_SENSE` LOW → immediately `EN` HIGH (drivers off), alarm-pattern LED, latch `alarm ESTOP`.
  - [ ] Latch clears **only** on `CLEARALARM` **and** button released **and** a subsequent successful `HOME` — releasing the button alone must not resume motion.
- [ ] Test the full sequence: moving → hit e-stop → motion stops, power cut, alarm latched → release + `CLEARALARM` + `HOME` → back to `IDLE`.

### 0.9 Gripper / vacuum  **[FW] + [MECH]**

- [ ] Wire the vacuum pump/valve through a MOSFET or relay driver from **`VAC_EN` (D12)** — MCU does not drive the coil directly. Flyback diode across the coil.
- [ ] Wire the vacuum pressure sensor / switch to **`VAC_SENSE` (A3)**.
- [ ] Firmware: `VAC ON` energises the pump **and** waits up to `VACUUM_CONFIRM_MS` for `VAC_SENSE` to confirm grip; timeout → `err <seq> VACUUM_TIMEOUT`. `VAC OFF` releases.
  - [ ] Test with a hand over the cup (grip) and open air (timeout).
- [ ] Decide idle behaviour: pump off between cycles; confirm the cup releases the tile promptly (check-valve / vent).

### 0.10 Phase-0 acceptance test

- [ ] From a serial terminal: `HOME ALL` → `MOVE` to several points inside the envelope → `JOG` each axis → `VAC ON`/`VAC OFF` → `STOP` mid-move → trigger a limit → `CLEARALARM` + re-home. All behave per protocol. Log a full session transcript into `documents/programming/` as the bring-up record.

---

## Phase 1 — Linux side, against real firmware over USB serial

### 1.1 `pick_place/protocol.py` (pure)  **[SW/Claude]**

- [ ] Encode each command type → exact wire line.
- [ ] Parse each response/telemetry line → typed event (`Ok`, `Err`, `Done`, `Pos`, `State`, `Alarm`, `Heartbeat`, `Log`).
- [ ] `tests/test_pick_place_protocol.py`: round-trip every command; parse every response variant; malformed lines → a `Log`/`Unknown` event, never a crash.

### 1.2 `pick_place/coordinate_model.py` + `pick_place/sequencer.py` (pure)  **[SW/Claude]**

- [ ] `coordinate_model.py`: named-location table + stack state (`place_height(stack)`, `record_placed(stack)`), from `config.yaml`.
- [ ] `sequencer.py`: `plan_pick_place(source, dest, model) -> list[Command]` — safe-Z first, XY over source, descend, `VAC ON`, lift, XY over dest, descend to current stack height, `VAC OFF`, retract.
- [ ] `tests/test_pick_place_coordinate_model.py`: stack height increments by tile thickness each place; caps at `max_height`.
- [ ] `tests/test_pick_place_sequencer.py`: correct command list for each destination; retry-branch shape on a simulated `VACUUM_TIMEOUT`; never emits a move outside the envelope.

### 1.3 `pick_place/gantry_backend.py` — `SerialGantryBackend` (HW wrapper)  **[SW/Claude]**

- [ ] pyserial wrapper: write a line, read lines on a reader thread, publish parsed events on a queue. `GantryBackend` ABC so `bridge_backend.py` can slot in later.
- [ ] Smoke test against the real firmware: connect, `PING` → `ok`, `GETSTATE` → `state ...`.

### 1.4 `pick_place/job_runner.py`  **[SW/Claude]**

- [ ] Drive a `sequencer` plan against a `GantryBackend`: send one command, await its `done`/`err`, advance; on `err VACUUM_TIMEOUT` retry the pick once, else park at safe Z and report failure.
- [ ] `tests/test_pick_place_job_runner.py` against an in-memory fake backend: happy path; pick fails once then succeeds; pick fails twice → failure result + safe park; `alarm` mid-job → abort + surface.

### 1.5 `worker.py` + `config.yaml` integration  **[SW/Claude]**

- [ ] `GantryWorker` selects backend from `config.yaml` (`mock` | `serial` | `bridge`); publishes live pose into the existing `SharedState`.
- [ ] Existing Flask dashboard + digital twin now render the **real** gantry with no template changes.
- [ ] Extend `config.yaml`: `gantry` (backend, serial port, envelope, speed, safe_z, link_timeout), `locations`, `stacks` (see `pick_place_control_protocol.md` §8).
- [ ] Re-verify the serial `port` string on the target machine (same caveat as the camera `device_index`).

### 1.6 Teach-in & calibration (needs the assembled gantry)  **[MECH] + [SW]**

- [ ] Calibrate `STEPS_PER_MM_X/Y/Z`: command 100 mm, measure actual travel, scale. Repeat until within ~0.5 mm.
- [ ] Measure and set the real **soft-limit envelope** (jog to each mechanical extreme, minus a safety margin).
- [ ] Check for **backlash** on each axis (command +10 / −10, measure return error); note values, add compensation only if it matters.
- [ ] Set `HOME_DIR_*` and confirm switch positions give a sensible zero corner.
- [ ] Teach `pickup` and each stack/bin pose by jogging; write the numbers into `config.yaml > locations`.
- [ ] Tune `MAX_FEED_*` / `ACCEL_*`: raise until an axis skips under real load, back off ~30 %. Mark all as provisional (Development Rule 3).
- [ ] Set `stacks.tile_thickness_mm` from a real tile (**still blocked on SMTW data** — placeholder until then).

### 1.7 Phase-1 end-to-end  **[Together]**

- [ ] Feed the `Automation_Architecture.md` §5.6 example JSON through `job_runner` → firmware → gantry physically moves a real tile from `pickup` to the correct stack → completion response returned.
- [ ] Run 20+ consecutive cycles; check for lost steps (re-home, compare position), dropped tiles, vacuum misses. Log the results.

---

## Phase 2 — Bridge + master integration

- [ ] **[!]** Blocked on: App Bricks Python↔MCU **bridge API confirmed on the board** (`requirements.md` FR-22, same blocker as `acoustic_node/main.py`).
- [ ] `pick_place/bridge_backend.py` — `BridgeGantryBackend`, same `GantryBackend` interface as serial. Keep serial as the debug fallback.
- [ ] Wire `pick_place_node/python/main.py` for real: master command in (MQTT `machine/robot/command` or a local queue) → `job_runner` → backend → response + node heartbeat out.
- [ ] Integrate with the master + camera + acoustic pipeline: real fused grade → real destination → real placement.
- [ ] Node heartbeat to the master (`Automation_Architecture.md` §11); master raises an alarm on heartbeat loss.

---

## Cross-cutting / not blocked by the phases

### Mechanical  **[MECH]**

- [ ] Design & build the gantry frame — decide belt vs leadscrew per axis, motor torque, Z counterbalance or self-locking leadscrew (so Z can't drop when `EN` goes high).
- [ ] **Square the gantry beam at assembly** (A axis is hardware-cloned, no auto-square).
- [ ] Cable management: drag chain / energy chain for the moving axes, strain relief at every connector, motor leads away from signal/sensor wiring.
- [ ] Decide homing-switch type (mechanical / optical / inductive) and mounting.
- [ ] Design the pickup nest (fixed tile position at the conveyor end) and the grade stacks / reject bin layout.
- [ ] Gripper: vacuum pump vs venturi, cup diameter, single vs multi-cup. Blocked on SMTW tile size/weight.

### Data / decisions

- [ ] **[!] Get SMTW tile size + weight range.** Blocks: stack pitch, cup sizing, Z speed, whether a cup can lift the tile, motor torque. (`requirements.md` Open Items, repo `TODO.md`.)
- [ ] **Load cell** — go/no-go deferred; if yes, mount in the fixed nest (not the carriage), HX711 amp. May belong to the dimensional module instead (`charter` §6.3).
- [ ] Decide whether to drop `EN` at idle (motor heat vs Z-drop risk).
- [ ] Confirm the derated motor (~1.13 A → less than the nameplate 4.4 kg·cm) still has torque margin once gantry mass + tile weight are known. If short: 24 V PSU, spreadCycle via UART, or belt reduction.
- [ ] Revisit open-loop vs closed-loop steppers only if lost-step errors show up in the Phase-1 endurance run.

### Documentation (file in the same commit as the work — `.claude/CLAUDE.md` rule 4)

- [ ] Keep the pin table in `pick_place_hardware_connections_plan.md` §3 in sync with `sketch.ino` as pins are confirmed.
- [ ] Keep `pick_place_control_protocol.md` §5 in sync with the real parser as the protocol firms up.
- [ ] Update `Automation_Architecture.md` §5.6, `pick_place_node/README.md`, `.claude/CLAUDE.md` (Architecture + Known Technical Debt), and repo `TODO.md` once real code lands.
- [ ] Save the Phase-0 serial bring-up transcript and the Phase-1 endurance-run results under `documents/programming/`.
- [ ] Add a flowchart of the pick-place FSM + sequencer to `documents/programming/flowcharts/`.

---

## Open questions (resolve as you go)

- **Stepper library under Zephyr** on the UNO Q — unknown until task 0.6 (the core is
  Zephyr, not AVR/mbed — this is now the single biggest firmware risk).
- **A-clone jumper layout** on the V3.10 — unknown until confirmed from the board (§0.1).
- **TMC2208 Rsense** (R110 vs R150) — sets whether 1.6 V Vref is right (§0.1).
- **3.3 V STEP/DIR margin** — resolved in principle (drive direct); final confirmation is
  the first-motor run (§0.5).
- Sketch source-of-truth: on-board `~/ArduinoApps/` vs repo `pick_place_node/sketch/` (§0.6).
- Whether `MOVE` needs coordinated linear interpolation — current plan says **no**
  (point-to-point between taught poses with clearance is enough). Revisit only if a
  diagonal move near an obstacle is ever required.
- Master transport for Phase 1 (MQTT vs local queue) — `job_runner` must not care.

**Resolved 2026-09-01:** shield V_IO = 5 V; MCU = 3.3 V STM32U585/Zephyr, not 5 V-tolerant;
endstop/analog inputs safe as-is; App Lab deploy path (`arduino-app-cli app`) works;
RouterBridge API known; motor PSU = LRS-150-12 (ample).
