# Data-collection sessions ("the bag")

Added 2026-10-19. One **session** = one data-collection run recorded into one folder on the Pi:
the continuous microphone, the camera's raw video, every message the station sent (with a
timestamp), and every striker **retract** and **strike**, each labelled by the operator. The aim
is a dataset that can be turned into training data later without re-collecting.

Status: **built and tested on the laptop only** (`tests/test_session_bag.py`,
`tests/test_session_pull.py`, `tests/test_nano_sprung_striker.py`; the demo run end to end through
the real GUI API). **Never run on the Pi, with a real microphone, the real camera, the Nano or a
tile.** Nothing about timing between the streams has been measured on hardware (see the end).

## What is recorded

```
data/sessions/2026-10-19_16-05-12/
  manifest.json     labels, run type, note, code commit, config, every file's size + SHA-256
  events.jsonl      every bus message {t, topic, payload}: camera results, taps, retracts, Nano status,
                    grades, alarms, session start/stop
  strikes.json      the labelled striker events (below)
  audio/            the microphone, continuous, 16-bit mono WAV in 30 s chunks + chunks.jsonl
                    (wall-clock start and sample count of each chunk)
  video/            the camera's raw (un-annotated) feed: video.mp4, frames.csv (wall time of every
                    frame), meta.json   (+ video_2/ if the camera service restarted mid-session)
  camera_config.yaml   the exact vision thresholds the camera ran with (replay the video with these)
  clips/            DERIVED: one short WAV per event (S0001_strike.wav, R0001_retract.wav, ...) + clips.csv
  tiles/            DERIVED: the camera's picture of each tile it graded (named by tile id)
```

The raw streams (audio, video, events) are never edited. Clips and labels are derived from them, so
a corrected label or a different clip window is a re-run (`python -m fusion.session clips <folder>
--pre 0.2 --post 2.0`), not a re-collection.

### The striker events

The prototype striker is one sprung solenoid above the tile: **energised = retracted, released =
gravity + spring strike**. Both edges make noise, so each cycle gives two events:

| event | when | `source` |
|---|---|---|
| `R0001` **retract** | the Nano energised the coil (striker goes up) | `tof` or `manual` |
| `S0001` **strike** | the Nano released the coil (the tile is hit) | `tof`, `manual`, `safety` (held too long / inhibited / watchdog / e-stop while up), `tof_blind`, `rest` (the quiet lowering to rest with nothing to strike: labelled data, but not a tile strike) |

A retract and the strike that ends it share a `cycle` and carry `retract_to_strike_ms` (from the
Nano's own clock, so it is exact). The retract clip is **labelled data, not a thing to throw away**:
it is the retraction-noise example a model or a filter needs to learn to ignore.

Each event carries the labels in force when it happened (changeable later, per event):

| label | values |
|---|---|
| `condition` | `tile` (a tile under the striker), `no_tile` (striker alone), `ambient` |
| `defect` | `fine`, `chipped`, `cracked`, `both`, `unsure`, or empty |
| `tier` | `3A`, `3B`, `4`, `5`, or empty |
| `tile_ref` | free text: the number you wrote on the tile |
| `note` | free text |

`clips/clips.csv` has one row per clip with those labels plus `kind`, `source`, `cycle`, `tile_id`
(only for a ToF-started strike the station paired with a camera tile), `event_offset_s` (where the
event sits inside the clip), `t_wall`, `sample_rate`.

**Label by looking at / handling the tile yourself, not from the camera's grade.** The camera's
crack and corner checks are unvalidated; training the sound on them would copy their mistakes.

## How to run one

1. **Services** (on the Pi): mosquitto, `tile-acoustic` and `tile-camera` with `mqtt.enabled: true` in
   `camera_node/python/camera/config.yaml` (otherwise the camera cannot hear the session and no video is
   recorded; the Session panel says "no answer"). The Nano plugged in with `station.nano.enabled: true` in
   `acoustic/config.yaml`. Start the station with `--collect` (hardware trigger, no UNO Q needed, controls on):
   `python -m acoustic.live_station --collect`.
2. Open `http://<pi>:5001/service`. In **Striker**: set the mode (**AUTO** = the camera and the ToF drive every cycle;
   **MANUAL** = you do). In AUTO the station **arms and disarms itself**: armed only while a tile is in the system (graded
   by the camera, or under the ToF); otherwise the striker rests down with the coil off (`striker_sequence.md`). For MANUAL,
   switch **Auto-arm** off and press **Armed** (`STRIKE 1`; without it the Nano refuses every manual command). The Nano link
   row must be green.
3. **Data session** panel: pick the run type, add a note, set the labels for what is under the striker, **Start session**.
   `● REC` shows the elapsed time; the table shows audio seconds, strike/retract counts, dropped audio blocks and
   whether the camera's video is `recording`.
4. In MANUAL: **Retract** (the striker goes up, you hear it), then **Release (strike)** when ready; or
   **Retract + release** for one automatic cycle. Change the label dropdowns between tiles (they apply to the
   *next* event); a wrong one is fixed afterwards with **use current label** on the event row.
5. **Stop & save** cuts the clips, waits for the camera to finish its file and seals the manifest (a few seconds).

Suggested order, each as its own short session so the notes stay meaningful:
1. **Room noise** (`ambient`): 60 s with nothing moving, then **Mark ambient** a few times. Fan, mic, mains hum.
2. **Striker alone** (`no_tile`): 20-30 cycles on the bare surface. The striker's own sound and the retract noise.
3. **Tiles**: one tile at a time, `tile_ref` written on it, the label from your own inspection. Several strikes
   per tile (re-seat it between some) so tile-to-tile and strike-to-strike variation can be told apart.

Why one tile at a time first: the station pairs a ToF strike with the *oldest waiting* camera tile (FIFO). A tile the
camera missed shifts every later pairing by one until a timeout. In MANUAL mode nothing is paired at all (a manual or
safety strike never uses up a tile's tap slot), so the labels you set are the only link; that is deliberate.

If the session stops uncleanly (power cut, crash) the next start-up seals the folder as `interrupted` (at most one
30 s audio chunk and the unwritten tail of the video are lost) and the Service view says so.

## Getting it off the Pi

- **Master PC, verified:** `python -m master.session_pull --pi-url http://192.168.1.11:5001 --dest data/master/sessions`.
  Lists the finished sessions through `/api/sync/sessions`, checks every file against the manifest's SHA-256, resumes a
  cut-off download (HTTP Range), discards a file that arrives wrong, skips a session still recording, and writes the
  session's `manifest.json` last so a half-copied session is recognisable (no manifest). Set `--token` if
  `station.sync.token` is set.
- **By hand:** the folder is plain files; `rsync -a pi:~/Tile_Sorting/data/sessions/<id>/ .`, then
  `python -m fusion.session verify <folder>` re-checks every hash.

## Limits and what is NOT known

- **Never run on hardware.** Not the Pi, the microphone through PortAudio, the CSI/USB camera writing video next to the audio, the
  Nano, or a tile. The synthetic microphone and fake clocks prove the bookkeeping, not the physics.
- **Time alignment is the wall clock**, good to tens of ms (audio device start-up latency, serial latency of the Nano's `TAP`
  line, camera frame timing), **not** to the sample. Clips use a window around the event, not an onset detector. If you need
  better, detect the onset inside the clip.
- **Disk:** audio is about 320 MB per hour; 1080p mp4v video is far larger (measure it). A session refuses to start below
  `station.session.min_free_gb` and the Service view shows LOW DISK below `stop_free_gb`; it does not stop by itself.
- **The camera's video** is raw frames as the camera delivered them; it is written by a *separate process* and the two only
  agree through the bus. If the camera service is down the audio and events are still recorded and the manifest says
  `"video": null`.
- **Trigger mode and clip pairing (2026-10-19).** `trigger.mode` now defaults to `hardware` (a Nano strike event starts every live-analysis capture); each clip is paired with its own strike event by a token, not by arrival order, and a tap the detector had to refuse is counted instead of mislabelling the next clip. This affects the live grading clips only; the session's continuous audio and its cut clips never depended on it. See `striker_sequence.md`.
- **Microphone gain.** The tap mic (`USB PnP Audio Device`, ALSA card `Device`) was found at maximum gain, clipping, so the first sessions' audio is unusable. It is now **150/496** on the Pi (saved with `alsactl store`; idle rms about 0.008). Re-check with a real strike: peak should sit around 0.1-0.8, never at 1.0 (`amixer -c Device cset numid=3 <value>`, then `sudo alsactl store`). Do not change the gain in the middle of a set of recordings, and note it in the session note if you do.
- **The gain does not survive a reboot (found 2026-10-19).** After a power cycle the USB mic came back at 496/496; `alsactl store` is not restored for this USB card. A systemd drop-in on the Pi, `tile-acoustic.service.d/gain.conf` (reference copy: `acoustic_node/deploy/tile-acoustic-gain.conf`), now sets 150 before every start. If the card name or `numid` changes, the line fails silently (`ExecStartPre=-`): check `amixer -c Device cget numid=3`.
- **The camera index moves after a reboot (found 2026-10-19).** The Logitech C930e was `device_index: 1` before the power cycle and `0` after (the Pi's other video nodes changed), so `tile-camera` reported "could not open device index 1". The Pi's camera config is now `0`. If the camera is not found, `ls /dev/video*`, `v4l2-ctl --list-devices`, or pick the camera on the camera dashboard (:5000).
- **Auto-arm and the camera trigger** worked on the rig in the 2026-10-19 tile run (below), but only 22 tiles were seen; the transit time and coil temperature are still unmeasured.
  Strikes caused by a tile in AUTO are tile data; the `rest` lowerings and any `safety` strikes are not, and should be dropped
  when building a training set.
- **Prototype striker numbers are placeholders:** `retract_hold_ms` (1500), `retract_max_ms` (8000), `strike_ms` (300). How long the
  retraction rings, the coil's duty rating and the real strike time are unmeasured. Hand-turned belt: tile arrival timing from
  these runs says nothing about belt timing.
- The **Armed** button only sends commands; the GUI does not read back whether the Nano is armed. The Nano refuses an unarmed
  manual command with `ERR INHIBITED`, which shows as a `nano_error` alarm.
- The sound analysis (`valid`, SNR, peak frequency) still runs on each strike's live clip but its thresholds are unvalidated
  and `station.reference` is null, so no sound grade exists. This feature records data; it grades nothing.

## Datasets recorded (2026-10-19, first real runs on the Pi)

All on the Pi with the USB PnP mic at gain 150, 48 kHz mono; pulled to the laptop with `master.session_pull` into
`data/master/sessions/` (hashes verified; `data/` is not in git).

| Session | What | Result |
|---|---|---|
| `2026-10-19_18-20-14` | Room noise, 90 s, striker resting down, coil off, `ambient` | no dropped blocks; one strike event was counted inside it and not investigated |
| `2026-10-19_18-26-52` | Striker alone, coil powered, 25 manual retract + release cycles, 6 s apart, `no_tile` | 25 retract + 25 strike clips; strike peaks 0.28-0.96; **every retract peaks at 1.000 (clipped)** |
| `2026-10-19_18-26-22` | One powered cycle (the check before the set) | valid |
| `2026-10-19_18-44-39` | Tiles through the whole workflow (AUTO, auto-arm, camera raises the striker, ToF strike), 298 s | 22 tile strikes each paired with its camera tile id, 22 retracts, 6 `rest` lowerings (not tile data), 7 retracts with no strike of their own; strike peaks median 0.70, 3 of 22 clipped, 28 of 29 retracts clipped; video + audio complete, no dropped blocks |

Earlier striker sessions made while the coil was unpowered recorded silence and were deleted (Pi and laptop).

**Tile notes not added yet.** The session was started with only `condition: tile`. `data/master/tile_notes_2026-10-19_18-44-39.csv`
lists the 22 tile strikes in time order (strike event, tile id, time) with empty `tile_ref`, `defect`, `tier`, `note` columns to be
filled from the operator's own inspection, then written onto the events (relabel) and `clips.csv` re-cut. The order in the CSV is the
camera's order, which differs from the belt order if the camera missed a tile.
