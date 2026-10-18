"""Supervised homing run on the real gantry firmware: HOME ALL (Z, then X, then Y), printing
state / position / encoders / alarms twice a second, then idling. Sends STOP on an alarm,
an exception or a lost link. Run as the app's main.py (see README)."""
import time

from pick_place import protocol
from pick_place.bridge_link import BridgeGantryLink
from pick_place.gantry_session import GantrySession
from pick_place.protocol import Alarm, Done, Err

time.sleep(3)  # let the MCU register its Bridge methods
link = BridgeGantryLink(poll_interval_s=0.02, fail_limit=5).open()
session = GantrySession(link)
events = []


def pump():
    for ev in session.pump():
        if not hasattr(ev, "uptime_ms"):  # skip heartbeats
            events.append(ev)
            if isinstance(ev, (Alarm, Err, Done)):
                print("EVENT", ev, flush=True)


try:
    seq = session.send(protocol.get_state())
    time.sleep(0.5)
    pump()
    print("START fsm=%s alarm=%s" % (session.fsm, session.alarm), flush=True)
    seq = session.send(protocol.home("ALL"))
    print("HOME ALL sent, seq=%d" % seq, flush=True)
    t0 = time.time()
    last_pos = 0.0
    while time.time() - t0 < 60:
        pump()
        if time.time() - last_pos >= 0.5:
            last_pos = time.time()
            session.send(protocol.get_pos())
            print("t=%4.1f fsm=%s homed=%s alarm=%s pos=%s" % (time.time() - t0, session.fsm, session.homed,
                                                              session.alarm, session.pos), flush=True)
        if session.alarm is not None or not link.connected or session.fsm in ("IDLE",) and session.homed:
            break
        time.sleep(0.05)
    print("END fsm=%s homed=%s alarm=%s pos=%s" % (session.fsm, session.homed, session.alarm, session.pos), flush=True)
except Exception as exc:  # noqa: BLE001
    print("EXCEPTION", exc, flush=True)
finally:
    try:
        session.send(protocol.stop())
        print("STOP sent", flush=True)
    except Exception as exc:  # noqa: BLE001
        print("STOP failed", exc, flush=True)
while True:
    time.sleep(5)
