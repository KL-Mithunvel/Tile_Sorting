#!/usr/bin/env python3
"""Whole-network check from one machine (normally the laptop).

    python netcheck.py                      # defaults: broker on the Pi, nodes pi + unoq
    python netcheck.py --expect pi unoq master --pings 10

Reports, per target:
  1. ping + TCP ports (is the machine and its services reachable at all)
  2. MQTT broker: connect, then publish/subscribe round trip to itself
  3. every echo_node.py that answered: round-trip time (min/avg/max over N
     pings), packets lost, and clock offset vs this machine (the control LAN has
     no internet NTP, so drift between nodes is expected until chrony is set up)
Exit code 0 only if the broker works and every --expect node answered every ping.
"""

from __future__ import annotations

import argparse
import json
import socket
import statistics
import subprocess
import sys
import threading
import time
import uuid

import paho.mqtt.client as mqtt

# name -> (address, ports worth checking). Addresses: documents/electrical/network_plan.md
TARGETS = {
    "pi": ("192.168.1.11", [22, 1883, 5000, 5001, 5900]),
    "unoq": ("192.168.1.15", [22]),
}


def make_client(client_id: str) -> mqtt.Client:
    if hasattr(mqtt, "CallbackAPIVersion"):
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    return mqtt.Client(client_id=client_id)


def ping_host(addr: str) -> bool:
    flag = "-n" if sys.platform.startswith("win") else "-c"
    r = subprocess.run(["ping", flag, "1", "-w" if flag == "-n" else "-W",
                        "1500" if flag == "-n" else "2", addr],
                       capture_output=True, text=True)
    return r.returncode == 0


def port_open(addr: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((addr, port), timeout=timeout):
            return True
    except OSError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--broker", default=TARGETS["pi"][0])
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--expect", nargs="*", default=["pi", "unoq"], help="echo nodes that must answer")
    ap.add_argument("--pings", type=int, default=5)
    ap.add_argument("--interval", type=float, default=0.3)
    args = ap.parse_args()
    ok = True

    print("== 1. Reachability")
    for name, (addr, ports) in TARGETS.items():
        up = ping_host(addr)
        states = ", ".join(f"{p}:{'open' if port_open(addr, p) else 'closed'}" for p in ports) if up else "-"
        print(f"  {name:5s} {addr:15s} ping {'OK ' if up else 'FAIL'}  ports {states}")

    print("== 2. MQTT broker")
    pongs: dict[str, list[tuple[float, float, float]]] = {}  # node -> [(rtt_s, offset_s, ...)]
    sent: dict[str, float] = {}
    selfecho = threading.Event()
    connected = threading.Event()
    presence: dict[str, dict] = {}
    lock = threading.Lock()
    self_topic = f"nettest/self/{uuid.uuid4().hex[:8]}"

    c = make_client(f"netcheck-{uuid.uuid4().hex[:6]}")

    def on_connect(client, userdata, *rest):
        client.subscribe([("nettest/pong", 0), ("nettest/node/+", 0), (self_topic, 0)])
        connected.set()

    def on_message(client, userdata, msg):
        recv = time.time()
        if msg.topic == self_topic:
            selfecho.set()
            return
        try:
            data = json.loads(msg.payload)
        except ValueError:
            return
        if msg.topic.startswith("nettest/node/"):
            with lock:
                presence[data.get("node", msg.topic)] = data
            return
        pid = data.get("id")
        with lock:
            if pid in sent:
                rtt = recv - sent[pid]
                # node clock minus this clock, assuming the reply left mid-round-trip
                offset = data["ts"] - (sent[pid] + rtt / 2)
                pongs.setdefault(data["node"], []).append((rtt, offset, 0.0))

    c.on_connect = on_connect
    c.on_message = on_message
    try:
        c.connect(args.broker, args.port, keepalive=10)
    except OSError as exc:
        print(f"  broker {args.broker}:{args.port}  FAIL ({exc})")
        return 2
    c.loop_start()
    if not connected.wait(4.0):
        print(f"  broker {args.broker}:{args.port}  FAIL (no CONNACK)")
        return 2
    t0 = time.time()
    c.publish(self_topic, "x")
    if selfecho.wait(3.0):
        print(f"  broker {args.broker}:{args.port}  OK  publish->subscribe {1000 * (time.time() - t0):.1f} ms")
    else:
        print("  broker accepted the connection but did not deliver our own message  FAIL")
        ok = False

    print(f"== 3. Echo nodes ({args.pings} pings each)")
    time.sleep(0.5)  # let retained presence messages arrive
    for i in range(args.pings):
        pid = uuid.uuid4().hex[:8]
        with lock:
            sent[pid] = time.time()
        c.publish("nettest/ping", json.dumps({"id": pid}))
        time.sleep(args.interval)
    time.sleep(1.0)
    c.loop_stop()
    c.disconnect()

    names = sorted(set(pongs) | set(args.expect) | {n for n, d in presence.items() if d.get("online")})
    for name in names:
        got = pongs.get(name, [])
        if not got:
            seen = presence.get(name)
            why = "last seen offline" if seen and not seen.get("online") else "never announced"
            print(f"  {name:8s} NO ANSWER ({why})" + ("  <- expected" if name in args.expect else ""))
            ok = ok and name not in args.expect
            continue
        rtts = [1000 * g[0] for g in got]
        offs = [g[1] for g in got]
        lost = args.pings - len(got)
        print(f"  {name:8s} OK  rtt min/avg/max {min(rtts):.1f}/{statistics.mean(rtts):.1f}/{max(rtts):.1f} ms"
              f"  lost {lost}/{args.pings}  clock offset {statistics.median(offs):+.3f} s")
        if lost and name in args.expect:
            ok = False

    print("== RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
