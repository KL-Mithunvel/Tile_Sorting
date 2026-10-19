# Nano firmware (striker + belt)

The Arduino Nano (classic ATmega328P) half of the acoustic station: it fires the
ball-drop striker when a tile is in place and runs the conveyor belt PWM, taking
its orders from the Raspberry Pi over USB serial. Spec:
`documents/programming/nano_serial_protocol.md`. Pi side: `acoustic/tap_protocol.py`,
`acoustic/tap_bridge.py`.

| File | Role |
|---|---|
| `nano_core.h` | **All the logic.** Portable C++11, no Arduino includes, no heap, behind a `Hal`. Protocol, watchdog, tap sequence, ToF handling, belt-end fail-safe. |
| `nano_firmware.ino` | Board glue only: pins, the two VL53L1X sensors, serial, PWM. **Never compiled for AVR.** |
| `../firmware_host/` | `nano_host.cpp` (core + simulated Hal) and `host_nano.py` (drives it from Python). Test tooling. |
| `tests/test_nano_firmware_conformance.py` | 33 tests: the compiled core driven over the real `tap_protocol.py` and the real `TapBridge`. |

## Prototype striker: one sprung solenoid (added 2026-10-19)

`striker_type = STRIKER_SPRUNG` (the glue sets it, `#define NANO_STRIKER_SPRUNG`): ONE solenoid
on the ARM pin above the tile. **Energised = striker retracted** (noisy), **released = gravity +
spring strike**. LOCK is unused. Both edges are reported so both sounds can be labelled:

- `RET <seq> <src> <ms>` when the coil is energised, `TAP <seq> <present> <mm> <ms> <src>` when it
  is released. `src`: 0 ToF, 1 manual, 2 safety release, 3 ToF-started but unreadable at the strike.
- `MODE 0|1` -- 0: the ToF starts cycles (settle, retract, hold `retract_hold_ms`, release);
  1: manual, the ToF is ignored. Manual commands (all need `MODE 1` and `STRIKE 1`):
  `RETRACT` holds the coil up until `RELEASE`; `TAP` is retract, hold, release in one go.
  A refused command answers `ERR NOTMANUAL | INHIBITED | BUSY | NOTRETRACTED | FAULT`.
- The coil is never left hanging: held longer than `retract_max_ms`, `STRIKE 0`, a watchdog trip, or
  an e-stop all let it down -- a strike, reported with `src` 2 (`ERR RETRACT` / `INHIBITED`).
- `HB` gains two fields (`manual`, `retracted`) in this mode only; the dual-solenoid wire format is
  unchanged. 16 tests in `tests/test_nano_sprung_striker.py`.
- **Driver (owner, 2026-10-19):** the coil is driven by a **Cytron MDD10A**, not a bare MOSFET: **D10 = PWM, D8 = DIR** (DIR held high; only the PWM energises it). The glue pulls in at
  `STRIKER_PULL_PCT` 100 % for `STRIKER_PULL_MS` 150 ms, then holds at the **settable hold power** (`HOLD n`, Striker panel slider, default **30 %**, reported in `HB`; applies live with no reflash). **The coil overheated parked at 60 % (owner switched its power off, 2026-10-19):** lower it until the coil stays cool, but too low drops the striker by itself. Timer1 is set to ~31 kHz so the coil does not hum at the hold duty (that hum would be in every
  recording). **A hold duty too low drops the striker by itself: a strike the Nano never reports** -- raise it until it holds for the whole `retract_max_ms`, then check the coil is not hot.
- **Unmeasured:** the hold time (how long the retraction rings), the coil's duty rating, the strike
  duration, the pull/hold duty. The `Config` and duty values are placeholders.

## Prototype ToF and timing (owner, 2026-10-19)

- **Sensor:** one "TINY LIDAR" **VL53L0X** module (I2C, SDA = A4, SCL = A5, default address 0x29, 2 m range), Pololu `VL53L0X` library.
  The full system's two VL53L1X remain behind `#define NANO_TOF_VL53L0X 0`. The prototype has **no belt-end sensor**
  (`tof_end_fitted = false`: sensor 3 is not polled, so no `ERR TOF3` noise).
- **Meaning:** the empty surface read 121-138 mm on the first try; **<= 100 mm = a tile is there** (debounced over 5 samples, ~125 ms). **Measured on the rig 2026-10-19: a tile reads 91 mm (rock steady over 20 s), the empty surface 112-142 mm** -- the original 75 mm never saw the tile, so nothing was released. Only about 20 mm separates them; 100 leaves ~9 mm on the tile side and ~12 mm on the empty side. Lower the sensor or re-measure with other tiles before trusting it.
- **The striker RESTS RETRACTED (owner, 2026-10-19, replaces the earlier "down at rest, retract on a tile"):** in AUTO, sensor answering and nothing under it -> the coil is energised and the
  striker waits up (`RET`) -- **armed or not, even before the Pi has said anything** (owner, same day: it was low whenever it was disarmed). **Arming (`STRIKE 1`) only allows the release.** A tile recognised for **2 s** (`release_delay_ms`) releases it (`TAP`, the strike). A tile that leaves before the 2 s costs nothing. **After the strike it LIFTS AGAIN 1.5 s later even though the tile is still under it** (owner: the tile cannot be moved out while the striker is down on it; 1.5 s lets the ring die before the lift noise); that tile is never struck twice -- it must leave (sensor reads no tile) before the next one counts. A tile already under it at power-up counts as 'must leave' too. The sensor must have settled (5 good readings) before any of this is decided. MANUAL or an unreadable ToF = it stays down. Disarming (`STRIKE 0`) or losing the Pi while parked leaves it UP but inhibited: a tile cannot release it, and arming again starts a fresh 2 s. (Fail-safe trade: a dead Pi leaves the coil on.)
  **There is no `retract_max_ms` limit while parked: the coil is on for as long as the line idles.** The only mitigation is the lower hold duty (60 %, a guess) -- measure the coil temperature after
  an hour parked before leaving it. Switching to MANUAL while parked turns it into a held retract (RELEASE strikes); back to AUTO re-parks it.
  `nano::prototypeConfig()` holds all of this, and the board glue and the laptop tests (`--prototype`) use the same function.
- **Build trap found 2026-10-19:** the first AVR build guarded the library `#include` with `__has_include`, and `arduino-cli` then linked **no ToF library at all** (flash 10.5 KB
  instead of 15.5 KB) while still compiling cleanly. The include is now unconditional, so a missing library is a compile error, not a silently blind sensor. Size now: 15,548 B flash (50 %), 1,286 B RAM (62 %).
- **Distance in the heartbeat (2026-10-19):** `HB` carries the striker ToF's latest distance (`tof1_mm`), shown on the Service view with a TILE / no tile verdict at 75 mm. First real readings on the empty surface: **121-138 mm, not 150** (so about +/-10 mm of noise); a tile has not been measured yet.
- **Unverified:** the Pololu VL53L0X calls (`init`, `startContinuous`, `readReg(RESULT_INTERRUPT_STATUS)`, `readRangeContinuousMillimeters`) are from the library's API and have never met the module;
  how the module's board is powered (3.3 V or 5 V) and its I2C pull-ups; the 150 mm / 75 mm figures against the real surface and a real tile (ToF noise, tile colour, the mount height).

## Running the tests

Needs a C++11 compiler; the tests skip without one.

```bash
# any of: c++ / g++ / clang++ on PATH, or
export NANO_HOST_CXX="g++"                      # or GANTRY_HOST_CXX (shared convention)
# no compiler at all:
pip install --target /some/empty/dir ziglang
PYTHONPATH=/some/empty/dir NANO_HOST_CXX="python -m ziglang c++" python -m pytest tests/test_nano_firmware_conformance.py
```

## Behaviour (each line is a test)

- **Boot:** belt 0, striker inhibited, `HELLO nano-fw-0.2`. `BELT n` and `STRIKE 1` are
  needed to do anything; `system.sh start` sends both once the line is verified.
- **Watchdog:** no *valid* line from the Pi for 2000 ms -> belt 0 **and** striker
  inhibited, `ERR WATCHDOG`. **Nothing restarts by itself:** after a trip, PINGs alone
  keep the link up but the belt and striker stay off until a fresh `BELT` / `STRIKE 1`
  arrives. (A bare `systemctl restart tile-acoustic` therefore needs `system.sh start`
  or a `station/belt` + `station/strike` afterwards.) A corrupt stream does not feed it.
  On the first valid line after a trip the Nano says `ERR WDOGRESET` (once), because the
  `ERR WATCHDOG` sent at the trip was probably never delivered. With
  `station.nano.resend_on_recover: true` the bridge answers that by re-sending the last
  `STRIKE`/`BELT` it was told to send (off by default: it can restart a stopped belt).
- **Checksums:** a line with a missing or wrong XOR checksum is discarded; out-of-range
  (`BELT 101`) or unknown commands are rejected, not clamped. `ERR BADLINE` is rate
  limited (one per 5 s) so motor noise cannot flood the Pi's alarms.
- **Strike:** ToF 1 in range for 5 samples -> SETTLE 200 -> ARM 300 -> ARM_SETTLE 150
  -> fresh ToF read -> LOCK opens and `TAP <seq> <tile_present> <mm> <ms>` is sent that
  instant -> 100 ms pulse -> COOLDOWN 1000. A tile must leave and a new one arrive
  before the next cycle. A tile that arrives while the striker is inhibited is not
  struck later.
- **No evidence, no claim:** if ToF 1 cannot be read at the strike there is no strike
  and `ERR TOF1` instead -- never a `TAP ... 0` on no data. A dead ToF 1 never starts a
  cycle.
- **Belt end (fall prevention):** ToF 3 silent, never seen, or a tile at the end -> the
  applied belt is 0 while the *commanded* speed is kept, and resumes when the end is
  seen clear. `HB` reports the applied speed.
- **E-stop:** latches FAULT (both solenoids off, belt off, status LED off, `ERR ESTOP`)
  until reset / power cycle; releasing the button does not restart anything.

## Decisions made here that the spec left open

1. Resume after a watchdog trip is **manual** (above). Safer than auto-resuming a belt
   after the Pi vanished; the cost is that the operator/master must re-send.
2. The striker keeps working while the belt is held by ToF 3 (the open question in
   `Automation_Architecture.md` section 5.5). Change in `runSequence()` if wrong.
3. `ToF 3` is mandatory by default (`Config::require_tof_end`); a bench with no
   belt-end sensor must pass `--no-tof-end` in the harness / set it false in the glue.

## Not verified -- do before trusting it on a Nano

- ~~Compile for AVR~~ **Done 2026-10-19:** `arduino-cli compile --fqbn arduino:avr:nano:cpu=atmega328old` with Pololu VL53L1X 1.3.1 -> 10,520 B flash (34 %), 1,161 B RAM (56 %, 887 B left).
  Flashed to a real Nano through the Pi the same day (below); it boots, sends `HELLO nano-fw-0.2`, answers `PING` and sends `HB`. **Not yet run:** the ToF sensors (both report `timeout`: not wired / not answering),
  the solenoid (no `RETRACT` was sent), belt PWM, e-stop.
- **Pins:** the striker pins are carried over from `acoustic_node/sketch/sketch.ino`;
  `PIN_BELT_PWM` (D9) and `PIN_TOF_END_XSHUT` (D12) are **new** and not in
  `acoustic_station_wiring.md` yet.
- The Pololu `VL53L1X` calls (`init`, `setAddress`, `startContinuous`, `read(false)`,
  `timeoutOccurred`, `ranging_data.range_status`) are from the library's documented
  API, not run. Without the library the firmware reports "ToF not answering": belt
  held, no strikes (fail-safe), `ERR TOF1` / `ERR TOF3` on the Pi.
- `tof_end_mm` (60) and the ToF mount distance vs belt coast are unmeasured; so are the
  solenoid timings (carried over, provisional).
- PWM at D9 is ~490 Hz; whether the motor driver likes that is unknown.
- Then, in `acoustic/config.yaml`: `station.nano.enabled: true`, `required: true`,
  `port:` by `/dev/serial/by-id/...`, and `trigger.mode: hardware`.

## Flashing from the Pi (what worked, 2026-10-19)

The Pi has no internet and no `avrdude`, so: compile on the laptop (App Lab's `arduino-cli` + the `arduino:avr` core +
`lib install VL53L1X` into a scratch library dir), copy `nano_firmware.ino.hex` to the Pi (`pscp`), copy Arduino's
`avrdude-6.3.0-arduino17-aarch64` tool (SHA-256 checked against `package_index.json`) and unpack it in `~/nano_flash/` (no root needed), then on the Pi:

```bash
~/nano_flash/avrdude/bin/avrdude -C ~/nano_flash/avrdude/etc/avrdude.conf -p atmega328p -c arduino   -P /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 -b 115200 -D -U flash:w:nano_firmware.ino.hex:i
```

This board (CH340) took **115200** baud (the new Optiboot bootloader); 57600 (the "old bootloader" setting) got `not in sync`.
The board resets when the port opens, so the solenoid driver's inputs float for a moment: keep clear of the striker while flashing.
Stop `tile-acoustic`'s Nano bridge (or leave `station.nano.enabled: false`) so nothing else holds the port.
