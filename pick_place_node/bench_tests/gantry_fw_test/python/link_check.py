"""Link check for the real gantry firmware (pick_place_node/sketch), through the node's own
BridgeGantryLink + GantrySession. Sends PING, GETSTATE and GETPOS and prints what the session
learned, including any latched alarm and the raw lines. Moves nothing: no HOME, MOVE or VAC.

Run it as the app's main.py (the GUI host is main.py; copy this over it, see README)."""
import time

from pick_place import protocol
from pick_place.bridge_link import BridgeGantryLink
from pick_place.gantry_session import GantrySession

time.sleep(3)  # let the MCU register its Bridge methods
link = BridgeGantryLink(poll_interval_s=0.02, fail_limit=5).open()
session = GantrySession(link)
raw = []


def ask(label, command):
    seq = session.send(command)
    deadline = time.time() + 2.0
    while time.time() < deadline:
        for ev in session.pump():
            raw.append(repr(ev))
        r = session.result(seq)
        if r is not None and r.finished:
            break
        time.sleep(0.02)
    r = session.result(seq)
    print(f"{label}: seq={seq} status={getattr(r, 'status', None)} reason={getattr(r, 'reason', None)} "
          f"fsm={session.fsm} homed={session.homed} alarm={session.alarm} pos={session.pos} "
          f"connected={link.connected} err={link.error}", flush=True)


for label, cmd in (("PING", protocol.ping()), ("GETSTATE", protocol.get_state()), ("GETPOS", protocol.get_pos())):
    ask(label, cmd)
print("RAW EVENTS:", raw[-25:], flush=True)
print("LOGS:", list(session.logs)[-10:], flush=True)
while True:
    time.sleep(5)
