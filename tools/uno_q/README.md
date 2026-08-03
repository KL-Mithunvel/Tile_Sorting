# UNO Q dev tooling

Scripts for working against the lab Arduino UNO Q board (`arduino@172.20.10.2`,
aarch64 Debian, hostname `KLM`) from this Windows dev machine, over its local
hotspot connection.

Only one physical UNO Q board exists right now. The project has multiple planned UNO Q
*node roles* (`acoustic_node/`, `camera_node/`, `pick_place_node/` at the repo root,
each following the Arduino App Bricks folder convention — see `acoustic_node/README.md`)
but that's a code/repo-structure decision, not multiple physical boards yet — this
tooling still only targets the one lab board, and `push.bat` only pushes
`acoustic_node/` since that's the only one with real code so far.

## Quick start

Run these from the repo root (PowerShell or cmd both work).

**One-time setup** — copy the secrets template and fill in the real password:

```powershell
copy tools\uno_q\secrets.bat.example tools\uno_q\secrets.bat
notepad tools\uno_q\secrets.bat
```

Set `UNO_Q_PASS` to the real SSH password in the file that opens, then save and close.
Requires PuTTY (`plink.exe`/`pscp.exe`) and RealVNC Viewer installed — both already
are on this machine.

**Every time you sit down to work:**

```powershell
# Push the current acoustic_node/python/acoustic/, tests/, requirements.txt to the board
tools\uno_q\push.bat

# Open an interactive terminal on the board (run pytest, launch scripts, etc.)
tools\uno_q\ssh.bat

# Start/restart the VNC desktop at 1280x720 and open it in VNC Viewer
tools\uno_q\vnc-start.bat

# When done with the VNC desktop, tear down the tunnel + remote session
tools\uno_q\vnc-stop.bat
```

`ssh.bat` opens a real shell, so from there you can run whatever's needed directly
on the board, e.g.:

```bash
cd ~/tile_sorting
python3 -m pytest tests/ -v
```

## Quick start (Git Bash)

Same scripts, run directly — Git Bash executes `.bat` files fine with no `cmd /c`
wrapper needed. Run from the repo root.

**One-time setup:**

```bash
cp tools/uno_q/secrets.bat.example tools/uno_q/secrets.bat
# edit tools/uno_q/secrets.bat and set UNO_Q_PASS to the real SSH password
```

**Every time you sit down to work:**

```bash
# Push the current acoustic_node/python/acoustic/, tests/, requirements.txt to the board
./tools/uno_q/push.bat

# Open an interactive terminal on the board
./tools/uno_q/ssh.bat

# Start/restart the VNC desktop at 1280x720 and open it in VNC Viewer
./tools/uno_q/vnc-start.bat

# When done with the VNC desktop, tear down the tunnel + remote session
./tools/uno_q/vnc-stop.bat
```

## One-time setup, if starting from scratch on a new machine

```powershell
# Generate a keypair (skip if ~/.ssh/id_ed25519 already exists)
ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\id_ed25519 -N '""'

# Install it on the board (one password prompt)
ssh-copy-id arduino@172.20.10.2
# — or, if ssh-copy-id isn't available on Windows —
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh arduino@172.20.10.2 "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

After that, plain `ssh arduino@172.20.10.2` / `scp` work with no password at all —
useful outside these scripts (e.g. VS Code Remote-SSH).

## Scripts

| Script | Does |
|---|---|
| `push.bat` | Copies `acoustic_node/python/acoustic/`, `tests/`, `requirements.txt`, `pytest.ini` to `~/tile_sorting/` on the device |
| `ssh.bat` | Opens an interactive terminal on the device |
| `vnc-start.bat` | Kills any stale TigerVNC session, restarts it at the configured geometry, opens an SSH tunnel, and launches VNC Viewer |
| `vnc-stop.bat` | Closes the SSH tunnel and kills the remote VNC session |

## Notes

- **Resolution bug fix**: if TigerVNC ever again ignores `-geometry`, it's almost
  certainly a stale session lock (`tigervncserver -list` shows a PID marked
  `(stale)`) — `vnc-start.bat` always kills `%VNC_DISPLAY%` before restarting to
  avoid this.
- The VNC server binds `-localhost yes` on the device (not exposed on the
  hotspot/LAN directly) — `vnc-start.bat` always connects through an SSH tunnel,
  which is the secure way to reach it. Don't change it to bind non-localhost as a
  shortcut.
- If the UNO Q's IP or SSH host key ever changes, update `UNO_Q_HOST` /
  `UNO_Q_HOSTKEY` in `config.bat` (see the comment there for how to re-derive the
  fingerprint). The host key is pinned explicitly via `-hostkey` on every call
  rather than relying on PuTTY's registry cache, so these scripts behave the same
  on any machine with no interactive first-run prompt.
- `push.bat`'s file list is hand-maintained — extend it as more of the project's
  code needs to run on-device (this mirrors how the separate `weather_daq` project's
  `update.bat` is structured, one `pscp` line per folder).
- `secrets.bat` is gitignored (see repo-root `.gitignore`) — never commit it.
