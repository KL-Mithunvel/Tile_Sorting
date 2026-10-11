"""MQTT access for the master: a paho client and an in-memory twin for tests.

Same small interface as the other nodes' buses (subscribe / publish / start /
stop / connected). Deliberately a copy, not an import: the master runs on a
different machine and must not depend on the station's packages.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Callable

log = logging.getLogger(__name__)

Callback = Callable[[str, bytes], None]


def topic_matches(topic_filter: str, topic: str) -> bool:
    f, t = topic_filter.split("/"), topic.split("/")
    for i, part in enumerate(f):
        if part == "#":
            return True
        if i >= len(t) or (part != "+" and part != t[i]):
            return False
    return len(f) == len(t)


def _encode(payload) -> bytes:
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")


class InMemoryBus:
    def __init__(self):
        self._subs: list[tuple[str, Callback]] = []
        self.published: list[tuple[str, bytes, int, bool]] = []
        self.link_up = True

    @property
    def connected(self) -> bool:
        return self.link_up

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def subscribe(self, topic_filter: str, callback: Callback) -> None:
        self._subs.append((topic_filter, callback))

    def publish(self, topic: str, payload, qos: int = 1, retain: bool = False) -> bool:
        if not self.link_up:
            return False
        data = _encode(payload)
        self.published.append((topic, data, qos, retain))
        for f, cb in list(self._subs):
            if topic_matches(f, topic):
                cb(topic, data)
        return True

    def messages(self, topic_filter: str) -> list[dict]:
        out = []
        for topic, data, _, _ in self.published:
            if topic_matches(topic_filter, topic):
                try:
                    out.append(json.loads(data))
                except ValueError:
                    pass
        return out


class MqttBus:
    def __init__(self, host: str, port: int = 1883, client_id: str = "tile-master"):
        self._host, self._port, self._client_id = host, port, client_id
        self._subs: list[tuple[str, Callback]] = []
        self._lock = threading.Lock()
        self._client = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def subscribe(self, topic_filter: str, callback: Callback) -> None:
        with self._lock:
            self._subs.append((topic_filter, callback))
        if self._client is not None and self._connected:
            self._client.subscribe(topic_filter, qos=1)

    def start(self) -> None:
        import paho.mqtt.client as mqtt

        if hasattr(mqtt, "CallbackAPIVersion"):
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self._client_id)
        else:
            c = mqtt.Client(client_id=self._client_id)
        c.reconnect_delay_set(min_delay=1, max_delay=10)

        def on_connect(cl, userdata, *a):
            self._connected = True
            with self._lock:
                filters = [f for f, _ in self._subs]
            for f in filters:
                cl.subscribe(f, qos=1)

        def on_disconnect(cl, userdata, *a):
            self._connected = False

        def on_message(cl, userdata, msg):
            with self._lock:
                targets = [cb for f, cb in self._subs if topic_matches(f, msg.topic)]
            for cb in targets:
                try:
                    cb(msg.topic, bytes(msg.payload))
                except Exception:  # noqa: BLE001
                    log.exception("handler failed for %s", msg.topic)

        c.on_connect, c.on_disconnect, c.on_message = on_connect, on_disconnect, on_message
        self._client = c
        c.connect_async(self._host, self._port, keepalive=20)
        c.loop_start()

    def stop(self) -> None:
        c, self._client = self._client, None
        if c is not None:
            c.loop_stop()
            try:
                c.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self._connected = False

    def publish(self, topic: str, payload, qos: int = 1, retain: bool = False) -> bool:
        c = self._client
        if c is None:
            return False
        return c.publish(topic, _encode(payload), qos=qos, retain=retain).rc == 0
