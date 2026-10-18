"""Hosts the node's GUI (the twin dashboard, port 5050) against the REAL gantry MCU through
the Bridge: `python -m pick_place.twin_prototype --backend bridge`.

The page has HOME / jog / vacuum / mode controls that move the real machine, and it is served
on 0.0.0.0, so anyone on the network can use them. Bench use only. Needs the whole
pick_place package (templates/, static/, config.yaml) next to this file on the board, and
port 5050 in the app's app.yaml. `link_check.py` is the non-moving PING check.
"""
import sys
import time

time.sleep(3)  # let the MCU register its Bridge methods
from pick_place.twin_prototype import main  # noqa: E402

if __name__ == "__main__":
    main(["--backend", "bridge"] + sys.argv[1:])
