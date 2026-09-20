# Camera Node — Raspberry Pi 5 Deployment

Deployment for the visual inspection station. Target: **Raspberry Pi 5, 16 GB RAM,
Raspberry Pi OS Desktop (64-bit)** — decided 2026-09-20, see
`documents/project/Automation_Architecture.md` §5.2 for the decision and its reasoning,
and §5.7 for why this node is systemd-deployed rather than run as an Arduino App Lab app
like `acoustic_node/` and `pick_place_node/`.

> **Nothing in this folder has been run.** It was written the same day the Pi decision
> was made, against documented Raspberry Pi OS package names and systemd behaviour. No
> Pi has been set up, no camera attached. Read the scripts before running them, and
> expect to fix something. This is a starting point, not a verified procedure.

## Why apt packages and not a venv

`install_pi.sh` installs `python3-picamera2`, `python3-opencv`, `python3-flask`,
`python3-yaml`, `python3-numpy` and `python3-pil` from apt, and the systemd unit runs
`/usr/bin/python3` — no virtualenv.

That's deliberate. `python3-picamera2` is compiled against the system numpy and the
system libcamera. Creating an isolated venv and pip-installing a second numpy and a
second OpenCV next to it is the usual route to an ABI mismatch that doesn't fail at
import time — it fails at the first `capture_array()`, on the rig, at the worst moment.
Every module this node imports (`cv2`, `numpy`, `flask`, `yaml`) is packaged by
Raspberry Pi OS, so there is nothing to gain from isolating it.

If you're running a **USB webcam only** and don't need the CSI module, picamera2 is out
of the picture and a normal venv is fine — use `requirements-pi.txt` and repoint the
unit's `ExecStart`.

## Optional: the ONNX grade model

`config.yaml`'s `grading_model` block runs a small ONNX classifier as a second opinion on
each tile (see `camera_node/README.md`). It is **off by default** and the station grades
tiles without it, so this is an opt-in step:

```bash
./camera_node/deploy/install_pi.sh --with-grading
```

`onnxruntime` is the one dependency apt doesn't package, so that flag pip-installs it
into the system interpreter with `--break-system-packages`. That sounds worse than it is:
we're adding a single pure-addition wheel to the interpreter the service already runs,
rather than building a venv that would break picamera2's numpy/libcamera ABI. Pillow
comes from apt as `python3-pil` and is installed either way.

The step is deliberately **non-fatal** — if the wheel won't install, the script warns and
carries on, and `grading_model.from_config()` returns None with a printed reason instead
of stopping the service. Then set `grading_model.enabled: true` in `config.yaml` and
restart.

Unverified like everything else here: if pip can't find a wheel for this Python/arch,
reach for piwheels or a venv rather than fighting it.

## Install

On the Pi, from a clone of this repo:

```bash
git clone <this repo> ~/Tile_Sorting
cd ~/Tile_Sorting
chmod +x camera_node/deploy/install_pi.sh
./camera_node/deploy/install_pi.sh
```

The script figures out the repo path and the invoking user itself and substitutes them
into `tile-camera.service` — that's why the checked-in unit file has `@PYTHON_DIR@` /
`@RUN_USER@` placeholders and shouldn't be copied into `/etc/systemd/system/` by hand.

## First run — check the camera before starting the service

```bash
cd ~/Tile_Sorting/camera_node/python
/usr/bin/python3 -m camera.live_dashboard --list-devices
```

This lists USB cameras by OpenCV index and CSI modules by libcamera camera number. Pin
whichever you're using in `camera_node/python/camera/config.yaml`:

| Camera | Set |
|---|---|
| CSI Raspberry Pi camera module | `camera.backend: "picamera2"`, `camera.picamera2.camera_num` |
| USB / UVC webcam | `camera.backend: "usb"`, `camera.device_index`, and `camera.api_preference: "v4l2"` |
| Either, auto-detected | `camera.backend: "auto"` (the default) |

`api_preference: "v4l2"` matters on the Pi — left at `"any"`, OpenCV can pick a GStreamer
path that silently ignores the requested resolution and frame rate.

Then run it in the foreground once to see it work:

```bash
/usr/bin/python3 -m camera.live_dashboard
```

and open `http://localhost:5000/` on the Pi's own desktop, or
`http://<pi-ip>:5000/` from another machine on the network.

## Service control

```bash
sudo systemctl start   tile-camera
sudo systemctl status  tile-camera
sudo systemctl restart tile-camera     # after editing config.yaml
journalctl -u tile-camera -f           # live logs
```

The unit is enabled at install time, so it comes back on reboot.

## Showing the dashboard on a station monitor

The Pi runs the **Desktop** image, so it has a browser — the station can display its own
dashboard on an attached monitor without a second machine. Autostart Chromium in kiosk
mode for the desktop user:

```bash
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/tile-camera-kiosk.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Tile Camera Dashboard
Exec=chromium-browser --kiosk --noerrdialogs --disable-infobars http://localhost:5000/
X-GNOME-Autostart-enabled=true
EOF
```

Worth knowing: the dashboard's MJPEG stream re-encodes every frame to JPEG, and a local
kiosk browser is one more client consuming it. If the station's frame rate drops once the
kiosk is up, that's why — drop `dashboard.jpeg_quality` in `config.yaml`, or run the
service with `--no-dashboard` and view results another way.

## Things to re-check on this hardware that the dev laptop can't tell us

- **`camera.device_index` / `camera_num`** — always machine-specific, same caveat the
  mic's `device: null` has in `acoustic_node`.
- **`segmentation.min_tile_area_px` and `crack_detection.border_margin_px`** are
  absolute pixel counts, calibrated against close-up photos at a different resolution
  and working distance than the rig will have. They must be re-derived with
  `development/analyze_dataset.py` once the camera's real mounting position is fixed.
  See `.claude/CLAUDE.md` Known Technical Debt.
- **Exposure and white balance.** With auto-exposure and AWB left on, the sensor
  re-exposes per tile and the HSV range in `config.yaml` stops meaning anything. Once the
  enclosure lighting is final, lock them via `camera.picamera2.controls` (there's a
  worked example in `config.yaml`). This did not matter on a laptop webcam pointed at a
  desk; it will matter on the line.
- **Thermals.** A Pi 5 running continuous CV wants an active cooler. Watch for
  `throttled` in `vcgencmd get_throttled` during a long run.
- **Frame rate under load.** `target_fps: 15` was never load-tested; the per-frame
  segmentation + JPEG encode is the cost, and it's untested on this CPU. If the grading
  model is enabled, add its per-tile inference on top — measured at 2.5 ms on a dev
  laptop at 2 threads, not on a Pi 5. `grading_model.threads` is pinned low on purpose:
  the Pi has 4 cores shared with the capture thread and the Flask server, and letting
  inference take all of them starves the MJPEG stream.
- **The trigger line's position.** `trigger_line.position` and `direction` describe where
  tiles cross the frame and which way they travel; both are properties of the rig's
  camera mounting and conveyor direction, so they can only be set once the camera is
  physically mounted. See `camera_node/README.md` → Capture trigger.
