# Nano ↔ Pi serial protocol

Status: **designed and implemented on the Pi side** (`acoustic/tap_protocol.py`,
`acoustic/tap_bridge.py`, 2026-10-11, unit-tested with a scripted fake serial port).
**The Nano firmware exists as of 2026-10-12** (`acoustic_node/nano_firmware/`: logic in
`nano_core.h`, tested on a laptop against `tap_protocol.py` and the real bridge; the board
glue has **never been compiled for AVR or run on a Nano**) — this is the specification it
meets. Where the firmware had to decide something the spec left open (manual resume after a
watchdog trip, striker keeps running while ToF 3 holds the belt) see its README.
Context: `Automation_Architecture.md` §5.3/§5.5 (the Nano runs the striker and belt PWM;
it is not on the LAN), `station_coordination.md` (what the Pi does with the events).

## Link

USB serial, 115200 baud, 8N1, ASCII, one message per line, newline-terminated.
On the Pi pin the port by id (`/dev/serial/by-id/usb-…`), not `ttyUSB0`:
`station.nano.port` in `acoustic/config.yaml`.

Every line ends with an **XOR checksum**:

```
<payload>*<HH>\n        HH = two upper-case hex digits, XOR of every byte of <payload>
BELT 55*3F
```

A line with a missing, malformed or wrong checksum is **discarded** (and counted). This
exists so a byte flipped by motor noise on a long USB run can never turn `BELT 0` into
`BELT 9`. Both ends must do the same; the Nano needs a 6-line function for it.

## Nano → Pi

| Message | When | Meaning |
|---|---|---|
| `HELLO <fw>` | once at boot | firmware id; resets the Pi's `seq` tracking |
| `HB <uptime_ms> <belt_pct> <tof1_ok> <tof3_ok> [<manual> <retracted> <tof1_mm> <armed> <state> <left_ms> <must_leave> <hold_pct> <up_wanted>]` | every 500 ms | alive; current belt %, ToF sensors responding (0/1). The prototype striker adds `manual` (0 ToF / 1 manual mode), `retracted` (coil energised) and `tof1_mm` (the striker ToF's latest distance, 65535 = no reading / nothing in range; then `armed` = STRIKE 1 read back, `state` = `nano::State` (7 RETRACTED, 8 STRUCK, 5 COOLDOWN, 6 FAULT), `left_ms` = ms until the strike while a tile is being timed, `must_leave` = the tile under it was already struck / was there at power-up, `hold_pct` = the coil hold duty, `up_wanted` = an `UP` is pending; the prototype sends HB every 250 ms; published as `tof1_mm` in `station/nano/status` and shown on the Service view) |
| `TAP <seq> <tile_present> <tof_mm> <uptime_ms> [<src>]` | the instant the LOCK solenoid opens (prototype striker: the instant the coil is **released**) | one strike. `seq`: 16-bit counter, wraps. `tile_present`: 0/1, the striker-station ToF reading *at the strike*. `src` (prototype striker only, 5th field): `0` ToF-started, `1` manual, `2` safety release (held too long, `STRIKE 0`, watchdog or e-stop while retracted), `3` ToF-started but the ToF could not be read at the strike, `4` rest (lowered back to its resting position with nothing to strike, so the coil is not left on) |
| `RET <seq> <src> <uptime_ms>` | **prototype striker:** the instant the coil is energised | the striker was just retracted. This is a sound of its own (plunger stop, spring), reported so it can be labelled and removed from the analysed clip. `src` as for `TAP` below |
| `PONG <n>` | after `PING <n>` | optional echo |
| `ERR <code> <text…>` | on a fault | e.g. `ERR TOF1 timeout`; becomes an alarm. Codes sent: `WATCHDOG` (belt stopped, Pi silent), `WDOGRESET` (first valid line after a trip: belt and striker need a fresh command), `TOF1` / `TOF3` (sensor not answering), `ESTOP`, `BADLINE` (rate limited) |

## Pi → Nano

| Message | Meaning |
|---|---|
| `PING <n>` | keep-alive, every 500 ms (`station.nano.ping_s`) |
| `BELT <pct>` | belt speed 0–100 (clamped by the Pi) |
| `STRIKE <0\|1>` | allow / inhibit the striker (e.g. the master pausing tapping) |
| `MODE <0\|1>` | **Implemented 2026-10-19 (prototype striker).** 0 = the ToF starts cycles, 1 = manual: the ToF is ignored and the Pi starts them. Switching to 1 cancels an auto cycle that has not yet energised the coil |
| `RETRACT` | manual mode: energise the coil and **hold** it (reports `RET`) until `RELEASE` |
| `UP` | prototype striker (request-only): a tile is coming (the camera saw it) -> raise the striker now (needs STRIKE 1). Forgotten after `up_timeout_ms` (30 s) if no tile reaches the ToF; `ERR UNSUPPORTED` on other cores |
| `HOLD <0-100>` | prototype striker: coil duty % while the striker is held up (heat vs holding force; too low drops it). Applies at once; reported as the last `HB` field `hold_pct` |
| `RELEASE` | manual mode: de-energise it; the strike (reports `TAP … 1`) |
| `TAP` | manual mode: `RETRACT`, wait `retract_hold_ms`, `RELEASE` in one command (hand-placed tiles, repeated runs). Answered like any tap |

All three manual commands obey the same inhibits as an automatic strike (`STRIKE 1` needed, e-stop, watchdog trip, cooldown) and are refused with `ERR NOTMANUAL`, `INHIBITED`, `BUSY`, `NOTRETRACTED` or `FAULT` -- never silently. On the full-system (dual solenoid) core they answer `ERR UNSUPPORTED`.

## Requirements on the Nano firmware (not optional)

1. **Watchdog: stop the belt if no valid line has arrived from the Pi for 2000 ms.**
   The Pi bridge's `PING` is the keep-alive; if the bridge, the Pi, the cable or the USB
   port dies, the belt must stop *by itself*. The bridge deliberately does **not** send
   `BELT 0` on shutdown — silence is the signal, because it is the only path that still
   works after a crash. Belt safety must not depend on the network or on MQTT
   (`Automation_Architecture.md` §5.3 risk 5).
2. Boot with the belt **stopped** and the striker **inhibited** until the first valid
   `BELT`/`STRIKE` line.
3. Send `TAP` exactly once per strike, with the next `seq`. Never reuse a `seq` for a
   different strike; repeating the *same* line is allowed (the Pi ignores duplicates).
4. `tile_present` must come from a ToF reading taken at the strike, not a stale one. If
   the ToF is not responding, do **not** send `TAP … 0 …` (that claims the position is
   empty) — send `ERR TOF1 timeout` and do not strike, so the Pi never records a strike
   it cannot place. *Exception, prototype striker:* once the coil is energised the
   strike cannot be withheld (it must come down), so a blind release is sent as
   `TAP … src=3` plus `ERR TOF1`, and the Pi treats `tile_present` as unknown.
5. Never block the main loop on serial output: step/PWM and the solenoid timing come
   first (the AVR has 2 KB RAM and two external interrupts).

## What the Pi does with it

- `RET` → publishes `station/retract` `{seq, source, nano_ms, ts}`. `TAP` gains `source`
  (`tof` | `manual` | `safety` | `tof_blind`; a 4-field `TAP` is `tof`). **Only a `tof` strike
  is paired with a tile.** `manual` and `safety` strikes are counted and recorded (data
  collection sessions, `data_collection_sessions.md`) but use up no tile's tap slot; `tof_blind`
  has `tile_present: null`.
- The Pi side sends these through `station/striker/cmd`
  `{"action": "mode"|"retract"|"release"|"cycle", "manual": bool}` (the Service view's Striker panel).

- `TAP` → publishes `station/tap` `{seq, tile_present, tof_mm, nano_ms, ts}` and the
  audio capture window starts (its pre-trigger buffer absorbs the few ms of serial
  latency).
- `tile_present = 0` → the strike **cannot be any tile's** and uses up no tap slot
  (alarm `empty_strike`).
- `tile_present = 1` with **no camera result waiting** → a tile the camera missed is
  physically on its way to the UNO Q; the station sends it a `Hold` final
  (`UNKNOWN-…`) so the UNO Q's ordered queue does not fall out of step (alarm
  `missed_camera`).
- A gap in `seq` → alarm `tap_gap` with the number of strike events lost.
- Heartbeats → `station/nano/status` (retained): `link`, `belt_pct`, ToF flags and
  counters; the run view shows the **Striker (Nano)** chip. With `station.nano.required:
  true` a silent Nano is a FAULT.
- `station/belt` `{"percent": n}` and `station/strike` `{"enabled": bool}` (from the
  master or an operator tool) become `BELT` / `STRIKE` lines.

## Not decided

Line length limit (the AVR serial buffer is 64 bytes — all messages above are well
under); whether `HB` should also carry the belt-end ToF 3 distance; belt speed feedback
(open loop today).
