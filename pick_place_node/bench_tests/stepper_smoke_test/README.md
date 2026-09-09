# Stepper smoke test

Bench bring-up sketch for the pick-and-place gantry — **checklist task 0.5**
(`documents/pick_place_todo.md`). Confirms that all four CNC Shield V3.10 stepper
motors physically move. **Not** the real gantry firmware (that stub lives at
`pick_place_node/sketch/sketch.ino`; design in
`documents/programming/pick_place_control_protocol.md`).

`bench_tests/` sits outside the node's own App Bricks project on purpose — it is
throwaway hardware-bring-up code, deployed as its own short-lived `arduino-app-cli`
app, not part of the pick-and-place node's shipping code.

## What it does

Firmware drives only 3 STEP/DIR pairs — X (D2/D5), Y (D3/D6), Z (D4/D7), shared
active-LOW enable on D8. The 4th motor (shield **A** socket) is **cloned from Y in
hardware** via the shield's clone jumpers, so the "Y" phase turns *both* Y-beam
motors at once.

Loop: X → Y+A → Z, each 1 rev forward + 1 rev back at ~0.26 rev/s, 3 s pause, repeat.
Send any character in the serial monitor to stop (drivers disabled); reset to restart.

## Before running — hardware prerequisites

From `documents/pick_place_todo.md` §0.1–0.4 and the golden rules:

1. **Vref set on all 4 TMC2208** (~1.6 V, motor disconnected, VMOT present). The A
   driver's Vref **must equal** the Y driver's.
2. **MS1 and MS2 jumpers closed** on all 4 drivers (= 1/16 microstep).
3. **A-socket clone jumpers fitted** to clone Y. Without them the 4th motor is never
   driven — expected, not a fault.
4. **12 V LRS-150-12** wired to the shield VMOT terminals, single common ground to the
   UNO Q, inline ~8–10 A fuse on V+.
5. **Never** seat/unseat a driver or a motor connector with power on.
6. Motors **clamped down / off the gantry**.

## Deploy

Board connection: `tools/uno_q/` (see its README). The board must be reachable
(`ping 172.20.10.2`) — it is on the owner's laptop hotspot.

```bat
:: 1. one-time — scaffold + register the app on the board (generates its app.yaml)
tools\uno_q\ssh.bat
::    then on the board:
arduino-app-cli app new stepper_smoke_test
exit

:: 2. push this sketch (repeat after every edit)
tools\uno_q\push-stepper-test.bat

:: 3. build + flash + run, then watch serial — on the board:
tools\uno_q\ssh.bat
arduino-app-cli app restart ~/ArduinoApps/stepper_smoke_test -v   :: ~90 s
arduino-app-cli monitor                                           :: needs a real TTY
```

`push-stepper-test.bat [app_name]` takes an optional app name (default
`stepper_smoke_test`) if you'd rather target an existing app such as the teammate's
`cnc`.

## Reading the result

| Observation | Meaning |
|---|---|
| All of X, both Y-beam motors, and Z turn each cycle | All 4 motors + wiring + drivers OK |
| Y turns but its partner (A) does not | A-clone jumpers not seated, or A driver Vref/seating |
| One axis silent / buzzing / stuttering | That driver's Vref too low, coil-pair miswired, or STEP/DIR not reaching it |
| Nothing moves, drivers get warm | EN wiring, or VMOT not present |
| Nothing moves, drivers cold | No driver enable / no logic — check D8, check the flash succeeded |

If STEP/DIR at 3.3 V proves marginal (missed steps, only through direction changes),
apply the `documents/pick_place_todo.md` §0.3 fallback (feed shield logic from 3V3, or
a 74AHCT125).

## Clean up

```
arduino-app-cli app destroy user:stepper_smoke_test
```
