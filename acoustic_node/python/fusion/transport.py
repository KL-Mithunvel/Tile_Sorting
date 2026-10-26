"""Topic names and the message bus.

`Bus` is the small interface the coordinator needs (publish / subscribe /
connected). Two implementations:

- `InMemoryBus` — synchronous, no network, for tests and the `--demo` mode.
- `MqttBus` — paho-mqtt, imported lazily so this module (and the whole
  fusion package) imports on a machine without it. Subscriptions are
  remembered and re-made on every (re)connect, so a broker restart does not
  leave the station deaf.

Topic names follow Automation_Architecture.md §12 (`tile/<id>/...`,
`machine/alarm`) plus a few `station/...` topics this station adds; the
contract is in documents/programming/station_coordination.md.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Callable, Optional, Protocol

log = logging.getLogger(__name__)

# ---- topics ----------------------------------------------------------------
CAMERA_RESULT_FILTER = "tile/+/camera/result"
ACK_FILTER = "tile/+/ack"
TOPIC_CAMERA_STATUS = "station/camera/status"
TOPIC_STATUS = "station/status"
TOPIC_CMD = "station/cmd"
TOPIC_TAP = "station/tap"  # a bridge (the Nano's serial reader, later) publishes here
TOPIC_ALARM = "machine/alarm"
# Diagnostics: anyone publishes {"id": ...} on TOPIC_DIAG_PING and every node that is
# alive answers on station/diag/pong/<node> with its own id, name and a short status.
# A pong is a real answer from the running program, not just "the port is open".
TOPIC_DIAG_PING = "station/diag/ping"


def diag_pong_topic(node: str) -> str:
    return f"station/diag/pong/{node}"

PLACED_FILTER = "tile/+/placed"  # the UNO Q: this tile was placed on a stack (or the pick failed)
TOPIC_GANTRY_STATUS = "station/gantry/status"  # retained, last-will {"state":"offline"}
TOPIC_GANTRY_CMD = "station/gantry/cmd"  # master/operator -> UNO Q: pause|resume|acknowledge|reset_stack|home|recover
TOPIC_GANTRY_CMD_RESULT = "station/gantry/cmd/result"

TOPIC_NANO_STATUS = "station/nano/status"  # the serial bridge: Nano link alive, belt %, ToF ok
TOPIC_BELT = "station/belt"  # {"percent": 0-100} -> bridge -> Nano (master or operator)
TOPIC_STRIKE = "station/strike"  # {"enabled": bool} -> bridge -> Nano: allow / inhibit the striker
TOPIC_RETRACT = "station/retract"  # bridge: the sprung striker was just energised (retracted) - its own sound
TOPIC_STRIKER_CMD = "station/striker/cmd"  # {"action": mode|retract|release|cycle, ...} -> bridge -> Nano
TOPIC_SESSION_CMD = "station/session/cmd"  # {"action": start|stop, ...} -> every recorder (camera, audio)
TOPIC_SESSION_STATUS = "station/session/status"  # retained: the one session being recorded, or {"active": false}


def tile_topic(tile_id: str, leaf: str) -> str:
    return f"tile/{tile_id}/{leaf}"


def tile_id_from_topic(topic: str) -> Optional[str]:
    parts = topic.split("/")
    return parts[1] if len(parts) >= 3 and parts[0] == "tile" else None


def topic_matches(topic_filter: str, topic: str) -> bool:
    """MQTT wildcard match: `+` is one level, `#` is the rest."""
    f, t = topic_filter.split("/"), topic.split("/")
    for i, part in enumerate(f):
        if part == "#":
            return True
        if i >= len(t):
            return False
        if part != "+" and part != t[i]:
            return False
    return len(f) == len(t)


Callback = Callable[[str, bytes], None]


class Bus(Protocol):
    @property
    def connected(self) -> bool: ...
    def publish(self, topic: str, payload, qos: int = 1, retain: bool = False) -> bool: ...
    def subscribe(self, topic_filter: str, callback: Callback) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...


def _encode(payload) -> bytes:
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")


class InMemoryBus:
    """Delivers to subscribers immediately on the publishing thread. Like a real
    broker it also delivers a client's own messages back to it. `link_up=False`
    simulates the broker being unreachable (publish returns False, nothing is
    delivered)."""

    def __init__(self):
        self._subs: list[tuple[str, Callback]] = []
        self._lock = threading.RLock()
        self.link_up = True
        self.published: list[tuple[str, bytes, int, bool]] = []  # everything accepted, for assertions
        self.retained: dict[str, bytes] = {}

    @property
    def connected(self) -> bool:
        return self.link_up

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def subscribe(self, topic_filter: str, callback: Callback) -> None:
        with self._lock:
            self._subs.append((topic_filter, callback))

    def publish(self, topic: str, payload, qos: int = 1, retain: bool = False) -> bool:
        if not self.link_up:
            return False
        data = _encode(payload)
        with self._lock:
            self.published.append((topic, data, qos, retain))
            if retain:
                self.retained[topic] = data
            targets = [cb for f, cb in self._subs if topic_matches(f, topic)]
        for cb in targets:
            cb(topic, data)
        return True

    def messages(self, topic_filter: str) -> list[dict]:
        """Decoded JSON of every accepted message matching `topic_filter`."""
        out = []
        for topic, data, _, _ in self.published:
            if topic_matches(topic_filter, topic):
                try:
                    out.append(json.loads(data))
                except ValueError:
                    pass
        return out


class MqttBus:
    """paho-mqtt client with automatic reconnect. Works with paho 1.x (apt's
    python3-paho-mqtt on Raspberry Pi OS) and 2.x (pip)."""

    def __init__(
        self,
        host: str,
        port: int = 1883,
        client_id: str = "tile-acoustic",
        keepalive: int = 20,
        will: Optional[tuple[str, dict]] = None,
        max_queued: int = 200,
    ):
        self._host, self._port, self._keepalive = host, port, keepalive
        self._client_id = client_id
        self._will = will
        self._max_queued = max_queued
        self._subs: list[tuple[str, Callback]] = []
        self._lock = threading.Lock()
        self._connected = False
        self._client = None

    @property
    def connected(self) -> bool:
        return self._connected

    def subscribe(self, topic_filter: str, callback: Callback) -> None:
        with self._lock:
            self._subs.append((topic_filter, callback))
        if self._client is not None and self._connected:
            self._client.subscribe(topic_filter, qos=1)

    def start(self) -> None:
        import paho.mqtt.client as mqtt  # lazy: see module docstring

        if hasattr(mqtt, "CallbackAPIVersion"):
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self._client_id)
        else:  # paho 1.x
            client = mqtt.Client(client_id=self._client_id)
        client.reconnect_delay_set(min_delay=1, max_delay=10)
        client.max_queued_messages_set(self._max_queued)
        if self._will is not None:
            topic, payload = self._will
            client.will_set(topic, _encode(payload), qos=1, retain=True)

        def on_connect(c, userdata, *args):
            self._connected = True
            with self._lock:
                filters = [f for f, _ in self._subs]
            for f in filters:
                c.subscribe(f, qos=1)
            log.info("MQTT connected to %s:%s", self._host, self._port)

        def on_disconnect(c, userdata, *args):
            self._connected = False
            log.warning("MQTT disconnected from %s:%s", self._host, self._port)

        def on_message(c, userdata, msg):
            with self._lock:
                targets = [cb for f, cb in self._subs if topic_matches(f, msg.topic)]
            for cb in targets:
                try:
                    cb(msg.topic, bytes(msg.payload))
                except Exception:  # noqa: BLE001 - one bad handler must not kill the network thread
                    log.exception("handler for %s failed", msg.topic)

        client.on_connect, client.on_disconnect, client.on_message = on_connect, on_disconnect, on_message
        self._client = client
        client.connect_async(self._host, self._port, keepalive=self._keepalive)
        client.loop_start()

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
        info = c.publish(topic, _encode(payload), qos=qos, retain=retain)
        return info.rc == 0
