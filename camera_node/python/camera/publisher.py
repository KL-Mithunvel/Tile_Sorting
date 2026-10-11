"""Publishes each graded tile (and a heartbeat) to the station's MQTT broker so
the acoustic/decision service on the same Pi can add the tap result and decide.

Why this exists: the camera is the first station to see a tile, so it MINTS the
tile's identity here, and the downstream stations only ever refer to it. The
master PC does not take part (the line must run without it); it may subscribe.

Design points, each from a failure it prevents:

- Tile ids are `T<YYYYMMDD>-<HHMMSS>-<n>` from the publisher's OWN counter, not
  `TileRecord.seq`: seq restarts from 1 whenever the video source changes and
  at every service restart, so two different tiles would share an id.
- Only tiles from a LIVE camera are published. A recording replayed through the
  dashboard is graded exactly like a live feed; sending it on would put
  phantom tiles into the line's ledger and sort them.
- A broker failure must never cost the inspection: every method here swallows
  and logs its own errors, and the worker additionally guards the call.
- Heartbeat on its own thread, so "camera alive" stays truthful on a belt with
  no tiles on it. The last-will tells the station if this process dies.

paho-mqtt is imported lazily and the bus is injectable, so this module imports
(and is unit-tested) without a broker or without paho installed. The topic and
payload contract is documented in documents/programming/station_coordination.md
and checked against the fusion package by tests/test_station_contract.py.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from typing import Callable, Optional

log = logging.getLogger(__name__)

TOPIC_STATUS = "station/camera/status"
DIAG_PING_TOPIC = "station/diag/ping"
DIAG_PONG_TOPIC = "station/diag/pong/camera"


def tile_topic(tile_id: str, leaf: str) -> str:
    return f"tile/{tile_id}/{leaf}"


def make_run_id(now: Optional[float] = None) -> str:
    return datetime.fromtimestamp(time.time() if now is None else now).strftime("%Y%m%d-%H%M%S")


def make_tile_id(run_id: str, n: int) -> str:
    return f"T{run_id}-{n:05d}"


def build_camera_payload(tile_id: str, run_id: str, rec: dict, ts: float) -> dict:
    """The `tile/<id>/camera/result` message from `tile_record_to_dict()` output."""
    return {
        "tile_id": tile_id,
        "run_id": run_id,
        "seq": rec.get("seq"),
        "station": "camera",
        "status": "done",
        # The station grade (rules, plus the model's vote when it counted).
        "grade": rec.get("grade"),
        "rule_grade": rec.get("rule_grade"),
        "model_grade": rec.get("model_grade"),
        "model_used": rec.get("model_used"),
        "crack_detected": rec.get("crack_detected"),
        "crack_severity": rec.get("crack_severity"),
        "crack_length_px": rec.get("crack_length_px"),
        "corner_broken": rec.get("corner_broken"),
        "corner_fill_ratio": rec.get("corner_fill_ratio"),
        "snapshot_path": rec.get("snapshot_path"),
        "ts": ts,
    }


def status_from_shared_state(state) -> dict:
    """What the heartbeat says about this camera: is its source a live camera
    and running, and how many tiles it has crossed. Read by the station's
    health strip."""
    st = state.status()
    src = st.get("source") or {}
    return {
        "source_kind": src.get("kind"),
        "source_status": src.get("status"),
        "station_state": st.get("station_state"),
        "tile_count": st.get("tile_count"),
        "errors": (st.get("errors") or {}).get("count", 0),
    }


class _PahoBus:
    """Minimal paho client (1.x and 2.x), auto-reconnecting, with a last-will."""

    def __init__(self, host: str, port: int, client_id: str, will_topic: str, will_payload: dict):
        self._host, self._port, self._client_id = host, port, client_id
        self._will = (will_topic, will_payload)
        self._client = None
        self._subs: list = []
        self.connected = False

    def subscribe(self, topic_filter: str, callback) -> None:
        self._subs.append((topic_filter, callback))
        if self._client is not None and self.connected:
            self._client.subscribe(topic_filter, qos=0)

    def start(self) -> None:
        import paho.mqtt.client as mqtt

        if hasattr(mqtt, "CallbackAPIVersion"):
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self._client_id)
        else:
            c = mqtt.Client(client_id=self._client_id)
        c.reconnect_delay_set(min_delay=1, max_delay=10)
        c.max_queued_messages_set(200)
        c.will_set(self._will[0], json.dumps(self._will[1]), qos=1, retain=True)
        def on_connect(client, userdata, *a):
            self.connected = True
            for f, _ in self._subs:  # re-made on every (re)connect
                client.subscribe(f, qos=0)

        def on_message(client, userdata, msg):
            for f, cb in self._subs:
                if f == msg.topic:
                    try:
                        cb(msg.topic, bytes(msg.payload))
                    except Exception:  # noqa: BLE001
                        log.exception("handler for %s failed", msg.topic)

        c.on_connect = on_connect
        c.on_message = on_message
        c.on_disconnect = lambda *a, **k: setattr(self, "connected", False)
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
        self.connected = False

    def publish(self, topic: str, payload: dict, qos: int = 1, retain: bool = False) -> bool:
        c = self._client
        if c is None:
            return False
        return c.publish(topic, json.dumps(payload, default=str), qos=qos, retain=retain).rc == 0


class TilePublisher:
    def __init__(
        self,
        cfg: dict,
        status_source: Optional[Callable[[], dict]] = None,
        bus=None,
        clock: Callable[[], float] = time.time,
    ):
        """`cfg` is config.yaml's `mqtt` block. `status_source()` returns the
        dict the heartbeat reports (source kind/status, tile count, ...). `bus`
        is injectable for tests."""
        self._cfg = cfg
        self._status_source = status_source
        self._clock = clock
        self.run_id = make_run_id(clock())
        self._n = 0
        self._lock = threading.Lock()
        self._bus = bus
        self._heartbeat_s = float(cfg.get("heartbeat_s", 2.0))
        self._thread: Optional[threading.Thread] = None
        self._halt = threading.Event()
        self.published = 0
        self.dropped = 0

    # ---- lifecycle -------------------------------------------------------

    def _on_ping(self, topic: str, payload: bytes) -> None:
        try:
            probe = json.loads(payload) if payload else {}
        except ValueError:
            probe = {}
        detail = dict(self._status_source() if self._status_source else {})
        detail.update(run_id=self.run_id, published=self.published, dropped=self.dropped)
        live = detail.get("source_kind") == "camera" and detail.get("source_status") == "running"
        try:
            self._bus.publish(
                DIAG_PONG_TOPIC,
                {"id": probe.get("id") if isinstance(probe, dict) else None, "node": "camera",
                 "ts": self._clock(), "ok": bool(live), "detail": detail},
                qos=0,
            )
        except Exception:  # noqa: BLE001
            log.exception("diagnostic answer failed")

    def start(self) -> None:
        if self._bus is None:
            self._bus = _PahoBus(
                host=self._cfg.get("host", "127.0.0.1"),
                port=int(self._cfg.get("port", 1883)),
                client_id=self._cfg.get("client_id", "tile-camera"),
                will_topic=TOPIC_STATUS,
                will_payload={"state": "offline"},
            )
        if hasattr(self._bus, "subscribe"):
            self._bus.subscribe(DIAG_PING_TOPIC, self._on_ping)
        try:
            self._bus.start()
        except Exception:  # noqa: BLE001 - e.g. paho not installed: keep inspecting
            log.exception("MQTT publisher could not start; tiles will not be forwarded")
            return
        self._halt.clear()
        self._thread = threading.Thread(target=self._heartbeat_loop, name="camera-heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._halt.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._bus is not None:
            try:
                self._bus.publish(TOPIC_STATUS, {"state": "offline"}, qos=1, retain=True)
                self._bus.stop()
            except Exception:  # noqa: BLE001
                pass

    # ---- publishing ------------------------------------------------------

    def publish_tile(self, rec: dict, source_kind: str) -> Optional[str]:
        """Forward one graded tile. `rec` is tile_record_to_dict(record).
        Returns the minted tile id, or None when nothing was sent (not a live
        camera, or the broker refused)."""
        if source_kind != "camera":
            return None
        with self._lock:
            self._n += 1
            tile_id = make_tile_id(self.run_id, self._n)
        payload = build_camera_payload(tile_id, self.run_id, rec, self._clock())
        try:
            ok = bool(self._bus.publish(tile_topic(tile_id, "camera/result"), payload, qos=1))
        except Exception:  # noqa: BLE001
            log.exception("could not publish tile %s", tile_id)
            ok = False
        if ok:
            self.published += 1
            return tile_id
        self.dropped += 1
        log.error("tile %s was graded but NOT delivered to the station (broker unreachable)", tile_id)
        return None

    def _heartbeat_loop(self) -> None:
        while not self._halt.is_set():
            try:
                body = dict(self._status_source() if self._status_source else {})
                body.setdefault("state", "online")
                body.update(run_id=self.run_id, ts=self._clock(), published=self.published, dropped=self.dropped)
                self._bus.publish(TOPIC_STATUS, body, qos=0, retain=True)
            except Exception:  # noqa: BLE001
                log.exception("camera heartbeat failed")
            self._halt.wait(self._heartbeat_s)

    @property
    def connected(self) -> bool:
        return bool(getattr(self._bus, "connected", False))

    def status(self) -> dict:
        return {
            "connected": self.connected,
            "run_id": self.run_id,
            "published": self.published,
            "dropped": self.dropped,
        }
