# Station coordination — camera + sound → final grade → UNO Q

Status: **built and unit-tested on the dev laptop, never run on the Pi** (2026-10-11).
Nothing here has seen a real microphone with real tiles, a real broker, the Nano or
the UNO Q. See "What is and is not verified" at the end.

## 1. What it is

The Raspberry Pi 5 runs two services side by side (`Automation_Architecture.md` §5.3):

| Service | Unit | Port | Job |
|---|---|---|---|
| camera | `tile-camera` | 5000 | grade each tile from the picture; **mint its tile id**; publish it |
| acoustic + decision | `tile-acoustic` | 5001 | hear the tap; bind it to the right tile; compute the **final grade**; send it to the UNO Q; keep the record; show the operator GUI |

Plus **mosquitto on the same Pi** — the broker. The line therefore runs with no master
PC and no uplink. The master (optional) connects to the same broker to observe, collect
data and send a few commands; the station never waits for it.

```
 USB camera ─▶ tile-camera ──tile/<id>/camera/result──▶ ┐
                    │  station/camera/status (heartbeat) │ mosquitto (on the Pi)
 USB mic ──▶ tile-acoustic  ◀── station/tap (Nano bridge)│
      │ analyses the tap, binds it to the oldest waiting tile,
      │ decides, stores in SQLite                         │
      └─tile/<id>/acoustic/result, tile/<id>/final ─────▶ ┤──▶ UNO Q (pick & place)
                                      ◀── tile/<id>/ack ──┘      master PC: observe + station/cmd
```

## 2. Where the GUI lives

A **separate process and port** (`:5001`), not inside the camera dashboard (`:5000`):
the audio callback must never be starved by a busy camera thread, and restarting either
service must not restart the other. It is the *operator* screen:

- **Run view `/`** — read-only, kiosk-ready: last tile's final grade and bin (large),
  per-grade counters, the tile's picture (read from the file the camera saved), a health
  strip (camera / mic / broker / UNO Q), the tiles waiting for a tap, alarms with an
  Acknowledge button, recent tiles. If it stops hearing from the station it turns red
  with "NO DATA" rather than show old data as live.
- **Service view `/service`** — microphone choice and live level, last tap waveform and
  spectrum and measurements, the sound-vote and hold-all switches, simulate tap.
  Changing anything needs `station.dashboard.controls_enabled: true` (OFF by default).

## 3. How one tile is decided

1. The camera grades the tile at the trigger line and publishes
   `tile/<id>/camera/result`. The id is `T<YYYYMMDD>-<HHMMSS>-<n>` — from the publisher's
   own counter, not the camera's `seq` (which restarts on every source switch).
   Only a **live camera** is forwarded; a replayed recording never is.
2. The station's **ledger** queues it `awaiting_tap`.
3. A strike is heard. The microphone clip is measured (`acoustic/features.py`: peak
   frequency, centroid, loudness, SNR, decay to −20 dB) and bound to the **oldest
   waiting tile**. Order, not timing, decides: belt speed is open-loop so transit time is
   not a number to trust.
4. `fuse_final()`: final = **worse of** camera grade and sound grade, but the sound only
   counts if `final.vote_enabled`, the tap was a valid strike, a grade exists, and
   confidence ≥ `final.min_confidence`. Otherwise it is recorded and shown as "not
   counted". Default: **not counted** (nothing is validated on real tiles).
5. The final goes to the UNO Q as `tile/<id>/final` and is stored.

### Fail-closed rules (each one tested)

| Situation | Result |
|---|---|
| No tap within `ledger.max_wait_s` | tile is **Hold** (manual bin); a camera Reject stays Reject; alarm |
| A tap with no tile waiting | **orphan**: counted, alarmed, attached to nothing |
| Camera message unreadable | tile keeps its place in the order, grade None → **Hold** |
| Same camera message delivered twice (QoS 1) | ignored — one tap slot only |
| rms trigger mode, clip is not a plausible strike | ignored, **does not use up a tile's slot** |
| Hardware trigger, clip unusable | still uses the slot; tile goes on camera grade alone |
| Strike fired with **no tile under it** (`tile_present=false`) | uses up no slot; alarm `empty_strike` |
| Tile under the striker but **no camera result waiting** | missed camera detection: a `UNKNOWN-…` final **Hold** is sent so the UNO Q's ordered queue stays in step; alarm `missed_camera` |
| Gap in the Nano's strike `seq` | alarm `tap_gap` with the count of lost events |
| Operator/master `hold_all` | every tile Hold |
| Broker down / camera silent / mic dead / UNO Q not acking | station state **FAULT**, red health chip, no guessing |
| UNO Q does not ack | final resent every `ack_timeout_s`; after `retries` the link is DOWN and later finals queue behind it, in order |

## 4. Topic and payload contract

All JSON, UTF-8. Topic names extend `Automation_Architecture.md` §12/§13.

| Topic | From → to | QoS / retain | Payload (main fields) |
|---|---|---|---|
| `tile/<id>/camera/result` | camera → station, master | 1 / no | `tile_id, run_id, seq, station:"camera", status:"done", grade, rule_grade, model_grade, model_used, crack_detected, crack_severity, corner_broken, snapshot_path, ts` |
| `station/camera/status` | camera → station | 0 / **retained**, last-will `{"state":"offline"}` | `state, source_kind, source_status, station_state, tile_count, errors, run_id, ts` (every `mqtt.heartbeat_s`) |
| `station/tap` | Nano serial bridge (`acoustic/tap_bridge.py`) / demo → station | 1 / no | `seq, tile_present, tof_mm, nano_ms, ts` — "the striker just fired"; starts the capture window; `tile_present=false` = fired on an empty position |
| `station/nano/status` | bridge → station, master | 0 / **retained**, last-will | `state, link, fw, belt_pct, tof1_ok, tof3_ok, taps, lost_taps, bad_lines, ts` |
| `station/belt` | master / operator → bridge | — | `{"percent": 0-100}` → `BELT n` to the Nano |
| `station/strike` | master / operator → bridge | — | `{"enabled": bool}` → `STRIKE 0\|1` to the Nano |
| `tile/<id>/acoustic/result` | station → master | 1 / no | `tile_id, station:"acoustic", status:"done"\|"invalid", acoustic_grade, confidence, peak_frequency_hz, decay_t20_s, ts` |
| `tile/<id>/final` | station → **UNO Q**, master | 1 / no | `tile_id, seq, final_grade, destination, camera_grade, acoustic_grade, acoustic_counted, timed_out, reasons[], attempt, ts` |
| `tile/<id>/ack` | **UNO Q** → station | 1 / no | any JSON, e.g. `{"status":"ok"}` — the topic is the acknowledgement |
| `station/status` | station → master | 0 / **retained**, last-will `{"state":"offline"}` | `state, state_detail, health{}, awaiting_count, settings{}, tally{}, counters{}` |
| `station/cmd` | master → station | — | `{"cmd":"vote","enabled":bool}`, `{"cmd":"hold_all","enabled":bool}`, `{"cmd":"clear_alarms"}` |
| `machine/alarm` | station → master | 1 / no | `ts, level, kind, message, tile_id` |
| `station/diag/ping` | `diagnose.sh` / `system.sh` → all | 0 / no | `{"id": ...}` — **never a tile**: answering it cannot create a pick job |
| `station/diag/pong/<node>` | each running node → asker | 0 / no | `{id, node, ts, ok, detail}`; nodes: `station`, `camera`, `gantry` (UNO Q feed), `nano` (serial bridge — `ok` only if the Nano itself spoke recently), `master` |

Final grades: `Grade A`, `Grade B`, `Grade C`, `Reject`, `Hold`. `destination` comes
from `station.final.bins`.

### What the UNO Q side must do

**Built 2026-10-11 as `pick_place_node/python/pick_place/tile_feed.py`** (`FinalQueue`, `FeedClient`,
bench CLI `python -m pick_place.tile_feed --host 192.168.1.11 [--auto-pick 2]`, and
`/api/feed` on the twin dashboard via `twin_prototype --station-host`). Tested on the laptop
against the real coordinator; **never run on the UNO Q**, and the gantry sequencer that will
consume `FinalQueue.start_next()` / `finish()` does not exist. It must:

- Subscribe to `tile/+/final` on the **Pi's** broker (`192.168.1.11:1883`).
- Publish `tile/<id>/ack` once it has *accepted* the tile into its queue.
- **De-duplicate on `tile_id`**: a retry resends the identical message with a higher
  `attempt`. Ack it again, act on it once.
- Treat finals as an ordered queue — they arrive in the order tiles were decided.
- Know that `Hold` means "put in the manual bin", not "skip".
- Answer a message it cannot act on with `{"status":"rejected","reason":...}` (the station alarms
  `unoq_rejected` and releases its queue); when its queue is full, **do not ack** (back-pressure — the
  station retries and eventually shows the link as down).

## 5. Files

| Path | Role |
|---|---|
| `acoustic_node/python/fusion/` | pure logic: `grades`, `ledger`, `final_grade`, `store` (SQLite), `transport` (topics, in-memory + MQTT bus), `coordinator` |
| `acoustic_node/python/acoustic/features.py` | tap measurement + optional rule-based sound grade |
| `acoustic_node/python/acoustic/station.py` | microphone, threads, health; `dashboard.py` + `templates/` the GUI; `live_station.py` CLI; `demo.py` synthetic peers |
| `acoustic_node/python/run_station.py` | systemd entry point (the old `main.py` is the UNO Q App Lab stub, untouched) |
| `acoustic_node/deploy/` | `tile-acoustic.service`, `install_pi.sh` (also installs mosquitto) |
| `camera_node/python/camera/publisher.py` | camera side of the contract; `config.yaml` `mqtt:` (off by default) |
| `acoustic_node/python/acoustic/tap_protocol.py`, `tap_bridge.py` | Nano serial link (spec: `nano_serial_protocol.md`) |
| `acoustic_node/python/fusion/stats.py` | camera->tap transit-time summary (suggested `max_wait_s`) |
| `acoustic_node/deploy/kiosk/` | Chromium kiosk autostart for the Pi's screen |
| `pick_place_node/python/pick_place/tile_feed.py` | the UNO Q's ordered, de-duplicating, acknowledging queue of finals |
| `master_node/` | optional PC observer / collector / commander (:8000) |
| `development/fake_station_peers.py` | fake camera / striker / UNO Q against a real broker |
| `tests/test_fusion_*.py`, `test_acoustic_features.py`, `test_acoustic_station.py`, `test_camera_publisher.py`, `test_station_contract.py` | the pure parts and the contract between the two nodes |
| `data/station/tiles.db` | the station's own record, one row per tile (gitignored) |

Config: `acoustic_node/python/acoustic/config.yaml` → `station:`; camera →
`camera_node/python/camera/config.yaml` → `mqtt:`.

## 6. Bring-up (scripts; Windows launchers in `tools/pi/`)

Everything below runs on the Pi from `acoustic_node/deploy/`; from the laptop each has a
`tools/pi/station_*.bat` that pushes the code and runs it over SSH. **None of it has run on
the Pi.** The laptop must be on the D-Link LAN (Ethernet in) first.

| Step | On the Pi | From the laptop | What it tells you |
|---|---|---|---|
| 0 | | `station_push.bat` | copies camera_node, acoustic_node, fake peers |
| 1 | `install_pi.sh` | `station_install.bat` | apt packages, **mosquitto**, `tile-acoustic` unit, `--mqtt` drop-in on `tile-camera` (install the camera first) |
| 2 | `selftest.sh [sec]` | `station_test.bat` | **software chain** against the real broker with synthetic mic + fake camera/striker/UNO Q: finals computed, acked, recorded, transit measured. PASS/FAIL per check |
| 3 | `status.sh` | `station_status.bat` | services, ports, USB topology (**mic and camera on different controllers**), audio inputs, serial ports, thermal |
| 4 | `system.sh start --no-unoq` | `station_start.bat --no-unoq` | **in-sync start**: broker → station (HELD) → camera → every required node must answer → release hold. Any silent node = the line stays held |
| 5 | `diagnose.sh` | `station_diag.bat` | network (ping) → ports → *programs answering* (diagnostic ping) → each station's own view. Exit 1 + a numbered fix list on any problem |
| 6 | `system.sh start --belt 40` | `station_start.bat --belt 40` | full system once the UNO Q feed runs (`--with-nano` once the Nano is wired) |
| – | `system.sh stop [--services]` | `station_stop.bat` | belt 0 %, striker inhibited, line held |

### Manual details the scripts cannot do for you

1. Plug the microphone into a **different USB controller/port from the camera**
   (`status.sh` prints `lsusb -t`); pin it **by name** in `acoustic/config.yaml`
   (`audio.device`, indices change between boots; `python -m acoustic.live_station --list-devices`).
2. Mount the microphone away from the Pi's fan. Read the idle level on the service view and
   set `trigger.rms_threshold` just above it (rms mode).
3. A real hand tap on a real tile with the camera watching: the row should go
   camera → sound → final; the waveform on the service view should look like a strike.
4. Only then `trigger.mode: hardware` (needs the Nano bridge) and, much later,
   `final.vote_enabled`.
5. Kiosk: `acoustic_node/deploy/kiosk/install_kiosk.sh`, then log out and in.
6. UNO Q: `python -m pick_place.tile_feed --host 192.168.1.11 --auto-pick 2`; `diagnose.sh`
   should then show **gantry** answering.
7. Master PC (optional): `python -m master.live_master --host 192.168.1.11`.

### What "in sync" means

`system.sh start` never lets a half-started line sort tiles. The station comes up **holding**
every tile (they go to the manual bin) and the belt is not started; only when the station,
the camera, and (unless `--no-unoq`) the UNO Q feed have each *answered a diagnostic ping and
reported ok* is the hold released and the belt set, in one step. This is start-up
sequencing, **not clock synchronisation** — cross-node time (no NTP on the isolated LAN) is
still an open TODO.

### Diagnostic layers (`diagnose.sh`)

1. **network** — ping router, Pi, UNO Q, master.
2. **ports** — 1883 broker, 5000 camera GUI, 5001 station GUI (UNO Q ssh and master are optional).
3. **answers** — a diagnostic ping through the broker; a node is up only if the *running
   program* replied. `unwell` = it answered but reports a problem (camera on a file
   replay, Nano link silent, station in FAULT).
4. **stations** — the station's state, tally, alarms, measured transit; the camera's source.

Run from a laptop in Git Bash with `--pi 192.168.1.11` (needs `python` + `paho-mqtt`; set
`TILE_PY` to a specific interpreter if `python3` is a Windows Store stub).

## 7. What is and is not verified

Verified (laptop, 2026-10-11): all pure logic and the coordinator over an in-memory bus;
the Flask API incl. controls, JSON-only and path checks; the GUI rendered in headless
Edge against the synthetic demo (`python -m acoustic.live_station --demo`); the MQTT
client's failure path (unreachable broker: not connected, publish returns False).

**Not verified:** any connection to a real broker (none was available on the laptop); the
Pi, `install_pi.sh`, the systemd unit; a real USB microphone through PortAudio on the
Pi; the Nano and its serial bridge (the tap-event format is still undesigned); the UNO Q
consuming finals; the GUI on the Pi's own browser. The sound features are unvalidated
against real tiles and `station.reference` is `null`, so the sound has no grade.

Open design points: belt transit time is unmeasured (`ledger.max_wait_s` is a generous
placeholder); the missed-camera-detection check needs the Nano's `tile_present` flag, so
with the rms trigger a missed camera detection still shifts every later pairing by one
until a timeout; a stopped belt with a tile under the striker is not modelled. The Nano
side is specified in `nano_serial_protocol.md` and has no firmware yet.
