"""Entry point for the pick-and-place node on the UNO Q (App Lab runs `python/main.py`).

Runs the gantry station service (`pick_place.gantry_station`): tile finals in from
the Pi's broker, picks on the gantry, `placed` / status / alarms back out.
Backend, broker address and which remote commands are allowed come from
`pick_place/config.yaml` (`backend:`, `gantry.station`); override on the command
line the same way as the module (`--host`, `--backend`).

On the board the MCU is reached through the App Lab Bridge (`g_cmd` / `g_poll`, see
`sketch/sketch.ino`), so this entry point defaults to `--backend bridge` unless a backend
is given. Checked 2026-10-18 on the lab UNO Q: PING / GETSTATE / GETPOS through the link.
UNVERIFIED: that this file starts under App Lab as written, paho-mqtt in the container
(`requirements.txt`), a broker (the Pi's `192.168.1.11` is not set up; the client retries),
and anything that moves - homing, the job runner, vacuum. `home_on_start` is false and no
tile arrives without a broker, so nothing moves on its own. Run by hand over SSH with
`python -m pick_place.gantry_station --host 192.168.1.11 --backend sim` for the simulated MCU.
"""

import sys

from pick_place.gantry_station import main

if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--backend" not in argv:
        argv += ["--backend", "bridge"]
    sys.exit(main(argv))
