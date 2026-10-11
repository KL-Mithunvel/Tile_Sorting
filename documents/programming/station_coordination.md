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
| Operator/master `hold_all` | every tile Hold |
| Broker down / camera silent / mic dead / UNO Q not acking | station state **FAULT**, red health chip, no guessing |
| UNO Q does not ack | final resent every `ack_timeout_s`; after `retries` the link is DOWN and later finals queue behind it, in order |

## 4. Topic and payload contract

All JSON, UTF-8. Topic names extend `Automation_Architecture.md` §12/§13.

| Topic | From → to | QoS / retain | Payload (main fields) |
|---|---|---|---|
| `tile/<id>/camera/result` | camera → station, master | 1 / no | `tile_id, run_id, seq, station:"camera", status:"done", grade, rule_grade, model_grade, model_used, crack_detected, crack_severity, corner_broken, snapshot_path, ts` |
| `station/camera/status` | camera → station | 0 / **retained**, last-will `{"state":"offline"}` | `state, source_kind, source_status, station_state, tile_count, errors, run_id, ts` (every `mqtt.heartbeat_s`) |
| `station/tap` | Nano serial bridge (later) / demo → station | 0 / no | `{}` — "the striker just fired"; starts the capture window |
| `tile/<id>/acoustic/result` | station → master | 1 / no | `tile_id, station:"acoustic", status:"done"\|"invalid", acoustic_grade, confidence, peak_frequency_hz, decay_t20_s, ts` |
| `tile/<id>/final` | station → **UNO Q**, master | 1 / no | `tile_id, seq, final_grade, destination, camera_grade, acoustic_grade, acoustic_counted, timed_out, reasons[], attempt, ts` |
| `tile/<id>/ack` | **UNO Q** → station | 1 / no | any JSON, e.g. `{"status":"ok"}` — the topic is the acknowledgement |
| `station/status` | station → master | 0 / **retained**, last-will `{"state":"offline"}` | `state, state_detail, health{}, awaiting_count, settings{}, tally{}, counters{}` |
| `station/cmd` | master → station | — | `{"cmd":"vote","enabled":bool}`, `{"cmd":"hold_all","enabled":bool}`, `{"cmd":"clear_alarms"}` |
| `machine/alarm` | station → master | 1 / no | `ts, level, kind, message, tile_id` |

Final grades: `Grade A`, `Grade B`, `Grade C`, `Reject`, `Hold`. `destination` comes
from `station.final.bins`.

### What the UNO Q side must do (not built)

- Subscribe to `tile/+/final` on the **Pi's** broker (`192.168.1.11:1883`).
- Publish `tile/<id>/ack` once it has *accepted* the tile into its queue.
- **De-duplicate on `tile_id`**: a retry resends the identical message with a higher
  `attempt`. Ack it again, act on it once.
- Treat finals as an ordered queue — they arrive in the order tiles were decided.
- Know that `Hold` means "put in the manual bin", not "skip".

## 5. Files

| Path | Role |
|---|---|
| `acoustic_node/python/fusion/` | pure logic: `grades`, `ledger`, `final_grade`, `store` (SQLite), `transport` (topics, in-memory + MQTT bus), `coordinator` |
| `acoustic_node/python/acoustic/features.py` | tap measurement + optional rule-based sound grade |
| `acoustic_node/python/acoustic/station.py` | microphone, threads, health; `dashboard.py` + `templates/` the GUI; `live_station.py` CLI; `demo.py` synthetic peers |
| `acoustic_node/python/run_station.py` | systemd entry point (the old `main.py` is the UNO Q App Lab stub, untouched) |
| `acoustic_node/deploy/` | `tile-acoustic.service`, `install_pi.sh` (also installs mosquitto) |
| `camera_node/python/camera/publisher.py` | camera side of the contract; `config.yaml` `mqtt:` (off by default) |
| `development/fake_station_peers.py` | fake camera / striker / UNO Q against a real broker |
| `tests/test_fusion_*.py`, `test_acoustic_features.py`, `test_acoustic_station.py`, `test_camera_publisher.py`, `test_station_contract.py` | the pure parts and the contract between the two nodes |
| `data/station/tiles.db` | the station's own record, one row per tile (gitignored) |

Config: `acoustic_node/python/acoustic/config.yaml` → `station:`; camera →
`camera_node/python/camera/config.yaml` → `mqtt:`.

## 6. Bring-up checklist (on the Pi, in this order)

1. `./acoustic_node/deploy/install_pi.sh` (apt packages, mosquitto, systemd unit).
2. `mosquitto_sub -h localhost -t '#' -v` in one terminal — keep it open; it shows everything.
3. Run `python development/fake_station_peers.py --host localhost` and
   `python -m acoustic.live_station` (controls on: `--controls`). Confirm: tiles appear,
   taps bind, finals publish, acks return, health strip all green. Then `--no-ack` and
   watch the no-ack alarm and FAULT.
4. Plug in the microphone **on a different USB controller/port from the camera**;
   `python -m acoustic.live_station --list-devices`; pin it **by name** in
   `acoustic/config.yaml` (`audio.device`, indices change between boots).
5. Mount the microphone away from the Pi's fan. Open the service view; read the idle
   level; set `trigger.rms_threshold` just above it (rms mode).
6. Set `mqtt.enabled: true` in the camera config; restart `tile-camera`. The camera chip
   in the run view should go green with a live source.
7. Tap a tile by hand with a real camera tile in view: the row should go
   camera → sound → final. Check the tap's waveform in the service view looks like a strike.
8. Only then consider `trigger.mode: hardware` (needs the Nano bridge publishing
   `station/tap`) and, much later, `final.vote_enabled`.
9. Kiosk: Chromium fullscreen on `http://localhost:5001/`.

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
placeholder); a missed *camera* detection shifts every later pairing by one until a
timeout — the FIFO cannot detect that by itself (the Nano's ToF presence flag in the tap
event would let it); a stopped belt with a tile under the striker is not modelled.
