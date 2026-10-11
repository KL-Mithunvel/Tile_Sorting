#!/usr/bin/env python3
"""Tiny MQTT helper for the diagnostic and start scripts (paho only, no
mosquitto-clients needed).

    mqtt_probe.py ping [--host H] [--wait 3]      # ask every node to answer, print who did
    mqtt_probe.py pub TOPIC JSON [--host H]       # publish one message
    mqtt_probe.py get TOPIC [--host H] [--wait 3] # print the first (retained) message on TOPIC

`ping` prints one JSON object: {"broker": bool, "nodes": {name: {ok, latency_ms,
detail}}}. A node is listed only if its running program answered — an open port
is not an answer.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import uuid


def _client(host: str, port: int):
    import paho.mqtt.client as mqtt

    if hasattr(mqtt, "CallbackAPIVersion"):
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"probe-{uuid.uuid4().hex[:8]}")
    else:
        c = mqtt.Client(client_id=f"probe-{uuid.uuid4().hex[:8]}")
    connected = threading.Event()
    c.on_connect = lambda *a, **k: connected.set()
    try:
        c.connect(host, port, keepalive=10)
    except OSError:
        return None, None
    c.loop_start()
    if not connected.wait(3.0):
        c.loop_stop()
        return None, None
    return c, connected


def cmd_ping(args) -> int:
    c, _ = _client(args.host, args.port)
    if c is None:
        print(json.dumps({"broker": False, "nodes": {}}))
        return 2
    probe_id = uuid.uuid4().hex[:8]
    sent_at = time.time()
    nodes: dict = {}

    def on_message(client, userdata, msg):
        try:
            data = json.loads(msg.payload)
        except ValueError:
            return
        if isinstance(data, dict) and data.get("id") == probe_id and data.get("node"):
            nodes[data["node"]] = {
                "ok": bool(data.get("ok")),
                "latency_ms": round((time.time() - sent_at) * 1000.0),
                "detail": data.get("detail"),
            }

    c.on_message = on_message
    c.subscribe("station/diag/pong/#", qos=0)
    time.sleep(0.2)
    sent_at = time.time()
    c.publish("station/diag/ping", json.dumps({"id": probe_id}), qos=0)
    deadline = time.time() + args.wait
    while time.time() < deadline:
        if args.expect and all(n in nodes for n in args.expect):
            break
        time.sleep(0.05)
    c.loop_stop()
    c.disconnect()
    print(json.dumps({"broker": True, "nodes": nodes}))
    return 0


def cmd_pub(args) -> int:
    c, _ = _client(args.host, args.port)
    if c is None:
        print("broker not reachable", file=sys.stderr)
        return 2
    info = c.publish(args.topic, args.payload, qos=1)
    info.wait_for_publish(3.0)
    c.loop_stop()
    c.disconnect()
    return 0 if info.is_published() else 1


def cmd_get(args) -> int:
    c, _ = _client(args.host, args.port)
    if c is None:
        return 2
    got: list = []
    c.on_message = lambda cl, ud, msg: got.append(msg.payload.decode("utf-8", "replace"))
    c.subscribe(args.topic, qos=1)
    deadline = time.time() + args.wait
    while time.time() < deadline and not got:
        time.sleep(0.05)
    c.loop_stop()
    c.disconnect()
    if not got:
        return 1
    print(got[0])
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("ping", "pub", "get"):
        s = sub.add_parser(name)
        s.add_argument("--host", default="localhost")
        s.add_argument("--port", type=int, default=1883)
    sub.choices["ping"].add_argument("--wait", type=float, default=3.0)
    sub.choices["ping"].add_argument("--expect", nargs="*", default=[], help="stop early once all of these answered")
    sub.choices["pub"].add_argument("topic")
    sub.choices["pub"].add_argument("payload")
    sub.choices["get"].add_argument("topic")
    sub.choices["get"].add_argument("--wait", type=float, default=3.0)
    args = p.parse_args(argv)
    return {"ping": cmd_ping, "pub": cmd_pub, "get": cmd_get}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
