# Encoder smoke test

Bench bring-up app for the pick-and-place gantry: checks that each of the 4 AS5600
encoders (X, Y, Y-clone, Z, behind the TCA9548A mux) follows its stepper motor, with a
browser GUI. **Not** the real gantry firmware. Sibling to `bench_tests/stepper_smoke_test/`
(motors move) and `bench_tests/io_smoke_test/` (limits / e-stop / vacuum) - same
"throwaway bring-up code, outside the node's App Bricks project" role. Wiring and open
problems: `documents/electrical/i2c_bus_plan.md`.

> **Status (2026-10-18): compiled and run on the lab UNO Q; reads only.** The sketch builds
> (93 KB, 11% of flash), the GUI connects, the mux answers and the two Y encoders (ch5, ch6)
> read angles with zero failed reads; the Y-clone magnet reported "too weak". **No motor
> move (Test button) has been run yet**, and X and Z encoders are not fitted. The pass/fail
> logic was exercised on the laptop against synthetic status lines only.
>
> **A4/A5 are `Wire2` on the UNO Q, not `Wire`.** `Wire` is i2c2 (D20/D21), `Wire1` is i2c4,
> `Wire2` is i2c3 = A4 (SDA) / A5 (SCL). Found with `bench_tests/i2c_scan_test/` (mux at `0x70`
> on `Wire2` only). The sketch uses `Wire2` throughout. Encoders are on mux channels
> **X=7, Y=5, Y-clone=6, Z=3** (`ENC_MUX_CH` in the sketch); `python/config.yaml` still
> lists them by encoder index, not by mux channel.

## What it does

- **Sketch** (`sketch/sketch.ino`): reads the 4 encoders through the mux (`ch0` X, `ch1` Y,
  `ch2` Y-clone, `ch3` Z; mux channel register read back after every select), unwraps the
  12-bit angle into a running position, counts failed reads, and runs a **1-revolution
  out-and-back move** on request (3200 steps each way, the same pulse timing as
  `stepper_smoke_test`), sampling the encoders every 64 steps. Drivers are enabled only
  during a move. Mux RESET is on `A1`.
- **GUI** (`python/`, Flask, `http://<board>:5071/`): a live dial per encoder (angle,
  running position, failed reads, magnet status), a Test button per axis plus Test all and
  STOP, a results table with PASS/FAIL and the reason, an I2C scan of the 8 mux channels,
  mux reset and zero-positions buttons.
- **Manual jog** (added 2026-10-18): per axis, signed jogs of 1/16, 1 or 5 motor turns (200 / 3200 /
  16000 steps). One int goes over the Bridge (`enc_jog`: `((axis*2 + dir) << 20) | steps`); the
  drivers are on only while the jog runs. A finished jog ends in state "stopped" so the page builds
  no pass/fail result for it. Not a substitute for homing: it ignores limit switches and soft limits.
- Y and Y-clone share one STEP/DIR pair (clone jumpers), so the "Y + A" test turns both
  and checks encoders 1 and 2 together.
- Pass/fail is decided in Python (`enc_protocol.py`, tolerance in `python/config.yaml`):
  out-leg within `tolerance_deg` of `deg_per_motor_rev`, back to start within the
  tolerance, no failed I2C reads, magnet OK. It also flags an encoder that moved when it
  was **not** driven (crossed mux channels, I2C plan section 5 test 2). The sign of the
  out-leg is shown as the encoder's direction; a `-` is a mirrored mount, not a failure.
- **Wiring check without moving anything:** turn a shaft by hand and watch only its dial
  follow.

## Try the GUI on the laptop (no board)

```bat
cd pick_place_node\bench_tests\encoder_smoke_test\python
pip install flask pyyaml
python sim_run.py [--fault none|dead_y2|crossed|noisy_z]     :: http://127.0.0.1:5071/
```

`sim_run.py` replaces the sketch with a simulator that emits the same `ENC ...` lines
(2 s per leg), so the real parser, pass/fail logic and page run. It proves the Python
side and GUI only - not the firmware or the wiring. Faults: `dead_y2` (encoder does not
follow), `crossed` (Y move shows on Z's encoder), `noisy_z` (failed I2C reads).

## Before running

1. Mux `0x70` on A4/A5 (`Wire2`) and the AS5600s on the channels above; magnets fitted.
   Check the motors actually have a place for the magnet (plan 3.3 - single-shaft motors).
2. Stepper prerequisites as in `stepper_smoke_test/README.md` (Vref, microstep jumpers,
   A-socket clone jumpers, 12 V and a common ground). Motors clamped, nothing on the gantry.
3. Run **Scan mux channels** first: expect `AS5600 0x36` on ch3, ch5, ch6, ch7 (and `ToF 0x29` on ch0 if
   that is wired). Anything else is wiring, not code.

## Deploy

```bat
:: 1. one-time - scaffold the app on the board
tools\uno_q\ssh.bat
arduino-app-cli app new encoder_smoke_test
::    edit ~/ArduinoApps/encoder_smoke_test/app.yaml so ports lists 5071
exit

:: 2. push (repeat after every edit)
tools\uno_q\push-encoder-test.bat

:: 3. build + flash + run, on the board
tools\uno_q\ssh.bat
arduino-app-cli app restart ~/ArduinoApps/encoder_smoke_test -v
```

The first start needs internet once (the container pip-installs `python/requirements.txt`).
If the network port is not reachable, `adb forward tcp:5071 tcp:5071` and open
`http://localhost:5071/` (see `io_smoke_test/README.md`). The sketch also prints the status
line on USB serial twice a second.

Run only one of this app, `io_smoke_test` and `stepper_smoke_test` at a time: they all
drive the same board.

**GUI loads but says "not connected" and the log shows `enc_status ... timed out`?** (seen 2026-10-19)
Check no other app is running (`arduino-app-cli app list`), then on the board
`sudo systemctl restart arduino-router` and restart this app. See
`documents/programming/gantry_bench_bringup_2026-10-19.md`.

## Known unverified

- (Resolved 2026-10-18) The sketch compiles, and the mux is on `Wire2` (A4/A5), not `Wire`.
  Which bus the Qwiic port is: `Wire1` (i2c4) by the board's bus order; not tested.
- The AS5600 STATUS bytes read `0x67` and `0x33`, with bits set that the datasheet lists as
  reserved. The magnet bits (MD/ML/MH) decode plausibly, but treat them with caution until a
  shaft turn confirms them.
- With the encoder app running, one hang was seen at 08:50 and the board rebooted; the cause
  was not found (suspected power). The app does I2C only in `loop()`; `Bridge.begin()` is first.
- The Bridge path (`Bridge.provide` with an `int` argument, `Bridge.call(name, arg)`) - copied
  from `io_smoke_test`, which passes a single `int`; this app does the same.
- `Wire` timeouts: a hung bus may block a read instead of failing it.
- 3200 steps per motor revolution (200 full steps x 16 microsteps) - this test is what
  measures it. If the out-leg reads, say, 180 deg, steps/rev or the microstep jumpers are
  wrong, not the encoder.
- Sampling: every 64 steps is ~11 deg of motor travel at this speed, well inside the
  half-revolution limit for unwrapping; an I2C read time over the move has not been measured.
- The mux RESET pin `A1` is a proposal (the I2C plan lists A1/A2 as free; the CNC Shield
  calls A1 "Hold").

## Reading the result

| Observation | Meaning |
|---|---|
| Scan shows no `0x36` on a channel | That encoder or its mux channel is not wired / powered |
| Dial moves when you turn a *different* motor | Mux channels crossed |
| "no encoder movement" but the motor turned | Magnet missing / out of range, or the encoder is not on the shaft |
| Out-leg about half or double the expected | Steps per rev / microstep jumpers, or the encoder is on a geared axis (set `deg_per_motor_rev`) |
| "did not return" | Lost steps on the reverse leg, or the encoder slipped |
| Failed I2C reads during the move | Motor noise on the bus - plan 3.2 (shorter cable, 100 kHz, shielding) |
| Direction `-` | Encoder counts opposite to the motor's forward - note it for the real firmware |

## Clean up

```
arduino-app-cli app destroy user:encoder_smoke_test
```

## Cross-references

- `documents/electrical/i2c_bus_plan.md` (sections 2, 3.2, 3.3, 3.5, 5).
- `pick_place_node/bench_tests/stepper_smoke_test/` and `io_smoke_test/` (siblings).
- Protocol this borrows its shape from: `documents/programming/pick_place_control_protocol.md`.
