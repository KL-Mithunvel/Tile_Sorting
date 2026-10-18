# Gantry firmware (UNO Q MCU)

Written 2026-10-12. **The logic is verified on a PC; the board glue is not.**

| File | What it is | Verified |
|---|---|---|
| `gantry_core.h` | Everything that decides things: line-protocol parser, machine FSM, trapezoid step generator (integer maths, ISR-safe), homing (Z, then X, then Y), limit / e-stop handling, soft limits, vacuum confirm + timeout, closed-loop check, link watchdog. Portable C++11, header-only, no Arduino includes. | Compiled and run on a PC: `tests/test_gantry_firmware_conformance.py` (51 tests) |
| `sketch.ino` | Board glue: pins, `k_timer` step ISR, Bridge line transport (`g_cmd` / `g_poll`), vacuum sensor, AS5600 reads through the TCA9548A mux (`Wire2`). | **Compiled for the UNO Q and flashed 2026-10-18** (98 KB, 12% flash; 44 KB RAM). Over the Bridge it answers `PING`, `GETSTATE`, `GETPOS` and sends a heartbeat. **No motor, limit, e-stop, vacuum or encoder has been exercised from it.** |
| `../firmware_host/` | `gantry_host.cpp` (the core behind a simulated machine, speaking the protocol on stdin / stdout) and `host_mcu.py` (drives it like a serial MCU). Test tooling, not shipped. | — |

## Run the checks

Needs a C++11 compiler. Anything on PATH works (`c++`, `g++`, `clang++`); otherwise point
`GANTRY_HOST_CXX` at one — for example, with no compiler installed:

```bash
pip install --target /some/empty/dir ziglang
export PYTHONPATH=/some/empty/dir
export GANTRY_HOST_CXX="python -m ziglang c++"
python -m pytest tests/test_gantry_firmware_conformance.py -q
```

Without a compiler those tests are skipped, not failed.

Part 1 of the tests runs every scenario against `fake_mcu.py` (the Python spec) **and** the compiled core;
a divergence shows as one side failing. Part 2 is firmware-only behaviour. To poke at it by hand:

```bash
c++ -std=c++11 -O2 -I pick_place_node/sketch -o gantry_host pick_place_node/firmware_host/gantry_host.cpp
printf '1 HOME ALL\n@tick 5000\n2 MOVE X100 F6000\n@tick 3000\n' | ./gantry_host --fast
```

(`@…` lines are harness controls — see the header of `gantry_host.cpp`.)

## Before it touches a motor (open, in order)

1. ~~Compile it for the board.~~ Done 2026-10-18. It needed one change in the core: `llroundf` links to
   `__aeabi_f2lz`, which this core does not provide, so `fmtMm` uses `lroundf` and clamps at +/-2e6 mm
   (51 conformance tests re-run and pass). `irq_lock()` and `k_timer` compile. Still unverified at run time:
   a `k_timer` callback running `digitalWrite` + `delayMicroseconds`, and `analogRead` resolution.
2. **`stepper_lib_spike` (checklist 0.6)** — can a `k_timer` really deliver `tick_us = 50`? If the best
   period is coarser, raise `Config::tick_us`; the core caps speed at 0.8 step per tick on its own.
3. **Limit / e-stop polarity.** `sketch.ino` assumes NC-to-GND + pull-up, i.e. *pressed or broken reads
   HIGH*. The wiring doc says the opposite; `io_smoke_test` settles it. Flip
   `GANTRY_LIMIT_TRIGGERED_LEVEL` / `GANTRY_ESTOP_ASSERTED_LEVEL`.
4. **Steps per mm.** `Config::defaults()` assumes 1/16 microstepping, GT2 20T (40 mm/rev) on X/Y and a
   T8 lead screw (8 mm/rev) on Z. Replace with measured values; `home_pos_mm`, `home_backoff_mm` and the
   homing speeds are placeholders as well.
5. **Direction.** `DIR_INVERT[]` in `sketch.ino`, per axis, after the first single-axis jog with the
   motor free.
6. **Vacuum sensor** (`A3`): analog level vs a threshold, or a switch? `GANTRY_VAC_*`.
7. **Encoders** (`GANTRY_ENCODERS_ENABLED`, off): TCA9548A channel map `{X:7, Y:5, Z:3, Y':6}` is the bench
   wiring of 2026-10-18 (ToF 1 on ch0), on **`Wire2`** (A4/A5 are not `Wire` on the UNO Q); the read-back of the mux channel is there because four
   identical `0x36` parts answer whatever is selected. Tolerances (`SETTOL`) are guesses — measure the
   dynamic error at speed before trusting `LOST_STEPS`.
8. **How Python reaches the MCU.** Decided and proven 2026-10-18: over the RouterBridge, not `Serial`
   (`Serial` is the router's monitor). The sketch provides `g_cmd(String line)` and `g_poll()` (returns
   queued reply lines, `
`-separated). The Python side is `pick_place/bridge_link.py`
   (`BridgeGantryLink`, `backend: bridge` in `config.yaml` / `--backend bridge`; polls `g_poll` every 20 ms).
   Checked on the board 2026-10-18 with `bench_tests/gantry_fw_test/` (PING / GETSTATE / GETPOS through a real
   `GantrySession`, nothing moved). **Not yet run on the board:** the job runner and `gantry_station.py`
   over this link, homing and any motion.

## Differences from `fake_mcu.py` worth knowing

The firmware does what the spec's table says, plus: a limit ahead of a moving axis stops it within one
step (the Python spec only has `inject_alarm`); `CLEARALARM` is refused while the e-stop loop is open;
per-axis homed flags (`HOME X` leaves the others as they were — the Python spec sets HOMED=1 after any
`HOME`, which `JobRunner` never exercises because it always sends `HOME ALL`); an unreadable encoder and a
homing run that never finds its switch are alarms (`ENCODER_<axis>`, `HOME_FAIL`).
