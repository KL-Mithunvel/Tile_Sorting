"""Transport check for the real gantry firmware (pick_place_node/sketch): sends PING and
GETSTATE over the Bridge (g_cmd / g_poll) and prints every reply line. Moves nothing."""
import time
from arduino.app_utils import Bridge

time.sleep(3)  # let the MCU register its Bridge methods
seq = 0


def send(cmd):
    global seq
    seq += 1
    Bridge.call("g_cmd", f"{seq} {cmd}")
    time.sleep(0.3)
    reply = Bridge.call("g_poll")
    print(f"SENT {seq} {cmd} -> {reply!r}", flush=True)


for c in ("PING", "GETSTATE", "GETPOS"):
    try:
        send(c)
    except Exception as e:
        print("CALL FAILED:", c, e, flush=True)
while True:
    try:
        send("PING")
    except Exception as e:
        print("CALL FAILED: PING", e, flush=True)
    time.sleep(2)
