# Acoustic Station Wiring — ToF-Triggered Dual-Solenoid Ball-Drop Impactor

> **Pending revision (2026-10-10, proposed):** the team is considering moving this
> controller from the UNO Q to an **Arduino Nano (classic ATmega328P)** and the microphone
> and audio analysis onto the camera **Raspberry Pi 5**, with the Nano reached over USB
> serial. If confirmed, this whole document changes: the pin table below (a Nano has a
> different pin map and **5 V logic**, the UNO Q is 3.3 V), the "microphone plugs into the
> UNO Q" row, the common-ground note, and the e-stop handling (the Nano's e-stop cuts
> power upstream). Also: the BOM below specifies a **VL53L0X**, but the owner's parts are
> **VL53L1X** — resolve in `documents/electrical/i2c_bus_plan.md`. Nothing below has been
> rewritten yet.

> **Prototype variant (2026-10-19):** the prototype replaces the two solenoids below with
> **one** solenoid driving a sprung striker mounted above the tile. Energised = retracted
> (spring compressed); de-energised = gravity + spring drive the striker into the tile, so
> a single MOSFET channel is enough and the ARM/LOCK timing sequence does not apply. The
> flyback diode and the common-ground rule still apply. The retraction is audible and must
> be removed from the microphone analysis. The belt is hand-turned (no belt motor
> driver). Trigger: ToF, or a manual `TAP` from the Pi over serial. Pin numbers, solenoid
> model and striker mass are not yet decided or measured; nothing below was rewritten.
> Details: `project_charter.md` §6.2 "Prototype Configuration".

Decided 2026-08-20. This resolves `project_charter.md` §6.2's open release-mechanism
question (electromagnet vs. solenoid gate vs. servo latch): **two solenoids, one ARM +
one LOCK, driving a gravity-drop impact.** Neither solenoid touches the tile directly —
see Mechanism below. Controller: Arduino UNO Q (`acoustic_node/`, per
`Automation_Architecture.md` §5.3/§5.6).

Status: **electrical/control architecture decided and coded** (`acoustic_node/sketch/sketch.ino`,
`acoustic_node/python/acoustic/`); mechanical design (actual lift/latch hardware, ball
mass/drop height, striker tip) and the exact pin numbering below are **not yet verified
against real hardware** — see Open Items.

---

## 1. Mechanism

```text
                    ARMED (waiting to release)
        ┌───────────────────────────────────┐
        │              LOCK latch            │
        │           (closed = holding)       │
        │                  ▼                 │
        │               ● ball                │
        │              /                     │
        │   guide tube/                       │
        │   channel    │                      │
        │              │                      │
        │        ┌─────┴─────┐                │
        │        │ ARM lift  │  <- pushed up   │
        │        │ platform  │     by ARM      │
        │        └───────────┘     solenoid    │
        │              ▲                       │
        │        (rest position,               │
        │         ball loaded here)            │
        └───────────────────────────────────┘
                          │
                          │  LOCK energizes -> latch opens -> ball free-falls
                          ▼
                    ┌──────────┐
                    │   Tile   │
                    └──────────┘
```

Sequence for one tile:

1. **ToF sensor** confirms a tile has arrived and settled at the station (distance below
   `TOF_TRIGGER_DISTANCE_MM` for `TOF_DEBOUNCE_SAMPLES` consecutive reads).
2. **SETTLE** — brief dwell so any tile/conveyor bounce stops before arming.
3. **ARM** — the ARM solenoid energizes, driving a lift platform/lever that raises the
   striker ball from its rest position up a guide tube/channel into the LOCK latch's
   catch at drop height. After `ARM_MS`, the ARM solenoid de-energizes and retracts out
   of the way — it plays no further part in the strike.
4. **ARM_SETTLE** — brief dwell for the arm mechanism to fully retract and any vibration
   from arming to die down, so it doesn't contaminate the acoustic recording.
5. **RELEASE** — the LOCK solenoid energizes, opening the latch. The ball is no longer
   held by anything and **free-falls under gravity** onto the tile. This is the actual
   impact — **neither solenoid strikes the tile directly**; both only position and then
   release the ball. The MCU increments a `tapCount` bridge variable at this instant so
   the Python side can time its audio capture window relative to the real release, not
   the earlier ToF detection.
6. **COOLDOWN** — minimum dwell before the next tile can arm a new cycle, and before the
   sequencer will accept another tile-present edge (prevents the same tile re-triggering
   while it's still sitting at the station).

Impact energy is `E = mgh` (ball mass × g × drop height) — both are open items (§7)
pending tile mass/stiffness data from SMTW.

---

## 2. Bill of Materials

| Component | Notes |
|---|---|
| Controller | Arduino UNO Q (existing lab board) |
| ToF sensor | VL53L0X breakout (I2C), e.g. Adafruit/GY-VL53L0XV2 — cheap, well-documented Arduino library support. Any I2C ToF module with a similar library API works; swap without repinning since it's on the fixed I2C bus. |
| Solenoid ×2 | Push-pull solenoid, 12V or 24V DC coil (match to whichever PSU is on hand) — one for ARM (lift), one for LOCK (latch release). Exact stroke/force TBD (§7). |
| MOSFET driver ×2 | One low-side N-channel MOSFET switch module per solenoid (e.g. a ready-made "MOSFET trigger switch" board with onboard gate resistor + flyback diode), or discrete IRLZ44N/similar logic-level MOSFET + 220 Ω gate resistor + 1N4007 flyback diode across the coil if built by hand. |
| Solenoid PSU | Separate regulated 12V/24V DC supply, sized to both solenoid coils' combined draw — **not** the UNO Q's own 5V rail (`project_charter.md` §8.2, §17 Electrical Safety). Common ground with the UNO Q logic side only, at one point. |
| USB microphone | Any USB-class audio input device (condenser or measurement mic — see `Acoustic_Sorting_Subsystem.md` §3.2 for model suggestions). Plugs into the UNO Q's USB-A host port directly — no GPIO involved. |
| E-stop switch | Normally-closed loop switch, wired to `PIN_ESTOP` (reserved, not yet physically installed — see §7). |
| Misc | Screw terminals, ferrite bead on the mic USB cable (electrical noise mitigation, `Acoustic_Sorting_Subsystem.md` §6.4), enclosure. |

---

## 3. Pin Table (Arduino UNO Q, Uno-shaped header)

**Not yet verified against the physical board silkscreen / App Lab's real GPIO naming**
(see `acoustic_node/README.md` — App Bricks has never been exercised on this project's
UNO Q). These are standard Uno R3 header positions, chosen to avoid the reserved
UART pins (D0/D1) and to leave the SPI-adjacent pins (D10–D13 besides the status LED)
free for anything added later.

| Pin | Signal | Direction | Connects to | Notes |
|---|---|---|---|---|
| A4 | I2C SDA | bidirectional | ToF sensor SDA | Shared bus — fixed pin, not reassignable |
| A5 | I2C SCL | bidirectional | ToF sensor SCL | Shared bus — fixed pin, not reassignable |
| D7 | `PIN_TOF_XSHUT` | output | ToF sensor XSHUT | Hardware shutdown/reset; HIGH = sensor active. Also needed if a second I2C ToF is ever added (re-addressing via XSHUT sequencing). |
| D5 | `PIN_SOLENOID_ARM` | output → MOSFET gate | ARM solenoid driver | PWM-capable pin, kept in reserve for soft-start/pulse shaping later; driven as a plain digital HIGH/LOW pulse for now. |
| D6 | `PIN_SOLENOID_LOCK` | output → MOSFET gate | LOCK solenoid driver | PWM-capable, same reasoning as D5. |
| D2 | `PIN_ESTOP` | input, `INPUT_PULLUP` | E-stop loop (normally closed) | Interrupt-capable pin. LOW = loop open = e-stop tripped. **Reserved — not yet wired to a physical switch** (§7). |
| D4 | `PIN_TAP_LED` | output | Bench debug LED | Lights for the duration of `RELEASE` — visual confirmation of a tap firing without a serial monitor. |
| D13 | `PIN_STATUS_LED` | output | Onboard LED | On = running normally, off = `FAULT` state (e-stop latched). |
| USB-A host port | — | — | USB microphone | Linux/Qualcomm side, not a GPIO pin. Captured via `sounddevice` in `acoustic/capture.py`, same as the current dev-laptop mic path. |
| D8 | *(reserved, unused)* | — | — | Candidate for a future arm-position limit switch, if open-loop `ARM_MS` timing proves unreliable (§7). Not implemented this pass. |

### 3.1 Nano variant (2026-10-12, firmware written, **never wired**)

If the shared-Pi plan is confirmed, this controller is an **Arduino Nano (ATmega328P,
5 V logic)** instead of the UNO Q, reached over USB serial by the Pi (`documents/programming/
nano_serial_protocol.md`); the microphone is on the Pi, not on the controller. The firmware
is `acoustic_node/nano_firmware/` (logic in `nano_core.h`; pins in `nano_firmware.ino`).
The striker pins are unchanged from the table above; the two marked NEW are **not yet
confirmed against a wired board**.

| Nano pin | Signal | Direction | Connects to | Notes |
|---|---|---|---|---|
| A4 / A5 | I2C SDA / SCL | bidirectional | ToF 1 + ToF 3 (VL53L1X) | Fixed pins. Two sensors on one bus: see XSHUT below. |
| D7 | `PIN_TOF_STRIKER_XSHUT` | output | ToF 1 XSHUT (striker station) | Held low at boot while ToF 1 is moved to address `0x30`. |
| D12 | `PIN_TOF_END_XSHUT` | output | ToF 3 XSHUT (belt end) | **NEW.** Released after ToF 1 is re-addressed, so ToF 3 keeps `0x29` (`i2c_bus_plan.md` 3.1). D8 stays reserved for the arm limit switch. |
| D5 | `PIN_SOLENOID_ARM` | output | ARM solenoid MOSFET gate | Unchanged. |
| D6 | `PIN_SOLENOID_LOCK` | output | LOCK solenoid MOSFET gate | Unchanged. |
| D2 | `PIN_ESTOP` | input, `INPUT_PULLUP` | E-stop loop (NC) | LOW = tripped. The Nano's e-stop also cuts power upstream (`Automation_Architecture.md` 5.5). |
| D4 | `PIN_TAP_LED` | output | Bench LED | Lit for the duration of `RELEASE`. |
| D13 | `PIN_STATUS_LED` | output | Onboard LED | On = running, off = FAULT. |
| D9 | `PIN_BELT_PWM` | output (PWM, Timer1, ~490 Hz) | Belt DC-motor driver input | **NEW.** Driver choice and whether 490 Hz suits it are open. |
| USB | serial 115200 8N1 | — | the Pi | Pin it by `/dev/serial/by-id/...`; the Nano is not on the LAN. |

Behaviour the wiring has to support (all in `acoustic_node/nano_firmware/README.md`):
**belt fail-safe** (a silent or never-seen ToF 3, or a tile at the belt end, holds the belt
at 0), **Pi watchdog** (no valid serial line for 2 s stops the belt and inhibits the
striker), and **latched e-stop FAULT**. ToF 3's mount distance (latency + belt coast) is
unmeasured; `tof_end_mm` in `nano_core.h` is a provisional 60.

---

## 4. Power Architecture

```text
   UNO Q (USB-C / 5V logic)              Separate 12V/24V DC PSU
   ────────────────────────              ───────────────────────
   D5 ── gate ──► MOSFET#1 ──► ARM solenoid  ◄── V+
   D6 ── gate ──► MOSFET#2 ──► LOCK solenoid ◄── V+
   GND ───────────────────────────────────────── GND (common, one point only)

   Each solenoid: flyback diode (1N4007 or similar) across the coil,
   cathode to V+, anode to the MOSFET drain node — protects the MOSFET
   from inductive kickback on turn-off.
```

- Logic (UNO Q, ToF sensor, MOSFET gates) and solenoid power are **separate supplies**,
  tied at a single common ground point — per `project_charter.md` §8.2 ("the computer
  should not directly drive solenoids... send commands to driver modules") and §17
  Electrical Safety (isolated power supplies, protected driver circuit).
- Fuse/circuit-breaker the solenoid PSU line (NFR-06).
- Keep solenoid power wiring physically separated from the mic USB cable and I2C wiring
  to reduce switching noise pickup (`Acoustic_Sorting_Subsystem.md` §6.4).

---

## 5. Timing Constants

Authoritative copy lives in `acoustic_node/sketch/sketch.ino` (this is what actually
runs). `acoustic_node/python/acoustic/config.yaml`'s `tap_mechanism` section mirrors the
same values for the dev-machine simulator (`acoustic.tap_sequencer.TapSequencer`) — the
two are **not** shared at the file level and must be kept in sync by hand.

| Constant | Default | Meaning |
|---|---|---|
| `TOF_TRIGGER_DISTANCE_MM` | 80 | Tile considered "in place" when the ToF reading is at or below this |
| `TOF_DEBOUNCE_SAMPLES` | 5 | Consecutive in-range readings required before treating the tile as confirmed present |
| `SETTLE_MS` | 200 | Dwell after tile confirmed, before arming |
| `ARM_MS` | 300 | ARM solenoid energized duration (lift travel time) |
| `ARM_SETTLE_MS` | 150 | Dwell after arm retracts, before releasing |
| `LOCK_PULSE_MS` | 100 | LOCK solenoid energized duration (latch open) |
| `COOLDOWN_MS` | 1000 | Minimum time before the next tile can arm a new cycle |

All five durations are placeholder starting points, not calibrated against real solenoid
travel time or settle behavior — tune once hardware exists (mirrors the "provisional
until recalibrated on real hardware" rule already applied to `rms_threshold`).

---

## 6. Safety

- E-stop input reserved (`PIN_ESTOP`, D2) — on trip, the MCU immediately de-energizes
  both solenoids (LOCK included, so the latch stays **closed**/holding — fails safe, no
  uncontrolled drop) and latches into a `FAULT` state that only clears on reset/power
  cycle, not simply on the e-stop loop reclosing (`project_charter.md` §17, "lockout
  procedure for maintenance").
- Guard the drop zone / ball path per §17 Mechanical Safety (no exposed pinch points).
- Solenoid driver circuits are opto-isolated or at minimum flyback-protected (§6.4 of
  `Acoustic_Sorting_Subsystem.md`).

---

## 7. Open Items

- **Physical pin verification** — these are standard Uno R3 header positions; confirm
  against the actual UNO Q board silkscreen and how App Bricks names/exposes GPIO once
  hardware is on the bench (same caveat as every other "unverified" App Lab item in this
  repo — see `acoustic_node/README.md`).
- **ToF sensor model + library** — VL53L0X assumed above; `sketch.ino`'s
  `readTofDistanceMm()` is a placeholder pending the actual library API. **The owner's
  parts are VL53L1X** (`i2c_bus_plan.md`); the Nano firmware is written for them (Pololu
  `VL53L1X` library API, unverified), so treat every "VL53L0X" in this document as
  VL53L1X.
- **Mechanical lift/latch design** — the ASCII diagram in §1 is a conceptual placeholder,
  not an engineered mechanism. Needs real CAD (`documents/mech/`) once the ARM/LOCK
  approach is validated on the bench.
- **Ball mass, material, and drop height** — fixes impact energy (`E = mgh`); blocked on
  SMTW tile size/weight data (`requirements.md` §3 Open Items, same blocker as elsewhere).
- **Reload/reset path** — after a strike, the ball needs to return to the ARM lift
  platform's rest position for the next cycle (e.g. a gravity return ramp back to the
  pickup point). Not designed yet — `COOLDOWN_MS` currently assumes this happens
  automatically, which it won't until a return path exists.
- **Open-loop arm timing** — `ARM_MS` assumes the lift always reaches the latch in a
  fixed time. D8 is reserved for a future limit switch if this proves unreliable.
- **Striker/ball surface** — Nylon/Delrin tip or coating recommended
  (`Acoustic_Sorting_Subsystem.md` §3.1) to avoid marking the tile surface; not yet
  chosen for the ball itself.

---

## Cross-references

- Mechanism/state-machine code: `acoustic_node/sketch/sketch.ino` (MCU, real-time),
  `acoustic_node/python/acoustic/tap_sequencer.py` (pure Python mirror, dev-machine
  simulator + tests).
- Audio capture on tap event: `acoustic_node/python/acoustic/hardware_trigger.py`.
- Config: `acoustic_node/python/acoustic/config.yaml` (`trigger.mode`, `hardware_trigger`,
  `tap_mechanism` sections).
- Decision record: `documents/project/project_charter.md` §6.2,
  `documents/project/Acoustic_Sorting_Subsystem.md` §3.1,
  `documents/requirements/requirements.md` FR-21.
