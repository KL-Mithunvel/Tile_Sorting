# I/O smoke test

Bench bring-up app for the pick-and-place gantry — checklist tasks 0.1/0.6
(`documents/pick_place_todo.md`). Lets you check the E-stop loop and all 3
limit switches, and switch the vacuum solenoid on/off, from a browser GUI
instead of a serial terminal. **Not** the real gantry firmware (that stub
lives at `pick_place_node/sketch/sketch.ino`; design in
`documents/programming/pick_place_control_protocol.md`). Sibling to
`bench_tests/stepper_smoke_test/` — same "throwaway hardware-bring-up code,
outside the node's App Bricks project" role.

## What it does

- MCU (`sketch/sketch.ino`) reads E-stop (`A0`), 3 limit switches
  (`D9`/`D10`/`D11`), and drives the vacuum-solenoid output (`D12`) — see
  `documents/electrical/schematics/pick_place_hardware_connections_plan.md` §3
  for the pin table. All 4 stepper drivers are held disabled (`EN`/`D8` HIGH)
  for the whole test; this sketch never touches STEP/DIR.
- It reports status over serial ~5 Hz and accepts `VAC ON` / `VAC OFF`.
- The Python side (`python/`) opens that serial link, and serves a Flask GUI
  (`http://<board-ip>:5070/`) showing live E-stop/limit-switch readings and a
  vacuum on/off button.

## Known discrepancy — RESOLVED for the limit switches 2026-10-17

Measured: X/Y/Z read LOW at rest and HIGH when pressed, i.e. triggered = HIGH. The text below is the original reasoning; the e-stop (A0) reads HIGH idle / LOW pressed, so the doc was right for it.

The wiring doc's pin table says `LOW = triggered / wire broken` for the
E-stop and limit-switch inputs. For an NC switch wired signal→GND with
`INPUT_PULLUP`, that's backwards on paper: an actuated NC switch (or a
broken wire) **opens** the circuit, which the pull-up reads as **HIGH**, not
LOW — and the doc's own "fail-safe: a broken wire reads as triggered"
reasoning only holds if triggered = HIGH.

So this tool doesn't guess: the GUI shows the **raw pin level** (LOW/HIGH)
for each channel, not a pre-interpreted triggered/ok label. **Press each
limit switch by hand and open the E-stop loop, and watch which level each one
shows** — that's the real polarity for your wiring. Once confirmed, update
the pin table note in `pick_place_hardware_connections_plan.md` §3 (and this
README) to match reality instead of the current guess.

## Known unverified

- **The App Lab container has none of this app's Python packages.** Observed
  2026-10-16: the first run died with `ModuleNotFoundError: No module named 'flask'`.
  App Lab builds a per-app venv (`.cache/.venv`) and installs only what is in
  `python/requirements.txt`, so that file (`flask`, `pyyaml`, `pyserial`) is part of
  the app and is pushed with it. The first start needs internet for pip. Whether the
  container can open the serial device (below) is still unverified.
- **There is no serial device for the MCU in the container** (observed
  2026-10-16: `/dev/ttyACM0` does not exist; `/dev/ttyHS1` is the MCU link
  owned by `arduino-router`). So the Python side no longer uses pyserial: it
  calls the sketch through the App Lab Bridge (`python/bridge_backend.py`:
  `Bridge.call("io_status")` / `("vac", 0|1)`), and the sketch registers them
  with `Bridge.provide()`. The sketch's `Serial` output still streams to the
  laptop over USB (COM8) — handy for reading the raw levels with no GUI. The
  Bridge path is written from Arduino's example, **not yet confirmed working**.
  `serial_backend.py` is kept only for its `SharedState`/`load_config`.
- **`python/main.py` does not use the `arduino.app_utils.App.run()` pattern**
  the other nodes' stubs use — see that file's docstring for why (this app's
  Python side has real background work to do, unlike `stepper_smoke_test`'s
  no-op Python side, and stacking an unverified `App.run()` behaviour on top
  of an already-unverified serial port wasn't worth it). If `arduino-app-cli`
  turns out to need `App.run()` specifically for the container to be
  considered "running," that's the first thing to revisit.

## Verified on the lab board, 2026-10-16

- Build, flash and container start work; `python/requirements.txt` is installed by
  the container on first start (needs internet once).
- The GUI reads the sketch through the Bridge: `/api/status` returned
  `connected: true` with live levels. With nothing wired every input read HIGH
  (pull-up), so that run says nothing about switch polarity yet.
- The board's generated `app.yaml` has `ports: []`, which leaves the container's
  5070 unreachable from the network; edit it to list `5070` (as the reference
  `app.yaml` here does) and restart.
- The laptop can reach the GUI over USB with
  `adb forward tcp:5070 tcp:5070` -> `http://localhost:5070/` (adb is in
  `%LOCALAPPDATA%\Arduino15\packages\arduino\tools\adb\<ver>\`). Raw levels also
  stream on the board's USB serial port (COM8 here).
- **The GUI port is 5070, not 5060.** Chrome and Edge refuse port 5060 (and 5061,
  the SIP ports) with `ERR_UNSAFE_PORT`, while `curl` and the status API on 5060
  worked fine — so the server looked healthy and the page still would not open.
  Avoid browser-restricted ports for any dashboard here.
- **Polarity measured 2026-10-17 (limit switches X/Y/Z): LOW at rest, HIGH when pressed.** This is what NC to GND with a pull-up predicts, so the wiring doc's "LOW = triggered" was backwards (now corrected there). E-stop A0 is the opposite: HIGH when not pressed, LOW when pressed. The vacuum output D12 switches on and off from the GUI. Not yet done: wire-unplugged level for the limits and A0.

## Before running — hardware prerequisites

1. E-stop loop's NC contact wired `A0`↔`GND`; each limit switch's NC contact
   wired `D9`/`D10`/`D11`↔`GND` (hardware doc §3).
2. Vacuum solenoid's MOSFET/relay gate on `D12`, flyback diode across the coil
   (hardware doc §2/§5). Confirm the MOSFET/relay's own supply is live before
   expecting the vacuum output to actually do anything — `D12` only switches
   it, it doesn't power it.
3. Motors can stay connected; the motor PSU doesn't need to be live for this
   test (steppers are held disabled throughout).

## Deploy

Board connection: `tools/uno_q/` (see its README). The board must be
reachable (`ping <UNO_Q_HOST>`).

```bat
:: 1. one-time — scaffold + register the app on the board
tools\uno_q\ssh.bat
::    then on the board:
arduino-app-cli app new io_smoke_test
exit

:: 2. push this app (repeat after every edit)
tools\uno_q\push-io-test.bat

:: 3. build + flash + run, on the board:
tools\uno_q\ssh.bat
arduino-app-cli app restart ~/ArduinoApps/io_smoke_test -v   :: ~90 s
```

Then open `http://<UNO_Q_HOST>:5070/` (or whatever the board's current hotspot
IP is — see `tools/uno_q/config.bat`) from any browser on the same WiFi.

If the GUI never shows a connection (banner stays red, "Not connected to the
board's serial port yet"), that's the serial-port-path unknown above — check
`journalctl`/the app's own stdout on the board for what `SerialIOBackend`
actually failed to open.

`push-io-test.bat [app_name]` takes an optional app name (default
`io_smoke_test`), same convention as `push-stepper-test.bat`.

## Reading the result

| Observation | Meaning |
|---|---|
| GUI connects, all 4 tiles show a level, vacuum button toggles the coil | Wiring + serial link OK — go note the real trigger polarity (above) |
| GUI shows "Not connected" indefinitely | Serial port didn't open — see Known unverified |
| A tile never changes when you press that switch / open the loop | That circuit's wiring, not the code — check continuity to the pin and to GND |
| Vacuum button toggles in the GUI but the coil doesn't click/the pump doesn't run | `D12`→MOSFET/relay gate wiring, or the MOSFET/relay's own supply isn't live |

## Clean up

```
arduino-app-cli app destroy user:io_smoke_test
```

## Cross-references

- Wiring / pin table: `documents/electrical/schematics/pick_place_hardware_connections_plan.md` §3.
- Protocol this borrows its shape from: `documents/programming/pick_place_control_protocol.md` §5.
- Checklist: `documents/pick_place_todo.md` §0.1, §0.6.
- Sibling bench app: `pick_place_node/bench_tests/stepper_smoke_test/`.
