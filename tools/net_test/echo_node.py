#!/usr/bin/env python3
"""Minimal network-test node: answers every ping on the broker.

Run one on each machine (Pi, UNO Q, ...):

    python3 echo_node.py --name pi --host 192.168.1.11

It subscribes to ``nettest/ping`` and replies on ``nettest/pong`` with its name,
hostname and own clock, so the controller (netcheck.py) can report who is
reachable, the round-trip time and the clock offset. It also publishes a
retained ``nettest/node/<name>`` presence message with a last-will, so a node
that dies shows as offline. Needs only paho-mqtt. Does not touch the station
code or topics (``station/*``, ``tile/*``).
"""

from __future__ import annotations

import argparse
import json
import socket
import time

import paho.mqtt.client as mqtt


def make_client(client_id: str) -> mqtt.Client:
    if hasattr(mqtt, "CallbackAPIVersion"):  # paho 2.x
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    return mqtt.Client(client_id=client_id)  # paho 1.x


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True, help="node name shown in the report, e.g. pi, unoq")
    ap.add_argument("--host", default="192.168.1.11", help="broker address (the Pi)")
    ap.add_argument("--port", type=int, default=1883)
    args = ap.parse_args()

    presence_topic = f"nettest/node/{args.name}"
    c = make_client(f"nettest-{args.name}")
    c.will_set(presence_topic, json.dumps({"node": args.name, "online": False}), qos=1, retain=True)

    def announce():
        c.publish(presence_topic, json.dumps({
            "node": args.name, "online": True, "host": socket.gethostname(), "ts": time.time(),
        }), qos=1, retain=True)

    def on_connect(client, userdata, *rest):
        # Subscribe on every (re)connect so a broker restart does not silence us.
        client.subscribe("nettest/ping", qos=0)
        announce()
        print(f"[{args.name}] connected to {args.host}:{args.port}, waiting for pings", flush=True)

    def on_message(client, userdata, msg):
        try:
            ping = json.loads(msg.payload)
        except ValueError:
            return
        client.publish("nettest/pong", json.dumps({
            "id": ping.get("id"), "node": args.name, "host": socket.gethostname(),
            "ts": time.time(),
        }), qos=0)

    c.on_connect = on_connect
    c.on_message = on_message
    c.reconnect_delay_set(min_delay=1, max_delay=5)
    c.connect_async(args.host, args.port, keepalive=10)
    try:
        c.loop_forever(retry_first_connection=True)
    except KeyboardInterrupt:
        c.publish(presence_topic, json.dumps({"node": args.name, "online": False}), qos=1, retain=True)
        c.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
