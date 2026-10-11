# tools/pi — Raspberry Pi 5 station access

Batch scripts for the Pi 5 (camera + acoustic station), mirroring `tools/uno_q/`.
Windows only. Needs `plink` (PuTTY) on PATH for `ssh.bat`; RealVNC Viewer for `vnc.bat`.

| File | Purpose |
|---|---|
| `config.bat` | Host, user, pinned SSH host key, VNC port and viewer path (git-tracked, no secrets) |
| `secrets.bat` | `PI_PASS` — **gitignored**. Copy `secrets.bat.example` and fill it in |
| `ssh.bat` | Interactive terminal on the Pi |
| `vnc.bat` | Opens the Pi's desktop in RealVNC Viewer |
| `push.bat` | Copies `camera_node\` to `~/tile_sorting/` on the Pi (tar over SSH, no `__pycache__`) |
| `install.bat` | **First time only:** push, then run `camera_node/deploy/install_pi.sh` (interactive; `--with-grading` optional) |
| `deploy.bat` | Everyday: push, then restart the `tile-camera` service |
| `status.bat` | Read-only health check: service, temperature/throttling, network, disk, camera |
| `logs.bat` | Last 100 lines of the service log; `logs.bat -f` follows |
| `run.bat` | Run the camera node in the **foreground** with output in your terminal (stops the service first, restarts it after). `run.bat --list-devices`, `run.bat --backend usb`, ... |
| `station_push.bat` | Copies `camera_node\`, `acoustic_node\` and the fake peers to the Pi (the **station**: decision service, broker deploy, diagnostics) |
| `station_install.bat` | **First time only:** push, then run `acoustic_node/deploy/install_pi.sh` (apt packages, mosquitto, `tile-acoustic` unit, the `--mqtt` drop-in for `tile-camera`) |
| `station_test.bat [sec]` | Software **self-test** on the Pi against the real broker: synthetic mic + fake camera / striker / UNO Q. Restarts `tile-acoustic` afterwards |
| `station_diag.bat` | **Diagnostic**: is everything on the network up *and answering* (ping, ports, then every running program answers a diagnostic ping). Read-only |
| `station_status.bat` | Services, ports, station's own view, USB topology (mic vs camera), audio inputs, serial ports, thermal. Read-only |
| `station_start.bat [--no-unoq] [--belt N]` | **In-sync start**: broker -> station (held) -> camera -> verify every node answers -> release hold + belt. Any silent node = the line stays held |
| `station_stop.bat [--services]` | Belt 0 %, striker inhibited, line held; `--services` also stops the services |
| `station_logs.bat [-f]` | `tile-acoustic` journal |
| `pull.bat` | **Bring the Pi's data back** into `data\pi\` - new recordings (+ their `meta.json`) and the per-tile photos. `pull.bat --captures` = photos only. Re-running only fetches what is new |

**Status:** all written 2026-10-11, **none run against the Pi** (the laptop's Ethernet was
unplugged). Only the local half was tested: the tar package contents and `push.bat`'s
failure message. `install.bat` drives `install_pi.sh`, which has never met a real Pi.
First run order: `ssh.bat` → `status.bat` → `install.bat` → `deploy.bat` / `logs.bat`.

## Collect -> label -> tune loop

```text
1. deploy.bat                         push camera_node\ + restart the service on the Pi
2. browser: http://192.168.1.11:5000  pick the camera, press Record, run tiles past, Stop
3. pull.bat                           recordings land in data\pi\recordings\<date_time>\
4. python development\export_tile_dataset.py --videos data\pi\recordings
                                      every graded tile -> data\tile_dataset\<run>\
5. python development\annotate_tiles.py data\tile_dataset\<run>
                                      you label each tile (defect + tier)
6. python development\tune_from_labels.py data\tile_dataset\<run>\human_labels.json
                                      threshold advice from YOUR labels (changes nothing)
```

**Faster route when you already know which tiles are which** - skip steps 3-5 and use the
dashboard's **Label** panel instead: press *Start labelling*, set "this batch is all fine /
chipped / cracked", and run that batch past the line. Every tile is saved as a lossless PNG
(+ the whole frame) with your verdict in `data\labelled\<date_time>\` on the Pi; `pull.bat`
does not fetch it yet, so copy that folder (or add `labelled` to `pull.bat`) and open it in
`annotate_tiles.py` or feed `human_labels.json` to `tune_from_labels.py`. Keys 1-5 / 6-9 set the
verdict / tier of the tile that just crossed. To check detection and counting rather than
grades, open a recording in `python development\review_video.py`.

Step 4 runs the pipeline on the laptop, from the raw recording, so the same footage can be
re-graded with new thresholds later without going back to the Pi. For a good dataset: light
the belt well (the webcam's auto exposure smears ~7 px), include known-good and known-damaged
tiles, and use the annotator's note box (e.g. "back face").

Annotator keys: `1-5` defect, `6-9` tier, `N` next unlabelled, `Z` zoom, `C` belt context,
`H` show the pipeline's opinion (hidden by default so it cannot bias you).

**Status of `pull.bat` / `run.bat`:** `pull.bat` was tested against a *fake* plink running in
a local folder (quoting, the loop, "only fetch new / re-fetch an unfinished recording"); it has
never talked to the real Pi. `run.bat` is untested. Both assume the password has none of
`& | < > ^ %`, and `pull.bat` calls Windows' own `System32\tar.exe` by full path because the
GNU `tar` that Git for Windows puts on PATH misreads `C:\` paths.

## Setup

```bat
copy tools\pi\secrets.bat.example tools\pi\secrets.bat
rem then edit secrets.bat and set PI_PASS
```

## Notes

- **Address:** `192.168.1.11` is the D-Link LAN reservation (stable). The Pi also joins the
  phone hotspot on Wi-Fi at `172.20.10.x`, a lease that changes — don't use it here.
- **Laptop must be on the D-Link LAN** (Ethernet cable in) for these to connect.
- **VNC** is the Pi's built-in server on port 5900, reachable on the LAN with no tunnel
  (the UNO Q's is localhost-only and tunnelled). It is unencrypted-by-default and only
  acceptable on the isolated control LAN — see `documents/electrical/network_plan.md` §7.
- **The password is weak** (`secrets.bat` says so). Change it on the Pi with `passwd`,
  update `secrets.bat`, then move to SSH keys.
- Network setup history and the Pi's routing fix: `documents/electrical/network_plan.md` §5.1.
