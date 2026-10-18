# I2C Bus Plan — Encoders, ToF Sensors and Mux

> **Status: PLAN, nothing wired or run** (written 2026-10-10). This is a paper analysis of
> the parts the owner named, to catch bus problems before any hardware is connected. Every
> address, voltage and timing figure comes from the parts' general specifications and the
> repo's own documents, **not from a bench measurement**. Items marked **VERIFY** must be
> checked against the actual modules.

## 1. Parts and where they live

The two controllers share **no wiring or hardware** (owner, 2026-10-10), so there are
**two completely separate I2C buses**. Identical addresses on the two buses cannot clash.

| Part | What it is | I2C address | Qty | Bus |
|---|---|---|---|---|
| TCA9548A | 8-channel I2C multiplexer | `0x70` (A0-A2 select `0x70`-`0x77`) | 1 (more if needed) | UNO Q bus |
| AS5600 | 12-bit magnetic rotary encoder module | **fixed `0x36`, not changeable** | 4 (X, Y, Y-clone, Z) | UNO Q bus, behind the mux |
| VL53L1X | Time-of-flight distance sensor (ST) | default `0x29`, changeable in software, **lost at every power cycle** | 1 on the UNO Q bus (ToF 1) | UNO Q bus |
| VL53L1X | same | default `0x29` | 2 on the Nano bus (ToF 2, ToF 3) | **Nano bus** |
| Raspberry Pi 5 | — | — | — | **no I2C devices** (camera is CSI, mic is USB) |

> **Part mismatch to resolve:** the owner wrote "vl5301x", read here as **VL53L1X**. The
> existing acoustic wiring doc (`documents/electrical/schematics/acoustic_station_wiring.md`
> §2) specifies a **VL53L0X**. They are different chips (L1X reaches 4 m, L0X about 2 m;
> their libraries differ). **VERIFY which part is actually in hand**, then make the wiring
> doc match.

### 1.1 Bus map

```text
UNO Q  (3.3 V logic, header pins A4=SDA / A5=SCL = `Wire2`)  NANO (5 V logic, A4=SDA / A5=SCL)
   │                                                              │
   └── TCA9548A  0x70                                             ├── VL53L1X  ToF 2  (striker station)   0x29 -> re-addressed
         ├── ch7  AS5600  X motor        0x36   (bench, 2026-10-18)   └── VL53L1X  ToF 3  (belt end / fall)    0x29
         ├── ch5  AS5600  Y motor        0x36   (bench)                   ^ both default to 0x29: CLASH, see 3.1
         ├── ch6  AS5600  Y' (clone)     0x36   (bench)
         ├── ch3  AS5600  Z motor        0x36   (bench)
         ├── ch0  VL53L1X ToF 1 (pickup) 0x29   (bench, 2026-10-18: ToF moved to ch0)
         └── ch1, ch2, ch4  spare
```

ToF 1 and ToF 3 watch the **same physical spot** but feed different controllers — see
`Automation_Architecture.md` §5.5 for what each does.

---

## 2. Why the mux is needed (and what it costs)

All four AS5600 answer at `0x36` and the address cannot be changed, so four on one bus is
impossible without a mux. The TCA9548A gives each encoder its own downstream bus; only the
selected channel is electrically connected to the controller.

Benefits worth keeping: a downstream device that hangs its bus (holds SDA low) only blocks
its own channel; and each channel has its own pull-ups, so the upstream bus sees only the
selected channel's load.

Costs:

- Every read is **select channel (write 1 byte) then read the device** — two bus
  transactions. A forgotten or failed channel select makes the *wrong* sensor answer, and
  with four identical `0x36` devices the read still "succeeds". Always read the channel
  register back, or verify after select.
- The mux itself is a device that can lock up. It has a RESET pin (wire it to a GPIO) and a
  recovery routine: release all channels, clock SCL up to 9 times, issue STOP.
- The mux's `0x70` must not collide with anything else on that bus. Nothing else is on
  it, so it does not.

---

## 3. Problems found, by severity

### 3.1 CRITICAL — two VL53L1X on the Nano bus share `0x29`

ToF 2 and ToF 3 both power up at `0x29`. Two on one bus means neither works reliably.
Options (**Open — decide**):

| Option | Cost | Notes |
|---|---|---|
| **XSHUT re-addressing at boot** (recommended) | 2 GPIO + no parts | Hold ToF 3 in reset via its XSHUT, boot ToF 2, give it a new address, release ToF 3. Address is lost on every power cycle, so this runs at **every boot**; after it, read both back and refuse to run the belt if either is silent. |
| A second TCA9548A on the Nano bus | 1 part | Cleaner, but adds a lockup-prone part to a *safety* path (ToF 3 prevents tiles falling). |
| A different ToF model for one | part change | Avoids the problem; new library to port. |

ToF 3 stops the belt to prevent a fall, so a **silent or dead ToF 3 must stop the belt**
(fail-safe), not look like "end is clear". A ToF that returns no target and a ToF that is
unplugged must not be treated the same.

### 3.2 HIGH — gantry cable length and motor noise on the encoder bus

The AS5600 modules sit on the stepper motors, spread across a gantry, while the UNO Q and
the mux sit at the shield. I2C is built for short runs (bus capacitance limit 400 pF); a
metre of cable past TMC2208 drivers and PWM-chopped motor leads is the usual way this goes
wrong, and the failure is intermittent (occasional NACKs, a stuck bus), not a clean error.
Mitigations, in order of effort:

1. Keep the mux **at or near the shield**, and run the downstream cables to the motors —
   downstream channels are isolated from each other, so one long noisy run does not corrupt
   the others.
2. Run SDA/SCL twisted with their own ground returns, away from motor leads; keep cable
   short. Add a **shielded** cable if runs exceed ~0.5 m.
3. Drop the bus clock from 400 kHz to **100 kHz** (reads take ~4x longer — see 4).
4. Lower pull-up values only after measuring rise time; do not stack many modules' onboard
   pull-ups on one segment (see 3.4).
5. If a run is still unreliable, use a **differential I2C extender** (e.g. PCA9615) on that
   run rather than fighting it.

### 3.3 HIGH — AS5600 on a single-shaft motor: a mechanical gap

The AS5600 reads a **diametrically magnetised magnet** close above the chip (small air gap,
centred on the shaft). The pick-and-place plan describes the motors as
**"42HM48-1684 NEMA 17 round-shaft"** — a single-shaft description. If there is **no rear
shaft**, there is nowhere to put the magnet on the motor side; the encoder would have to
sit on the driven axis (a pulley/idler) instead. **VERIFY** whether the motors have a rear
shaft. Also: a stepper's own field can disturb the sensor, so keep the module away from
the motor body or use a spacer/shield.

### 3.4 MEDIUM — logic levels and pull-ups

- **UNO Q is 3.3 V logic**; the **Nano is 5 V**. They are separate buses, so there is no
  level-shifting between them, but **each module must suit its bus voltage**. AS5600
  modules and VL53L1X breakouts differ: many carry a regulator and onboard pull-ups to
  their own supply, some are 3.3 V-only. **VERIFY each module's board** before connecting
  to the Nano's 5 V bus. The TCA9548A works at 1.65-5.5 V and sits at the bus voltage.
- **All pull-ups on one bus segment must pull to the same rail.** Mixing a 3.3 V-pulled
  module onto a 5 V bus (or vice versa) is a classic silent fault.
- **Too many pull-ups in parallel** (each module often has 4.7-10 kOhm) lowers the total
  resistance and can exceed the 3 mA sink limit. Rule of thumb for 3.3 V: keep the
  combined pull-up above ~1 kOhm. The mux isolates unselected channels, which helps on the
  UNO Q bus; the Nano bus has no mux, so count two ToF boards' pull-ups.

### 3.5 MEDIUM — encoder read timing and the closed loop

The owner's plan is **closed-loop** stepper control with an encoder on every motor. Rough,
**unmeasured** budget: at 400 kHz one 2-byte angle read plus a mux channel select is
about 0.2-0.3 ms, so four encoders is about 1 ms per sweep (about 1 kHz at best). At 100 kHz
it is about 4 ms (about 250 Hz). The step-pulse generation must **not wait on the bus**:
encoder polling is a separate, lower-priority task, and a stuck read must time out rather
than stall motion. An AS5600 gives **one revolution of absolute angle only (0-4095)**, so
firmware must count turns, and the sampling rate must exceed half a revolution per sample
at top speed. Whether the UNO Q's Zephyr Arduino core gives reliable I2C **timeouts**,
repeated starts and 400 kHz is **untested** (protocol doc §9, build task 0.3 shows the same
uncertainty for step timing).

### 3.6 MEDIUM — ToF behaviour that affects tile detection

- The VL53L1X ranges at a configurable timing budget (tens of ms per reading), so tile
  detection has up to that much latency. ToF 3 (fall prevention) must account for
  latency **plus belt coast distance** when choosing where to mount it.
- ToF 1 and ToF 3 point at the same spot. VL53L1X units share the same 940 nm band, so
  **optical crosstalk** is possible; aim them at slightly different points or test.
- The sensor reads distance to a *surface*: a terracotta tile's colour, a glossy glaze, or
  a tile at an angle will all change the reading. Calibrate on the real tile.
- The VL53L1X has an interrupt pin (GPIO1). Using it removes polling latency, but needs a
  free Nano interrupt pin (D2/D3). The Nano's e-stop is on the **power side**, so the MCU
  has those pins free.

### 3.7 LOW — notes

- ToF 1's address is the factory `0x29` on the UNO Q bus: fine, because the only other
  device types on that bus (mux `0x70`, AS5600 `0x36`) differ — and the AS5600s are behind
  the mux anyway. Put ToF 1 on its own mux channel to isolate it from the encoders.
- **Measured 2026-10-18:** on the UNO Q Zephyr core the header pins A4/A5 are **`Wire2`** (i2c3,
  PC1/PC0), not `Wire`. `Wire` is i2c2 (D20/D21) and `Wire1` is i2c4 (by the board's bus
  order; the Qwiic port is probably `Wire1`, not tested). Sketches that use plain `Wire` with
  the mux on A4/A5 see an empty bus. Found with `bench_tests/i2c_scan_test/` (mux `0x70` on
  `Wire2` only). A second bus is available if the ToF should live away from the encoders.
- Library footprint on the **Nano (328P: 32 KB flash, 2 KB RAM)**: two VL53L1X driver
  instances plus Wire plus the striker FSM must fit. **VERIFY** by compiling before the
  design is frozen; a lighter VL53L1X library may be needed.

---

## 4. Address table (final, once the choices above are made)

| Bus | Device | Address | Notes |
|---|---|---|---|
| UNO Q | TCA9548A | `0x70` | RESET wired to a GPIO (pin TBD) |
| UNO Q, mux ch0-3 | AS5600 x4 | `0x36` | one per mux channel |
| UNO Q, mux ch0 | VL53L1X (ToF 1) | `0x29` | own channel (was ch4 in the first plan) |
| Nano | VL53L1X (ToF 2) | `0x29` -> new address | re-addressed each boot (3.1) |
| Nano | VL53L1X (ToF 3) | `0x29` | kept at default; its fail-safe is mandatory |

No two devices on one bus share an address once 3.1 is solved.

---

## 5. Bring-up tests (bench, in this order)

1. **Scan each bus alone.** UNO Q: expect only `0x70` before any channel is selected.
   Nano: expect `0x29` with one ToF, two ToFs only after re-addressing.
2. **One encoder at a time through the mux**: read the angle, rotate by hand, confirm the
   value tracks and that the *right* channel answers (move one encoder; the others must
   not change).
3. **All four encoders at the target rate with the motors off**, then **with the motors
   running at speed** (the TMC2208s and motor leads are the noise source). Log NACKs,
   timeouts and stuck-bus events over a long run; any non-zero count is a finding.
4. **Probe SDA/SCL with a scope** (or logic analyser) on the longest run: rise time and
   ringing, clock at 400 kHz vs 100 kHz.
5. **Mux lock-up recovery**: deliberately hold a channel's SDA low, confirm the other
   channels still work and the recovery routine frees the bus.
6. **ToF crosstalk and fail-safe**: ToF 1 and ToF 3 aimed at the same spot, both reading
   a real tile; then unplug ToF 3 mid-run and confirm the Nano stops the belt.
7. **Voltage check** of every module's I2C lines against its bus rail before first power-up.

## 6. Open items

- [ ] Confirm the ToF part (VL53L1X vs the wiring doc's VL53L0X) and update the wiring doc.
- [ ] Choose the Nano ToF addressing method (3.1); XSHUT is the recommendation.
- [ ] Confirm whether the motors have a rear shaft for the AS5600 magnet (3.3).
- [ ] Confirm each module's board voltage / pull-ups for its bus (3.4).
- [x] Confirm the UNO Q's A4/A5 bus: it is `Wire2` (2026-10-18). Still open: which bus the Qwiic
      port is, and the core's I2C timeout behaviour (a hung read may block `loop()`).
- [ ] Choose the UNO Q mux RESET pin; the shield pin table (A1 and A2 are free) is the candidate.
- [ ] Compile-check the two-ToF Nano sketch for flash/RAM headroom.
- [ ] Measure real encoder sweep time on the UNO Q at 100 and 400 kHz.
- [ ] Run the section 5 tests and record results here.
  - 2026-10-18, test 2 partly: mux `0x70` answers on `Wire2`; AS5600s found behind it on ch5
    and ch6 (Y, Y-clone), angles read with 0 failed reads. Y-clone magnet reported too weak.
    Hand-rotation and motor-driven tests not yet run. One unexplained board reboot at 08:50
    while this bench was wired (suspected power).
