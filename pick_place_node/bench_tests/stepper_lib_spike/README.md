# Stepper-library spike

Bench bring-up app for the pick-and-place gantry — **checklist task 0.6**
(`documents/pick_place_todo.md`), the item the checklist calls *"the single biggest
firmware risk"*. Sibling of `bench_tests/stepper_smoke_test/` (does the motor turn?) and
`bench_tests/io_smoke_test/` (do the switches read?). **Not** the real firmware.

> **Status: written, never compiled, never run.** It was written on a machine with no
> `arduino-cli` and no Zephyr toolchain. The step-generator *maths* was checked off-target;
> the C++ was not. A compile error is not a failed spike — it is the answer to "does this
> build on the Zephyr core", and the table below says what to do with each one.

## The question

On the UNO Q's Zephyr core (`arduino:zephyr:unoq`, STM32U585), can we generate
**accelerated STEP pulses without blocking `loop()`**? The real firmware must keep reading
limit switches, the e-stop and the serial protocol while an axis moves, so a blocking
`delayMicroseconds` loop (what the smoke test does) is not an option.

## What it measures

One sketch, one comparison table. Every candidate performs the **same move**: 6400 steps
(2 rev at 1/16 microstep) forward then back, 4000 steps/s cruise, 8000 steps/s² — while
`loop()` prints a character every 100 ms, standing in for "limit switch + serial protocol +
telemetry".

| Stage | What it is | Runs only if |
|---|---|---|
| **baseline** | `micros()` resolution · cost of a STEP pulse pair (⇒ max bit-bang rate) · `delayMicroseconds(10)` accuracy · cost of a `Serial.println` · what a Zephyr `k_timer` actually delivers at 200/100/50/25 µs | always |
| **AccelStepper** | the library, polled from `loop()` | the library is installed |
| **k_timer ISR** | a hand-written trapezoid generator in a kernel-timer callback (integer fixed-point — no FPU in the ISR) | `<zephyr/kernel.h>` compiles |

Per stage it reports: steps produced, time taken vs the ideal trapezoid, and the **worst
gap between two `loop()` iterations** (`max_loop_gap_us`) — the number that says whether
`loop()` stayed responsive.

## Safe by default

`DRIVERS_ENABLED` is **0**: the shield's `EN` (D8) is held HIGH, all four drivers are off,
STEP pulses go nowhere and **nothing moves** — even with 12 V on the shield. That is enough
to answer the timing question, because everything it reports is measured in software.

Set `DRIVERS_ENABLED` to **1** only with the motor **off the gantry and clamped**, Vref set,
and the golden rules from `documents/pick_place_todo.md` observed. Then it also moves the
X motor 2 rev forward, 2 rev back per stage, so you can judge smoothness by ear/hand.
Send any character over the serial monitor to abort (drivers disabled).

## Run it

Board connection: `tools/uno_q/` (see its README).

```bat
:: 1. one-time — scaffold + register the app on the board
tools\uno_q\ssh.bat
::    then on the board:
arduino-app-cli app new stepper_lib_spike
exit

:: 2. push (repeat after every edit)
tools\uno_q\push-stepper-spike.bat

:: 3. build + flash + run, then read the table — on the board:
tools\uno_q\ssh.bat
arduino-app-cli app restart ~/ArduinoApps/stepper_lib_spike -v    :: ~90 s
arduino-app-cli monitor                                           :: needs a real TTY
```

The report is printed once the run finishes and **repeated every 5 s**, so you can attach
the monitor late. Copy everything from `=== stepper-library spike` to the closing
`=====` line and send it back.

If `AccelStepper` is not found: `sketch/sketch.yaml` asks for it, but the App Lab schema is
unverified — install it on the board instead and restart:
`arduino-cli lib install "AccelStepper@1.64"`.

## Reading the result

A good run looks like this (numbers are illustrative, not measured):

```
=================== SPIKE RESULT ===================
DRIVERS_ENABLED=0  move=6400 steps fwd + back, vmax=4000 sps, accel=8000 sps^2
baseline: micros_res_us=1  step_pair_us=0.9  delay10_us=10.3  println_us=180
k_timer ask 50us -> 2000/2000 callbacks per 100 ms
AccelStepper (polled): steps=12800 time_ms=4170 ideal_ms=4200 err%=-0.7 max_loop_gap_us=380 prints=41
k_timer ISR generator: steps=12800 time_ms=4160 ideal_ms=4200 err%=-1.0 max_loop_gap_us=210 prints=41
```

**Pass criteria** (per stage): `steps` = 2 × 6400 · `|err%|` within ~5 (a small *negative*
value is normal — the generator starts at 100 steps/s, not 0) · `max_loop_gap_us` well under
~5000 while the prints run.

| Observation | Meaning | Use for the firmware |
|---|---|---|
| **AccelStepper passes** (gap small, err small) | Simplest route works; `run()` polled from `loop()` is fast enough at this rate | Use it for firmware v0. Keep the e-stop/limit checks *inside the same loop* and re-measure with all 3 axes moving |
| AccelStepper `err%` large / steps short | `loop()` or `digitalWrite` too slow for 4000 sps | Go to the ISR generator, or lower `MAX_SPEED` and re-test |
| AccelStepper `max_loop_gap_us` ≫ 5000 | Serial printing (or something) stalls the step loop | Keep serial output out of the step path (buffer + send from a lower-priority task) |
| **k_timer passes** and callbacks per 100 ms ≈ ideal | Kernel timer is fine-grained enough → ISR-driven generation is available | Preferred design for 3 axes: one tick, three DDA phases; loop() free for protocol/limits |
| k_timer callbacks ≪ ideal (e.g. 100 per 100 ms at 50 µs) | Kernel tick is coarse (≈ 1 ms): `k_timer` cannot be the step clock | Need a hardware timer through a Zephyr counter/PWM driver — a deeper spike; tell me the baseline numbers |
| `tick_us min/max` far apart | Jitter in the ISR timing | Note it; judge by the motor (`DRIVERS_ENABLED=1`): audible stutter ⇒ too much jitter |
| `step_pair_us` ≫ 2 | `digitalWrite` is slow on this core | The ISR budget is mostly GPIO; consider port-level writes via the Zephyr GPIO API |
| `println_us` in the thousands | Serial blocks | Same as the gap row: keep serial off the step path |

## If it does not compile

| Compiler says | Do |
|---|---|
| `zephyr/kernel.h: No such file` / `k_timer_*` / `K_USEC` undeclared | Set `#define SPIKE_ZEPHYR_TIMER 0` at the top of `sketch.ino` — you still get the baseline and the AccelStepper stage. **Send me the exact error**: it tells us whether sketches can reach the Zephyr kernel API at all. |
| `AccelStepper.h` missing | Not an error — the stage is skipped and the table says "NOT installed". Install it (above) to get that stage. |
| anything in AccelStepper itself | Send it; that is the "library does not build on this core" answer |

## Not covered (deliberately)

- Three axes at once, the Y-clone pair, homing, limits, e-stop — this is a *generator*
  spike. Whatever passes gets re-measured with all three axes in the real firmware.
- Hardware timers via the Zephyr counter driver (needs device-tree access a sketch does not
  have) — only worth doing if the `k_timer` granularity row says it is needed.
- Encoder reads (AS5600 over I²C) — they must run at low priority and never stall step
  generation (`pick_place_control_protocol.md` §5.6); a separate bench item.

## Clean up

```
arduino-app-cli app destroy user:stepper_lib_spike
```

## Cross-references

- Checklist: `documents/pick_place_todo.md` §0.6.
- Design: `documents/programming/pick_place_control_protocol.md` §2, §3.1, §9.
- Executable protocol spec the real firmware must satisfy:
  `pick_place_node/python/pick_place/fake_mcu.py`.
- Siblings: `bench_tests/stepper_smoke_test/`, `bench_tests/io_smoke_test/`.
