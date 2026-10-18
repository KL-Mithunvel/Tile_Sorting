"""Link check for the real gantry firmware (pick_place_node/sketch), through the node's own
BridgeGantryLink + GantrySession. Sends PING, GETSTATE and GETPOS and prints what the session
learned. Moves nothing: no HOME, no MOVE, no VAC.

Needs pick_place/{__init__,protocol,gantry_session,bridge_link,serial_link}.py copied next to
this file on the board (see README)."""
import time

from pick_place import protocol
from pick_place.bridge_link import BridgeGantryLink
from pick_place.gantry_session import GantrySession

time.sleep(3)  # let the MCU register its Bridge methods
link = BridgeGantryLink(poll_interval_s=0.02, fail_limit=5).open()
session = GantrySession(link)


def ask(label, command):
    seq = session.send(command)
    deadline = time.time() + 2.0
    while time.time() < deadline:
        session.pump()
        r = session.result(seq)
        if r is not None and r.finished:
            break
        time.sleep(0.02)
    r = session.result(seq)
    print(f"{label}: seq={seq} status={getattr(r, 'status', None)} fsm={session.fsm} homed={session.homed} "
          f"pos={session.pos} hb_uptime_ms={session.last_hb_uptime_ms} "
          f"connected={link.connected} err={link.error}", flush=True)


for label, cmd in (("PING", protocol.ping()), ("GETSTATE", protocol.get_state()), ("GETPOS", protocol.get_pos())):
    ask(label, cmd)
while True:
    ask("PING", protocol.ping())
    time.sleep(2)
