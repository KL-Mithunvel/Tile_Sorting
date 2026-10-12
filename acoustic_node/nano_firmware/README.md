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
| `tests/test_nano_firmware_conformance.py` | 30 tests: the compiled core driven over the real `tap_protocol.py` and the real `TapBridge`. |

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

- **Boot:** belt 0, striker inhibited, `HELLO nano-fw-0.1`. `BELT n` and `STRIKE 1` are
  needed to do anything; `system.sh start` sends both once the line is verified.
- **Watchdog:** no *valid* line from the Pi for 2000 ms -> belt 0 **and** striker
  inhibited, `ERR WATCHDOG`. **Nothing restarts by itself:** after a trip, PINGs alone
  keep the link up but the belt and striker stay off until a fresh `BELT` / `STRIKE 1`
  arrives. (A bare `systemctl restart tile-acoustic` therefore needs `system.sh start`
  or a `station/belt` + `station/strike` afterwards.) A corrupt stream does not feed it.
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

- Compile for AVR (`arduino-cli compile --fqbn arduino:avr:nano`) and check flash/RAM
  (two VL53L1X objects on 2 KB RAM; `i2c_bus_plan.md` item).
- **Pins:** the striker pins are carried over from `acoustic_node/sketch/sketch.ino`;
  `PIN_BELT_PWM` (D9) and `PIN_TOF_END_XSHUT` (D8) are **new** and not in
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
