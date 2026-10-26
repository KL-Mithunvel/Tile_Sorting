# Gantry bench bring-up notes, 2026-10-19

Session notes from getting the encoder test GUI running on the lab UNO Q with the gantry built.
Observations only; nothing here moved a motor. More (X/Z directions, encoder results) to be added.

## What to run, in order

| Step | App (`pick_place_node/bench_tests/...`) | Proves | Push |
|---|---|---|---|
| 1 | `stepper_smoke_test` | all 4 motors turn, no feedback | `tools\uno_q\push-stepper-test.bat` |
| 2 | `i2c_scan_test` | mux `0x70` and AS5600 `0x36` on the expected channels | no script (pscp by hand) |
| 3 | `encoder_smoke_test` (GUI :5071) | each encoder follows its motor | `tools\uno_q\push-encoder-test.bat` |
| 4 | real firmware `pick_place_node` + `gantry_fw_test/python/home_run.py` | homing, encoder checks, alarms | `tools\uno_q\push-pick-place.bat` |

Only one app can run at a time (they share the MCU). Stop the running one first:
`arduino-app-cli app stop user:<name>`, then `arduino-app-cli app restart ~/ArduinoApps/<name> -v`.

## Wiring recorded

TCA9548A mux `0x70` on A4 (SDA) / A5 (SCL) = `Wire2`, RESET on A1. AS5600 encoders (all `0x36`):

| Motor | Mux channel |
|---|---|
| X | 7 |
| Y | 5 |
| Y-clone | 6 |
| Z | 3 |
| ToF 1 (`0x29`) | 0 |

Source: `documents/electrical/i2c_bus_plan.md`, `ENC_MUX_CH` in the encoder test sketch. The real firmware
lists `{7, 5, 3, 6}` for X, Y, Z, Y' (same channels per motor). Real firmware `GANTRY_ENCODER_MASK` is `0x06`
(Y, Z); X was not fitted on 2026-10-18. No encoder was connected on 2026-10-19.

## Axis direction

Y: negative movement goes toward home (owner, 2026-10-19). Matches `home_dir = -1` in
`gantry_core.h`. X and Z not recorded. Not confirmed by a `HOME Y` run.

## Problem: GUI "not connected" (fixed)

Network was fine (board `192.168.1.15` reachable, SSH open). Causes, in order found:

1. `encoder_smoke_test` was stopped and `pick_place_node` was running and owned the MCU.
2. After stopping it and flashing the encoder app, the page loaded (HTTP 200) but the status stayed
   `connected: false`; log: `bridge enc_status failed: Request 'enc_status' timed out after 2s`.
3. Flashing the minimal `i2c_scan_test` failed the same way (`method i2c_scan not available`), so the
   fault was the MCU-to-Linux Bridge, not the encoder code.
4. **Fix: `sudo systemctl restart arduino-router` on the board**, then restart the app. The scan app then
   answered (`Wire2: 0x70`) and the encoder GUI showed `connected: true`, `mux_ok: true`.

The board had rebooted at 12:32 that day; why the router stopped seeing the MCU's methods after the
reflash is not known. The router log shows 37 "invalid packet, expected array" lines at every flash;
that is bootloader noise, not the fault. If the Bridge stops answering after a reflash, restart the router first.

## State at the end of the session

`encoder_smoke_test` running; drivers off; all four encoders read `-1` (none fitted). `pick_place_node`
and `i2c_scan_test` stopped. Not done: motor moves, encoder tests, real-firmware homing.
