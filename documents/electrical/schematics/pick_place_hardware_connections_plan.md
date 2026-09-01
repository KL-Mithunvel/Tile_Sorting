# Pick-and-Place Station — Hardware Connections Plan

Design pass, 2026-08-31. Wiring and power plan for the Cartesian gantry's motion
hardware, built around a **CNC Shield V3** (Uno form factor, 4× TMC2208 sockets) that the
owner already has, mounted on the **Arduino UNO Q** (`Automation_Architecture.md` §5.6,
`requirements.md` FR-14 / FR-18).

**Status: design only — nothing wired or tested.** The gantry frame does not exist yet;
axis travel, motor sizes, and the gripper are not finalized (`TODO.md`). Every pin
number, current setting, and timing/speed value here is a **placeholder** — see
§9 Open Items. This mirrors `acoustic_station_wiring.md`'s role for the acoustic station.

Control-side architecture, command protocol, and build order:
`documents/programming/pick_place_control_protocol.md` (read that first for the
MCU-vs-Linux responsibility split and *why not GRBL*).

**Placement note:** filed under `documents/electrical/schematics/` per `.claude/CLAUDE.md`
rule 4 (wiring/connection docs belong here, alongside `acoustic_station_wiring.md`), not
under `documents/programming/`.

---

## 1. Overview

```text
   Linux side (Qualcomm)                 MCU side (STM32, Arduino headers)
   ─────────────────────                 ────────────────────────────────
   pick_place_node/python/               pick_place_node/sketch/sketch.ino
     motion planning, coordinate           step generation + accel,
     model, sequencer, HMI     ── link ──► homing, limits, e-stop,
                                           soft limits, gripper, FSM
                                              │
                                              ▼
                                   ┌────────────────────────┐
                                   │   CNC Shield V3         │
                                   │   4× TMC2208 (standalone)│
                                   └───────────┬────────────┘
                                               │ STEP / DIR / EN
                              ┌────────────┬────┴───┬────────────┐
                              ▼            ▼        ▼            ▼
                          X motor     Y motor   Y' motor     Z motor
                                      (Y beam, 2 motors)   (vertical)
```

- **3 logical axes** (X, Y, Z). The Y beam is driven by **two motors** — the second one
  sits in the shield's A socket and is **cloned from Y in hardware** (§4), so firmware
  only ever drives 3 STEP/DIR pairs.
- **Open-loop steppers.** Position reference comes from **homing switches**; travel is
  bounded by **limit switches**. No encoders in v1 (revisit only if missed steps become a
  real problem — `pick_place_control_protocol.md` §9).
- **End effector: vacuum cup**, single on/off. Pick success confirmed by a **vacuum
  pressure/flow sensor** (primary). Optional load cell for per-tile weight — see §7.

---

## 2. Bill of Materials

| Component | Notes |
|---|---|
| Controller | Arduino UNO Q (existing lab board). MCU side = STM32U585 per Arduino's UNO Q spec — **verify against the board**. |
| Motion shield | **CNC Shield V3.10** (existing, Robu SKU 12202 — A4988/DRV8825 board, TMC2208 is pin-compatible). GRBL 0.9 pinout, 4 driver sockets, shared ENABLE, 6 endstop headers, spindle/coolant headers. A-axis: on-board jumpers to clone X/Y/Z, **or** a true 4th axis on **D12/D13** with custom firmware (we use the clone — §4). Ref: robu.in SKU 12202; cirkitdesigner.com component `2c4686b3-…`. |
| Stepper drivers | 4× **TMC2208** (existing) in **standalone mode** — microstepping by the MS1/MS2 jumpers (TMC2208 table, **not** A4988 — §4a), current by the onboard Vref pot, PDN/UART pin left floating. Datasheet: 1.4 A RMS continuous, 2 A peak, V_M 4.75–36 V, logic 3–5 V, 256 µsteps via microPlyer. Standalone = **stealthChop2 only** (spreadCycle needs UART — future upgrade if high-speed torque is short). |
| Stepper motors | 4× **42HM48-1684** (JK42HM48-1684) NEMA 17 round-shaft — 1× X, 2× Y, 1× Z. **1.68 A/phase**, 1.8°/step (200 full steps/rev), bipolar 4-wire, **4.4 kg·cm ≈ 0.43 N·m** holding torque, ±2 % dims/weight. Note the motor's 1.68 A **exceeds the TMC2208's 1.4 A continuous** — it is run derated (§5, §8). |
| Motor PSU | **Mean Well LRS-150-12** (owner has it) — 12 V, 12.5 A, 150 W enclosed switcher, built-in OLP (hiccup) / OVP / OTP / SCP. Well within the TMC2208 range (4.75–36 V); 12.5 A is ~3× the ~4.5 A the four drivers draw. Separate supply — **not** the UNO Q's 5 V rail (`project_charter.md` §8.2, §17). Inline ~8–10 A fuse on V+ (NFR-06); no separate breaker needed given the PSU's own OLP/SCP. *Trade-off: 12 V gives less high-speed torque headroom than 24 V — keep `MAX_FEED` conservative; a 24 V PSU or spreadCycle-via-UART is the drop-in upgrade if an axis skips only at speed (§9).* |
| Limit switches | 3× mechanical microswitch (one per axis), wired **normally-closed to GND** (fail-safe: a broken wire reads as triggered). Internal pull-up on the MCU input. Optionally 6 (min + max per axis). |
| E-stop | Latching mushroom button, **normally-closed**. Breaks the motor-PSU feed via a contactor/relay **and** pulls an MCU input low (§5). |
| Vacuum generator | Small 12 V diaphragm vacuum pump **or** a compressed-air venturi + solenoid valve, switched by a MOSFET/relay from one MCU output. Cup size TBD (blocked on SMTW tile size/weight). |
| Vacuum sensor | Analog vacuum pressure sensor or a pressure switch on the cup line → one MCU input. Confirms grip before the gantry lifts. |
| Load cell (optional) | See §7 — HX711 amp + cell, only if per-tile weighing is wanted. Not in the v1 critical path. |
| Level shifter | 1 channel min, **only if** §6 shows the 5 V shield / 3.3 V MCU mismatch needs it. |
| Misc | Screw terminals, flyback diode across the vacuum-pump/solenoid coil, drag chain for the moving-axis wiring, ferrite beads on motor leads. |

---

## 3. Pin Table (Arduino UNO Q, Uno-shaped header — CNC Shield V3 fixed mapping)

The CNC Shield V3 hard-wires most of these; they are **not reassignable** without cutting
traces. **Not yet verified against the physical UNO Q silkscreen or how App Lab names
GPIO** — same caveat as every other unverified App Lab item in this repo
(`acoustic_node/README.md`).

| Pin | Signal | Direction | Connects to | Notes |
|---|---|---|---|---|
| D2 | `X_STEP` | output | X driver STEP | Shield-fixed. |
| D5 | `X_DIR` | output | X driver DIR | Shield-fixed. |
| D3 | `Y_STEP` | output | Y driver STEP | Shield-fixed. Also feeds the A socket via clone jumper (§4). |
| D6 | `Y_DIR` | output | Y driver DIR | Shield-fixed. Also feeds A via clone jumper. |
| D4 | `Z_STEP` | output | Z driver STEP | Shield-fixed. |
| D7 | `Z_DIR` | output | Z driver DIR | Shield-fixed. |
| D8 | `EN` | output | all 4 drivers ENABLE | Shield-fixed, **active-LOW**, shared. Drive HIGH to disable all (used on e-stop/alarm). |
| D9 | `LIM_X` | input, `INPUT_PULLUP` | X limit switch (NC→GND) | Shield endstop header. LOW = triggered / wire broken. |
| D10 | `LIM_Y` | input, `INPUT_PULLUP` | Y limit switch (NC→GND) | Shield endstop header. |
| D11 | `LIM_Z` | input, `INPUT_PULLUP` | Z limit switch (NC→GND) | Shield endstop header. |
| D12 | `VAC_EN` | output → MOSFET/relay gate | Vacuum pump / valve driver | Shield "SpinEnable" pin, repurposed. Switches the pump through an external driver — the MCU does **not** drive the coil directly (`project_charter.md` §8.2). |
| D13 | `STATUS_LED` | output | Onboard LED | On = running, off/blink = `ALARM`. Shield "SpinDir" pin — free here. |
| A0 | `ESTOP_SENSE` | input, `INPUT_PULLUP` | E-stop loop (NC) | Shield "Abort" pin. LOW = loop open = e-stop tripped. Interrupt-capable. **This is only the MCU's awareness of the e-stop — the loop also physically breaks motor power (§5).** |
| A1 | *(reserved)* | — | — | Shield "Hold" pin — spare (candidate: cycle-start / pause button). |
| A2 | *(reserved)* | — | — | Shield "Resume" pin — spare. |
| A3 | `VAC_SENSE` | input (analog) | Vacuum pressure sensor / switch | Shield "CoolEn" pin, repurposed. Read before lifting; `VACUUM_TIMEOUT` fault if grip not confirmed. |
| A4 / A5 | I2C SDA / SCL | — | *(free)* | Not used by the shield. Available if a load cell ADC or an I2C sensor is added later. |
| D0 / D1 | UART RX / TX | — | reserved | Do not use — MCU↔Linux / debug serial. |

**A6 / A7 are NOT used.** The teammate draft sketch's `#define A_STEP A6 / A_DIR A7` is
wrong (analog-input-only on a classic Uno; unreliable to assume otherwise on the UNO Q).
The 4th motor is handled by hardware cloning instead — §4.

---

## 4. The second Y motor (A socket) — hardware clone, not code

The CNC Shield V3 has jumper positions beside the A socket that **parallel A's STEP/DIR
onto another axis's signals**. Fit the jumpers to clone **Y**:

```text
   Y_STEP (D3) ──┬──► Y driver STEP
                 └──► A driver STEP     (via A-clone jumper)
   Y_DIR  (D6) ──┬──► Y driver DIR
                 └──► A driver DIR      (via A-clone jumper)
```

- Both Y-beam motors then step **in perfect lockstep**, always — there is no software
  path that can desync them (this is the failure the teammate's `stepMotorPair()` was
  working around; hardware cloning removes the problem entirely).
- **Trade-off: no independent auto-squaring.** The gantry is squared **mechanically at
  assembly** and stays square. Acceptable for a lab PoC. If auto-square homing is ever
  needed, that requires driving A from its own pins (D12/D13, giving up the vacuum pin) or
  a different controller — a later decision, not now.
- Set the A driver's Vref and microstepping **identical** to the Y driver.
- If the two Y motors face opposite directions on the beam, flip one motor's coil-pair
  wiring (swap one pair at the connector), **not** a DIR pin — there's only one Y DIR.

---

## 4a. TMC2208 microstepping & current — this shield is an A4988 board

The CNC Shield V3.10 was designed for A4988/DRV8825. TMC2208 modules drop in
(same StepStick footprint) but **two things differ** — get these right before any move.

### Microstepping — TMC2208 table, not A4988

The shield exposes three jumper rows per socket (A4988 MS1/MS2/MS3). TMC2208 uses only
**MS1 and MS2**, with a **counter-intuitive table**:

| MS2 jumper | MS1 jumper | Microstep |
|---|---|---|
| open | open | **1/8** ← default, *not* full step |
| open | closed | 1/2 |
| closed | open | 1/4 |
| **closed** | **closed** | **1/16** ← plan default |

- For the planned **1/16, fit both MS jumpers**. Leave the third row (A4988 MS3) empty —
  on a TMC2208 that pin is not a microstep select.
- microPlyer always interpolates to 1/256 internally regardless, for smoothness.
- **Always verify** with the physical turn test: set `stepsPerRev = 200`, mark the shaft,
  command one "rev", measure actual degrees, `true_steps_per_rev = 200 × 360 / measured`
  (checklist §0.5). Do not trust the jumper table blind.

### Current (Vref)

Standalone TMC2208 sets phase current by the trim pot. For a standard SilentStepStick
(**Rsense = 0.11 Ω**) the community formula is:

```
I_RMS ≈ Vref × 0.71            (Vref ≈ I_RMS × 1.41)
```

- **Motor:** 42HM48-1684, rated **1.68 A/phase**. **Driver:** TMC2208, **1.4 A RMS
  continuous** — so the motor cannot be run at its rated current on this driver; it is
  run derated.
- **Starting point: Vref ≈ 1.6 V → ~1.13 A RMS** (~67 % of motor rating, safely under the
  driver's continuous limit). This matches the teammate's calculation for a standard
  0.11 Ω module.
- **Before trusting 1.6 V:** confirm the module's **Rsense** (0.11 Ω standard; some clones
  are 0.15 Ω, where the same Vref gives only ~0.83 A — likely too weak). Check the
  resistors near the chip (`R110` vs `R150`) or the vendor page.
- Headroom to raise toward **~1.8–1.9 V (~1.3 A)** if an axis skips under load — but
  watch driver + motor temperature (heatsink fitted; add airflow if hot to touch after
  10–15 min). See checklist §0.4.
- Set Vref with the **motor disconnected**, VMOT present, probing the pot wiper / Vref
  pad against a shield GND. Set X, Y, Z, **and A identically to Y**.

All values provisional until confirmed on the built gantry (`.claude/CLAUDE.md`
Development Rule 3).

---

## 5. Power architecture & E-stop

```text
   ┌─ Mains ─┬──────────────► UNO Q PSU (USB-C / 5 V logic)
             │
             └─► LRS-150-12 (12 V 12.5 A) ─[8–10 A FUSE]─► E-STOP contactor ─► CNC Shield VMOT
                 (24 V is the upgrade path)
                                                       ▲
                                                       │ NC mushroom button
                                                       │  (opens → contactor drops →
                                                       │   motor power cut)
                                                       │
                          also: one NC contact ────────┴──► A0 ESTOP_SENSE (MCU input)

   Common ground: motor-PSU GND ── tied to ── UNO Q GND   (single point, near the shield)
```

**E-stop behaviour (two independent layers):**

1. **Hardware:** the NC button breaks the contactor coil → motor power (VMOT) is
   physically removed. Motion stops regardless of firmware state. This is the layer that
   must always work.
2. **Firmware:** `ESTOP_SENSE` (A0) goes LOW → the sketch immediately drives `EN` HIGH
   (disable drivers), sets `STATUS_LED` to the alarm pattern, latches `ALARM ESTOP`, and
   emits `alarm ESTOP` on the link. The latch clears **only** on `CLEARALARM` *and* the
   physical loop being reclosed *and* a subsequent successful `HOME` — reclosing the
   button alone does not resume motion (`project_charter.md` §17, lockout).

**Other power rules:**

- Logic (UNO Q, shield logic rail, MOSFET gate, sensors) and motor power are **separate
  supplies**, joined only at the single common-ground point (`project_charter.md` §8.2,
  §17).
- Flyback diode across the vacuum pump/solenoid coil (cathode to V+), protecting the
  switching MOSFET.
- Set each TMC2208 Vref for its motor's rated **RMS** phase current. For common TMC2208
  modules `I_rms ≈ Vref / 1.41 × (√2)` — **use the specific module's own formula/table**,
  they vary. Start low, raise only if the motor stalls under load; watch driver temp.
- The Z axis holds the load against gravity — do **not** disable Z's driver at idle
  unless the axis is mechanically self-locking (leadscrew) or the head is parked down.

---

## 6. Logic-level compatibility — measured 2026-09-01

The CNC Shield V3.10 was designed for a 5 V ATmega Uno; the UNO Q / STM32U585 drives
**3.3 V** logic and its inputs are not 5 V-tolerant. Bench measurements with the
`pinprobe` sketch on the board:

| Point | Reading | |
|---|---|---|
| MCU digital output HIGH (D2) | **3.3 V** | confirms 3.3 V logic |
| Shield "5V" pin | **5.0 V** | driver V_IO rail is 5 V, from the UNO Q 5 V pin |
| Endstop pins D9/D10/D11, `INPUT_PULLUP`, no switch | **3.3 V** | shield puts **no 5 V pull-up** on these lines |
| A0–A3, `INPUT_PULLUP` | **3.3 V** | same |

**Resolution:**

| Path | Status |
|---|---|
| **MCU inputs** — endstops (D9/D10/D11), e-stop (A0), vac-sense (A3) | **Safe, no action.** Sit at 3.3 V on the internal pull-up; nothing on the shield drives them to 5 V. Wire NC switches signal→GND with `INPUT_PULLUP`. |
| **MCU outputs** — STEP/DIR/EN into TMC2208 at V_IO = 5 V | **Drive direct at 3.3 V.** 3.3 V vs the 3.5 V datasheet V_IH (0.7 × 5 V) is marginal on paper but works in practice (Schmitt trigger trips ~V_IO/2). **Verify empirically** at first motor bring-up (todo §0.5). Fallback if flaky: lift the shield's 5 V header pin and jumper the shield logic rail to the UNO Q **3V3** pin (reversible; then all 3.3 V), or a 74AHCT125 on STEP/DIR/EN. |
| TMC2208 DIAG/INDEX (5 V outputs) | Not used — never wire to the MCU. |

---

## 7. Optional: load cell feedback

Not in the v1 critical path. The **vacuum sensor** (`VAC_SENSE`, A3) is the primary
pick-confirmation. A load cell adds one capability the vacuum sensor doesn't: the tile's
**weight**, which could feed grading (underfired / porous tiles are lighter).

If added:

- **Mount it in the fixed pickup nest**, not on the moving Z carriage — avoids a flexing
  load-cell cable in the drag chain and keeps the cell out of the dynamic load path.
- Cell + **HX711** 24-bit ADC → I2C-adjacent free pins (A4/A5 area) or two spare digital
  pins (HX711 is a bit-banged 2-wire, not real I2C). ~10–80 SPS — fine for a static
  "weigh the tile" read, too slow for dynamic sensing.
- Read while the tile sits in the nest, before pickup; pass the weight up to the master
  with the tile record.
- Tare on startup and periodically (drift with temperature).

Decision deferred — revisit once the dimensional-inspection module scope is settled
(`charter` §6.3), since tile weight may belong to that station rather than this one.

---

## 8. Calibration constants (all placeholders)

Authoritative copies live in `pick_place_node/sketch/sketch.ino` (firmware) and
`pick_place_node/python/pick_place/config.yaml` (Linux side); the two are **not** shared
at file level and must be kept in sync by hand (same rule as the acoustic station).

| Constant | Placeholder | Meaning / how to calibrate |
|---|---|---|
| `Vref` (per driver) | ~1.6 V | TMC2208 phase current. `I_RMS ≈ Vref × 0.71` for Rsense = 0.11 Ω → 1.6 V ≈ 1.13 A (~67 % of the 1.68 A motor; under the driver's 1.4 A continuous). Verify Rsense; thermal-test; X=Y=Z, A=Y. See §4a. |
| `MICROSTEP` | 1/16 | TMC2208 MS1/MS2 table (§4a — **both jumpers closed** = 1/16; open/open = 1/8, not full step). Same on all 4 drivers. Confirm with the turn test (§0.5). |
| `STEPS_PER_MM_X/Y/Z` | TBD | `200 × microstep_divisor / travel_per_rev_mm` (e.g. 1/16 + GT2 belt on a 20 T pulley = 40 mm/rev → 80 steps/mm). Then calibrate: command 100 mm, measure actual, scale. Belt/leadscrew choice unknown until the gantry exists. |
| `MAX_FEED_X/Y/Z` (mm/min) | 8000 / 8000 / 4000 | Raise until the motor skips under load, then back off ~30 %. |
| `ACCEL_X/Y/Z` (mm/s²) | 400 / 400 / 300 | Same method. Z lower (gravity + head mass). |
| `HOME_SEEK / HOME_TOUCH` (mm/min) | 2000 / 200 | Fast approach, slow re-touch after back-off. |
| `HOME_BACKOFF` (mm) | 3 | Retreat distance between seek and touch. |
| `HOME_DIR_X/Y/Z` | −, −, + | Which way each axis drives to find its switch (Z homes **up**). |
| `SOFT_LIMIT_*` | from `config.yaml` envelope | Real values captured at teach-in (protocol doc task 1.7). |
| `VACUUM_CONFIRM_MS` | 500 | Max wait for `VAC_SENSE` to show grip before `VACUUM_TIMEOUT`. |
| `LINK_TIMEOUT_MS` | 500 | Link silent this long mid-motion → stop + `alarm LINK_TIMEOUT`. |

All provisional until calibrated on the built gantry (`.claude/CLAUDE.md` Development
Rule 3).

---

## 9. Open items

- **Physical pin verification** — §3 pin names still to confirm against the real UNO Q
  silkscreen. MCU confirmed as **STM32U585 / Cortex-M33** (from the SWD flash log, 2026-09-01).
- **Logic-level compatibility (§6)** — **measured & resolved 2026-09-01**: inputs safe as-is,
  STEP/DIR/EN driven direct at 3.3 V with an empirical check at first motor bring-up.
- **Stepper library on the UNO Q core** — the core is **Zephyr** (`arduino:zephyr:unoq`),
  not classic AVR/mbed; deployed via `arduino-app-cli app` (App Lab). Classic Arduino API
  (`pinMode`/`digitalWrite`/`delayMicroseconds`) works — the teammate's `cnc` sketch
  compiles and flashes. FastAccelStepper / AccelStepper support under Zephyr is
  **unconfirmed** (protocol doc §9, build task 0.3); fallback is a hand-written timer ISR
  or a Zephyr-native stepper approach.
- **Gantry mechanical design** — frame, axis travel, belt/leadscrew choice, Z
  counterbalance/self-locking. Blocks `STEPS_PER_MM`, `MAX_FEED`, `ACCEL`. (`TODO.md`
  "Decide pick-and-place axis count, motor/gripper hardware, and gantry travel limits".)
- **Motor torque headroom** — the 42HM48-1684 gives 4.4 kg·cm, but run at ~1.13 A
  (derated for the TMC2208) it delivers proportionally less. Confirm this is enough for
  the gantry mass + tile once both are known; if not, options are a higher-current
  driver, 24 V with spreadCycle (UART), or belt reduction.
- **TMC2208 Rsense value** — 0.11 Ω assumed for the Vref formula; confirm on the actual
  module (0.15 Ω clones exist and change the current). See §4a.
- **stealthChop vs spreadCycle** — standalone gives stealthChop2 only. If high-speed
  torque is short (missed steps only at speed, not at low speed), wiring the PDN/UART pin
  for spreadCycle is the fix — a later upgrade.
- **12 V supply, not 24 V** — accepted for now (it's the PSU on hand). Limits high-speed
  torque; mitigate by keeping `MAX_FEED` moderate. If pick-place cycle time proves too
  slow or axes skip at speed, moving to a 24 V PSU is the first upgrade (nothing else in
  the wiring changes — TMC2208 and shield both handle it).
- **Gripper** — vacuum pump vs. venturi, cup diameter, whether one cup or several.
  Blocked on SMTW tile size/weight (`requirements.md` Open Items).
- **Homing switch type/placement** — mechanical vs. optical vs. inductive; min-only vs.
  min+max per axis.
- **A-socket clone jumper positions** — exact jumper layout differs slightly between CNC
  Shield V3 clones; confirm against the actual board.
- **E-stop contactor** — part not selected; must be rated for the motor PSU and fail-open.
- **Load cell (§7)** — go/no-go deferred to the dimensional-module scope decision.
- **Idle current reduction** — whether to drop `EN` at idle to cut motor heat, and how to
  keep Z from dropping if so.

---

## Cross-references

- Control architecture, command protocol, module plan, build order:
  `documents/programming/pick_place_control_protocol.md`.
- HMI / digital-twin dashboard: `documents/project/Pick_Place_Sorting_Subsystem.md`.
- Decision record: `documents/project/project_charter.md` §7.4 / §8.2 / §17,
  `documents/project/Automation_Architecture.md` §5.6,
  `documents/requirements/requirements.md` FR-14 / FR-18.
- Node code: `pick_place_node/` (`sketch/sketch.ino` MCU, `python/pick_place/` Linux).
- Sibling wiring doc / pattern: `documents/electrical/schematics/acoustic_station_wiring.md`.
- CNC Shield V3 reference: https://docs.cirkitdesigner.com/component/2c4686b3-94a4-4df1-aee0-7a9f67fd5f2e/cnc-shield-v3
