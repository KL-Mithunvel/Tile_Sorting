"""Entry point for the pick-and-place node on the UNO Q (App Lab runs `python/main.py`).

Runs the gantry station service (`pick_place.gantry_station`): tile finals in from
the Pi's broker, picks on the gantry, `placed` / status / alarms back out.
Backend, broker address and which remote commands are allowed come from
`pick_place/config.yaml` (`backend:`, `gantry.station`); override on the command
line the same way as the module (`--host`, `--backend`).

UNVERIFIED on the board: that App Lab starts this file as written, that paho-mqtt
is importable in its container, and that the `serial` backend's port
(`gantry.serial.port`) is how Python reaches the MCU from there - the RouterBridge
path (`Bridge.call`) is the documented alternative and is not wired. Until that is
settled, run it by hand over SSH:

    python -m pick_place.gantry_station --host 192.168.1.11 --backend sim
"""

import sys

from pick_place.gantry_station import main

if __name__ == "__main__":
    sys.exit(main())
