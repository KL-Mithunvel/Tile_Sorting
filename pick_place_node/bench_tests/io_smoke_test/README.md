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
  (`http://<board-ip>:5060/`) showing live E-stop/limit-switch readings and a
  vacuum on/off button.

## Known discrepancy — read this before trusting "triggered"

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

- **`pyserial` may not be preinstalled in the App Lab Python container.** It's now in
  the repo-root `requirements.txt` (for the dev-laptop venv), but nothing pushes that
  file alongside this app the way `push.bat` does for `acoustic_node/` — if
  `python/main.py` fails on `import serial` on the board, `pip install pyserial` there
  (or inside the container, whichever `arduino-app-cli`'s error points at).
- **The serial port path on the board's Linux side is a placeholder**
  (`python/config.yaml`'s `serial.port: "/dev/ttyACM0"`). Whether the App Lab
  Python container this app runs in can even open a device node for the
  sketch's `Serial` link is untested — same class of unknown as the App
  Bricks bridge API elsewhere in this repo
  (`pick_place_control_protocol.md` §9). If `SerialIOBackend.start()` fails to
  open the configured port, SSH onto the board (`tools/uno_q/ssh.bat`) and
  `ls /dev/tty*` to find candidates, then edit `config.yaml`.
- **`python/main.py` does not use the `arduino.app_utils.App.run()` pattern**
  the other nodes' stubs use — see that file's docstring for why (this app's
  Python side has real background work to do, unlike `stepper_smoke_test`'s
  no-op Python side, and stacking an unverified `App.run()` behaviour on top
  of an already-unverified serial port wasn't worth it). If `arduino-app-cli`
  turns out to need `App.run()` specifically for the container to be
  considered "running," that's the first thing to revisit.

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
reachable (`ping 172.20.10.2`).

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

Then open `http://172.20.10.2:5060/` (or whatever the board's current hotspot
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
