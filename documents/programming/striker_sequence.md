# The prototype striker: sequence, arming and safety

Added 2026-10-19. This is the reference for how the acoustic station's **single sprung solenoid striker** behaves, who
decides what, and what is and is not proven. Companion documents: `nano_serial_protocol.md` (the wire format),
`data_collection_sessions.md` (recording the strikes), `acoustic_node/nano_firmware/README.md` (the firmware),
`documents/project/project_charter.md` section 6.2 "Prototype Configuration" (why the prototype looks like this).

**Status.** Built and tested on the laptop (1,157 tests, including the real firmware core compiled and driven over the real
protocol module). Flashed to a real Nano and deployed to the Pi. On the rig so far: the ToF answers, the striker parked up,
held, released on a manual command and on a tile, lifted again, and the Service view showed it. **Not yet seen on the rig:**
the camera raising it, automatic arming, or the lowering to rest (the coil's power was switched off because it overheated).
Everything marked *provisional* below is a guess to be measured.

## 1. The hardware

| Part | Detail | Source |
|---|---|---|
| Striker | one solenoid above the tile, spring-loaded: **energised = retracted (up)**, **released = gravity + spring strike**. No ball, no ARM/LOCK. | owner, charter 6.2 |
| Driver | Cytron MDD10A: **D10 = PWM, D8 = DIR** (DIR held high; only the PWM energises). Pull-in at full power for 150 ms, then a lower **hold power** (default 30 %, settable). Timer1 runs at ~31 kHz so the coil does not hum into the microphone. | owner, 2026-10-19 |
| ToF | one VL53L0X "TINY LIDAR" module on I2C (A4 = SDA, A5 = SCL, 0x29). Sensor 3 (belt end) does **not** exist in the prototype. | owner |
| Controller | Arduino Nano (CH340), USB to the Pi, `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0`. Firmware: `acoustic_node/nano_firmware/`. | |
| Belt | turned by hand. Nothing here says anything about belt speed. | charter 6.2 |
| E-stop | Nano D2, normally-closed loop, `INPUT_PULLUP`; tripped = latched FAULT (coil off). | `nano_core.h` |

## 2. The idea in one paragraph

**The striker rests DOWN, so the coil is off and cool.** The camera is upstream of the striker; when it grades a tile the Pi
**arms** the striker and asks it to come **up**. The tile reaches the ToF; **2 s after the sensor recognises it the striker is
released** (the strike). **1.5 s later it lifts again**, even with the tile still under it, so the tile can be moved out. That
tile is never struck twice. Once the tile has gone and nothing more is coming, the Pi **disarms** and the Nano **lowers the
striker to rest**. *Armed only while a tile is in the system.*

Why it is built this way: first the striker rested **up** and the coil, energised for as long as the line idled, **overheated**
(owner switched its power off, 2026-10-19). Resting down puts the heat budget where it belongs: the coil is on only for the
few seconds a tile is actually being handled.

## 3. The sequence

```
        camera grades a tile
               |  Pi: STRIKE 1 (arm)  +  UP (a tile is coming)
               v
  [1 RESTING DOWN] ------------------------------------------------.
   coil off, cool                                                   |
               |  armed + (UP pending  OR  a tile already under     |
               |           the ToF = the camera missed it)          |
               v                                                    |
  [2 RAISED]  coil on, waiting for the tile to reach the sensor     |
               |  ToF <= 100 mm, 5 good samples (~125 ms)           |
               v                                                    |
  [3 TILE AT THE SENSOR]  2 s countdown (drops if the tile leaves)  |
               |  2 s, armed                                        |
               v                                                    |
  [4 STRIKE]   coil off: gravity + spring.  TAP src=tof            |
               |  +1.5 s (0.3 s strike + 1.2 s cooldown)            |
               v                                                    |
  [5 LIFTED]   coil on again, tile still there: it can be moved out |
               |  ToF reads no tile                                 |
               v                                                    |
  nothing in the system, nothing requested, 3 s ---> quiet lowering  |
  to rest (TAP src=rest, never paired with a tile) ------------------'
               |  Pi: STRIKE 0 (disarm) after 2 s of "nothing in the system"
```

### Timings

| What | Value | Where | Status |
|---|---|---|---|
| ToF poll / debounce | 25 ms / 5 samples (~125 ms) | `defaultConfig` | provisional |
| Tile threshold | **<= 100 mm** | `prototypeConfig` | **measured**: tile 83-94 mm, empty surface 112-149 mm. Only ~20 mm apart. |
| Release delay | **2000 ms** after recognition | `release_delay_ms` | owner's number |
| Lift after the strike | 1500 ms (strike 300 + cooldown 1200) | `strike_ms`, `cooldown_ms` | provisional: long enough for the ring to die before the lift noise |
| Rest delay | 3000 ms of nothing to do | `rest_delay_ms` | provisional |
| Forget an unanswered UP | 30000 ms | `up_timeout_ms` | **a guess**: the camera-to-sensor transit is unmeasured |
| Disarm grace (Pi) | 2.0 s | `station.striker.disarm_grace_s` | provisional |
| Heartbeat | every 250 ms (500 ms on the full-system core) | `hb_ms` | |
| Pull-in | 100 % for 150 ms | `nano_firmware.ino` | provisional |
| Hold power | **30 %** (settable 0-100 live) | `hold_pct`, `HOLD n` | **provisional; too low drops the striker, too high overheats** |

## 4. Who decides what

| Decision | Made by | How |
|---|---|---|
| "A tile is coming" | the camera process | grades a live tile on its line, publishes `tile/<id>/camera/result` |
| Send the Nano `UP` | Pi, `acoustic/striker_arming.py` `CameraArming` | one `UP` per graded tile (config `station.striker.arm_on_camera`) |
| Armed or not (`STRIKE 1/0`) | Pi, `AutoArm` (same file) | see section 5; config `station.striker.auto_arm` |
| Raise / countdown / strike / lift / lower | **the Nano**, `nano_core.h` | the Pi only requests and arms; the timing is on the Nano so a slow Pi or a lost message cannot change a strike |
| Labelling the strike for the dataset | Pi, `fusion/session.py` | `RET` / `TAP` events with their `src` |

The camera and the Nano never talk directly. The camera tells the bus, the Pi turns that into `UP` and arming, the Nano acts.

## 5. "Armed only while a tile is in the system"

`AutoArm` arms the striker (`STRIKE 1`) while **any** of these holds, and disarms (`STRIKE 0`) after `disarm_grace_s` of none:

1. a **camera-graded tile has not yet had its tap** (the station's ledger, `coordinator.awaiting_tiles()`);
2. the Nano holds a **pending `UP`** (`up_wanted` in its heartbeat);
3. a **tile is under the ToF** (reading <= `tile_mm`), which covers a tile the camera missed and the tile just struck that has
   to be lifted off.

It decides from what the Nano reports back (`armed`, `tof1_mm`, `up_wanted`), not from what it last sent, and repeats an
unconfirmed command once a second. Deliberately **not** a reason: the Nano's own striking/cooldown state (the quiet lowering to
rest passes through it and counting it re-armed the striker after every rest; found by the whole-life test).

It does nothing in **MANUAL** mode, with a dead link or stale status, or when `auto_arm` is off (then the **Armed** button is the
operator's switch). Disarmed, the Nano does not raise the striker, and a raised striker with nothing to do lowers itself.

## 6. Modes and commands

| Mode | Behaviour |
|---|---|
| **AUTO** (default) | the sequence above. |
| **MANUAL** | the ToF and the camera are ignored. `RETRACT` energises and holds; `RELEASE` strikes; `TAP` does one full retract-hold-release. Needs `STRIKE 1`. A refused command answers `ERR NOTMANUAL / INHIBITED / BUSY / NOTRETRACTED / FAULT`. Switching to MANUAL while up keeps it up as a held retract. |

Pi to Nano: `MODE 0/1`, `STRIKE 0/1`, `UP`, `HOLD n`, `RETRACT`, `RELEASE`, `TAP`, `BELT n`, `PING n`. Nano to Pi: `HELLO`, `HB` (mode,
retracted, ToF mm, armed, state, ms left, must-leave, hold power, up-wanted), `RET`, `TAP` with a source, `PONG`, `ERR`. See
`nano_serial_protocol.md`. Bus topics: `station/striker/cmd` (actions `mode`, `retract`, `release`, `cycle`, `up`, `hold`),
`station/strike`, `station/retract`, `station/tap`, `station/nano/status`.

## 7. Safety behaviour

| Situation | What happens |
|---|---|
| E-stop | coil off, latched FAULT until reset. If it was up, the drop is reported first as a `safety` strike. |
| Pi / cable dies (no valid line for 2 s) | the Nano's watchdog **disarms** it; a raised striker with nothing to do **lowers itself** 3 s later (`rest`). A tile cannot release it with the Pi gone. |
| Not armed | it does not rise; a tile cannot release it. |
| ToF stops answering | it will not rise; if it dies while up it lowers after the rest delay. "Cool beats blind." |
| Tile under the striker when it would lower | it is **never lowered onto a tile**; it stays up until the tile has gone. |
| MANUAL hold longer than 8 s | released as a `safety` strike (heat). |
| Camera misses a tile | the ToF raises it and the tile is still struck (a late retract, not a missed strike). |
| UP for a tile that never arrives | forgotten after 30 s, then lowered. |
| Hold power too low | the striker drops **by itself**, an unreported strike. This is why the GUI warns next to the slider. |
| Coil overheats | the original failure. Mitigations: rests down, lower hold power, lowers when idle. **Coil temperature has not been measured.** |

## 8. What the data records

Every raise is a `RET`, every release a `TAP`; each carries a **source**: `tof` (a tile, a real measurement), `manual`, `safety`,
`tof_blind` (ToF unreadable at the strike), `rest` (a lowering with nothing to strike). Only `tof` strikes are paired with a camera tile.
`manual`, `safety` and `rest` strikes are recorded and labelled but use up no tile's tap slot. See `data_collection_sessions.md`.

## 9. The Service view (`:5001/service`) Striker panel

A phase banner, a countdown bar, a five-step strip and a table (ToF distance and verdict, striker up/down, whether the camera asked,
armed as read back from the Nano, whether this tile was already struck, last strike, link). Buttons: **Mode**, **Auto-arm** (with
the reason it is or is not armed right now), **Armed** (greyed while auto-arm is on), **Retract / Release / Retract + release**,
and the **Hold power** slider. Banner texts: `NANO NOT CONNECTED`, `ToF SENSOR NOT ANSWERING`, `RESTING DOWN - waiting for the camera`,
`RAISING - the camera saw a tile`, `RAISED by the camera - waiting for the tile to reach the sensor`, `TILE AT THE SENSOR - striking in N s`,
`STRUCK - lifting in a moment`, `LIFTED - move the tile out`, `NOTHING COMING - lowering to rest`, `MANUAL`.

## 10. Configuration

| Where | Key | Default |
|---|---|---|
| `acoustic/config.yaml` | `station.striker.arm_on_camera` | true |
| | `station.striker.auto_arm` | true |
| | `station.striker.tile_mm` | 100 (keep equal to the firmware's) |
| | `station.striker.disarm_grace_s` | 2.0 |
| | `station.nano.enabled / port` | per the Pi (by-id path) |
| `nano_core.h` `prototypeConfig()` | tile threshold, release delay, cooldown, hold power, request-only, timeouts | table in section 3 |
| `nano_firmware.ino` | pins, pull-in power / time, PWM frequency, ToF library | |

`prototypeConfig()` is used by the board glue **and** by the laptop tests, so what is tested is what is flashed.

## 11. Tests and how to run them

`tests/test_nano_sprung_striker.py` (the compiled firmware core over the real protocol module and the real bridge, including
`test_a_tiles_whole_life_arms_then_raises_then_strikes_then_lowers_then_disarms`), `tests/test_striker_arming.py` (`CameraArming`,
`AutoArm`), `tests/test_session_bag.py` (the API routes). The firmware tests need a C++11 compiler; without one they are skipped:

```bash
pip install --target <empty dir> ziglang
NANO_HOST_CXX="env PYTHONPATH=<empty dir> python -m ziglang c++" python -m pytest tests
```

Flashing from the Pi (no internet there) is in `acoustic_node/nano_firmware/README.md`.

## 12. Not verified / to measure

1. **Coil temperature** and the right hold power (start at 30 %, find the lowest value that holds with margin).
2. **Camera-to-sensor transit time**; the 30 s forget time and `max_wait_s` should come from it.
3. The **camera actually raising it** on a real detection, and the automatic arm/disarm on the rig.
4. The **tile threshold margin** with other tiles (about 9-12 mm either side of 100 mm). Lowering the sensor would widen it.
5. The **Pi's power supply**: undervoltage warnings appear in its log and once coincided with the ToF dropping out. Use the official 5 A supply.
6. The **microphone gain** was at its maximum (496 of 496, +31 dB) and clipped; lowered to **150** on 2026-10-19 (idle level 0.24 -> 0.008 rms). The 2026-10-19 sessions recorded before that are clipped and not usable for acoustics. **Not yet checked: a real strike at the new gain** (it should peak around 0.1-0.8 and not clip; raise or lower with `amixer -c Device cset numid=3 <0-496>`, then `sudo alsactl store`).
7. The ToF library calls have met the real module only briefly; a sensor that hangs after a brown-out is recovered today only by a reset.
8. The AVR build's RAM headroom is ~660 bytes.
