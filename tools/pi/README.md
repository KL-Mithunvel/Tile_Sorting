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

**Status:** all written 2026-10-11, **none run against the Pi** (the laptop's Ethernet was
unplugged). Only the local half was tested: the tar package contents and `push.bat`'s
failure message. `install.bat` drives `install_pi.sh`, which has never met a real Pi.
First run order: `ssh.bat` → `status.bat` → `install.bat` → `deploy.bat` / `logs.bat`.

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
