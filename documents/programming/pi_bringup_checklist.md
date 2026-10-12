# Pi station bring-up checklist

Tick-box companion to `station_coordination.md` §6 (scripts) and §7 (open items).
Nothing here has run on the Pi yet — every box starts empty. Record the date and any
surprise next to each step so the next session knows what was actually observed.

Scripts: `acoustic_node/deploy/*.sh` on the Pi; Windows launchers in `tools/pi/`.
The laptop must be on the D-Link LAN (Ethernet in) for the launchers.

---

## 0. Before touching the station software

- [ ] Pi 5 imaged (Raspberry Pi OS Desktop 64-bit), reachable by SSH at its reserved LAN address
- [ ] Network plan items done: never-default route, Wi-Fi as intended, router SSH off (`documents/electrical/network_plan.md`)
- [ ] Active cooler fitted; `vcgencmd get_throttled` reads `0x0` under load
- [ ] Camera station installed first (`camera_node/deploy/install_pi.sh`, service `tile-camera`) — the station installer adds an `--mqtt` drop-in to it
- [ ] Camera opens: dashboard at `:5000` shows a live picture (USB: `--list-devices`; CSI: `picamera2` backend)

## 1. Install and software self-test (no hardware needed)

| # | Step | Launcher | Pass looks like | Done / notes |
|---|---|---|---|---|
| 1 | Push code | `station_push.bat` | camera_node, acoustic_node, fake peers copied | [ ] |
| 2 | Install | `station_install.bat` | apt packages, mosquitto, `tile-acoustic` unit, no errors | [ ] |
| 3 | Self-test | `station_test.bat` | every check PASS: finals computed, acked, recorded, transit measured | [ ] |
| 4 | Status | `station_status.bat` | services up, ports open, USB topology printed, thermals sane | [ ] |

If step 3 fails, stop here — it is the software chain against the real broker with a
synthetic mic, so a failure is a code/config problem, not a hardware one.

## 2. Microphone

- [ ] Mic plugged into a **different USB controller/port from the camera** (`lsusb -t` in `status.sh` output)
- [ ] Mic mounted **away from the Pi's fan**
- [ ] Any AGC / noise suppression / enhancement on the mic or its driver is off
- [ ] `python -m acoustic.live_station --list-devices` shows the mic; pinned **by name** in `acoustic/config.yaml` `audio.device` (indices change between boots)
- [ ] Idle level read on the Service view (`/service`) with the line running and no tap
- [ ] `trigger.rms_threshold` set just above the idle level (placeholder 0.02 is not a real value)
- [ ] Reboot the Pi; confirm the mic is still found by name

## 3. First real tap

- [ ] Service view mic bar moves when you tap a tile by hand
- [ ] Waveform on the Service view looks like a strike (sharp onset, decaying ring)
- [ ] Spectrum shows a clear peak; SNR is comfortably above the noise
- [ ] With the camera watching, a tile row goes **camera → sound → final** on the Run view
- [ ] Tap with no tile under the camera: shows as an orphan / ignored sound, not a graded tile
- [ ] Several taps: "tiles seen / taps heard" stays in step

## 4. In-sync start (still without UNO Q / Nano)

| # | Step | Launcher | Pass looks like | Done / notes |
|---|---|---|---|---|
| 5 | Start | `station_start.bat --no-unoq` | broker → station (HELD) → camera → every required node answers → hold released | [ ] |
| 6 | Diagnose | `station_diag.bat` | ping → ports → programs answering → each station's view; exit 0 | [ ] |
| 7 | Stop | `station_stop.bat` | belt 0 %, striker inhibited, line held | [ ] |

- [ ] A deliberately silent node keeps the line **held** (kill the camera service and re-run step 5)
- [ ] Run view shows the right status for each link when one is down

## 5. Measure the placeholders

- [ ] Collect at least 10 real camera→tap pairs; read "suggested max wait" on the Service view and set `ledger.max_wait_s` (placeholder 30 s)
- [ ] Belt speed measured at the chosen PWM (distance / time); written down next to the trigger-line position
- [ ] Run a stretch of tiles and compare "tiles seen" vs "taps heard"; investigate any drift (a missed camera tile shifts every later pairing by one until a timeout)
- [ ] Record real good and defective tile taps (Acoustic-Analysis **Collect**) before setting `station.reference`; until then sound is shown but **does not vote**

## 6. Not ready yet — blocked on other work

- [ ] UNO Q consumer written (subscribe `tile/+/final`, ack, de-duplicate); `pick_place/tile_feed.py` exists but no gantry sequencer uses it
- [ ] Nano firmware written (belt PWM, striker, keep-alive watchdog); `station/tap` format designed
- [ ] `sketch.ino` compiled and run; ToF reading replaced from the placeholder
- [ ] Belt-stop-on-heartbeat-timeout implemented in the Nano (the belt control path runs through the Pi and router)
- [ ] Two e-stops reconciled (Nano cuts power upstream; UNO Q uses the shield Abort pin)
- [ ] Full system: `station_start.bat --belt 40`, then `--with-nano` once wired

## 7. Kiosk (optional)

- [ ] `acoustic_node/deploy/kiosk/` Chromium autostart installed and shows the Run view after reboot
- [ ] Frame rate of the camera MJPEG feed still acceptable with the kiosk browser as a second client

---

## Results log

| Date | Step | Result | Notes |
|---|---|---|---|
| | | | |
