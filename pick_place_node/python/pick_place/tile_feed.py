"""The pick-and-place node's side of the station contract: receive each tile's
final grade from the Pi, queue it in physical order, acknowledge it.

Contract (documents/programming/station_coordination.md section 4):

- subscribe `tile/+/final` on the Pi's broker;
- publish `tile/<id>/ack` once the tile has been ACCEPTED into the queue;
- **de-duplicate on tile_id** — the Pi resends the identical message (higher
  `attempt`) until it is acknowledged, so the same tile arrives more than once;
- finals arrive in the order tiles were decided (the Pi sends one at a time and
  waits for the ack), and that order is the order tiles reach the pickup
  position: the queue is FIFO and nothing here re-sorts it;
- `Hold` is not "skip": the tile is on the belt and goes to the manual bin.

`FinalQueue` is pure (no network, no clock but the one passed in) and is what
the gantry sequencer will consume; `FeedClient` is the thin MQTT wrapper. This
node does not import the fusion package (separate node, possibly a different
machine); `tests/test_pick_place_tile_feed.py` runs both against each other.

Back-pressure: when the queue is full the final is NOT acknowledged. The Pi
keeps retrying and, if it stays unacknowledged, raises its no-ack alarm — an
unmistakable fault, where silently dropping a tile's instruction would put
every later tile one place out of step.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

log = logging.getLogger(__name__)

GRADES = ("Grade A", "Grade B", "Grade C", "Reject", "Hold")

QUEUED, PICKING, DONE, FAILED = "queued", "picking", "done", "failed"


@dataclass
class PickJob:
    tile_id: str
    grade: str
    destination: str
    attempt: int
    received_ts: float
    reasons: list = field(default_factory=list)
    state: str = QUEUED


@dataclass
class AcceptResult:
    status: str  # "ok" | "duplicate" | "rejected" | "full"
    reason: Optional[str] = None
    job: Optional[PickJob] = None

    @property
    def should_ack(self) -> bool:
        """ok / duplicate: the Pi has it, stop retrying. rejected: acknowledge
        with status 'rejected' so its queue is not stuck behind a message that
        can never be accepted. full: do NOT ack (back-pressure)."""
        return self.status != "full"


class FinalQueue:
    def __init__(self, max_len: int = 200, remember: int = 2000):
        self._max_len = max_len
        self._queue: deque = deque()
        self._seen: "OrderedDict[str, None]" = OrderedDict()
        self._remember = remember
        self._active: Optional[PickJob] = None
        self._lock = threading.Lock()
        self.counters = {"accepted": 0, "duplicates": 0, "rejected": 0, "full": 0, "done": 0, "failed": 0}

    def accept(self, payload: dict, now: float) -> AcceptResult:
        tile_id = payload.get("tile_id") if isinstance(payload, dict) else None
        grade = payload.get("final_grade") if isinstance(payload, dict) else None
        dest = payload.get("destination") if isinstance(payload, dict) else None
        with self._lock:
            if not isinstance(tile_id, str) or not tile_id:
                self.counters["rejected"] += 1
                return AcceptResult("rejected", "no tile_id")
            if tile_id in self._seen:
                self.counters["duplicates"] += 1
                return AcceptResult("duplicate")
            if grade not in GRADES or not isinstance(dest, str) or not dest:
                self.counters["rejected"] += 1
                # Remember it: the Pi will not resend a rejected message, and a
                # repeat of the same bad one must not be re-counted as new.
                self._remember_id(tile_id)
                return AcceptResult("rejected", f"unusable final_grade/destination ({grade!r}, {dest!r})")
            if len(self._queue) >= self._max_len:
                self.counters["full"] += 1
                return AcceptResult("full", "queue is full")
            job = PickJob(
                tile_id=tile_id,
                grade=grade,
                destination=dest,
                attempt=int(payload.get("attempt") or 1),
                received_ts=now,
                reasons=list(payload.get("reasons") or []),
            )
            self._queue.append(job)
            self._remember_id(tile_id)
            self.counters["accepted"] += 1
            return AcceptResult("ok", job=job)

    def _remember_id(self, tile_id: str) -> None:
        self._seen[tile_id] = None
        while len(self._seen) > self._remember:
            self._seen.popitem(last=False)

    # ---- consumed by the gantry sequencer ---------------------------------

    def start_next(self) -> Optional[PickJob]:
        """Take the oldest queued tile for picking (None while one is already
        being handled or nothing is queued)."""
        with self._lock:
            if self._active is not None or not self._queue:
                return None
            self._active = self._queue.popleft()
            self._active.state = PICKING
            return self._active

    def finish(self, tile_id: str, ok: bool = True) -> None:
        with self._lock:
            job = self._active
            if job is None or job.tile_id != tile_id:
                raise ValueError(f"{tile_id} is not the tile being picked")
            job.state = DONE if ok else FAILED
            self.counters["done" if ok else "failed"] += 1
            self._active = None

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "depth": len(self._queue),
                "active": asdict(self._active) if self._active else None,
                "queued": [asdict(j) for j in list(self._queue)[:20]],
                "counters": dict(self.counters),
            }


class FeedClient:
    """Wires a FinalQueue to a bus: every `tile/<id>/final` is accepted and
    acknowledged. `bus` needs subscribe(filter, cb(topic, payload_bytes)) and
    publish(topic, payload, qos=1, retain=False)."""

    def __init__(
        self,
        bus,
        queue: Optional[FinalQueue] = None,
        clock: Callable[[], float] = time.time,
        on_job: Optional[Callable[[PickJob], None]] = None,
    ):
        self._bus, self.queue, self._clock, self._on_job = bus, queue or FinalQueue(), clock, on_job
        self.last_message_ts: Optional[float] = None

    def start(self) -> None:
        self._bus.subscribe("tile/+/final", self._on_final)
        self._bus.subscribe("station/diag/ping", self._on_diag_ping)

    def _on_diag_ping(self, topic: str, payload: bytes) -> None:
        """Diagnostic answer. Deliberately NOT a final: a diagnostic must never
        be able to create a pick job."""
        try:
            probe = json.loads(payload) if payload else {}
        except ValueError:
            probe = {}
        snap = self.queue.snapshot()
        self._bus.publish(
            "station/diag/pong/gantry",
            {"id": probe.get("id") if isinstance(probe, dict) else None, "node": "gantry", "ts": self._clock(),
             "ok": True,
             "detail": {"queue_depth": snap["depth"], "active": bool(snap["active"]),
                        "counters": snap["counters"], "last_final_ts": self.last_message_ts}},
            qos=0,
        )


    def _on_final(self, topic: str, payload: bytes) -> None:
        now = self._clock()
        self.last_message_ts = now
        try:
            data = json.loads(payload)
        except ValueError:
            data = None
        # The tile id in the topic is the fallback for the ack of an unreadable body.
        topic_id = topic.split("/")[1] if topic.count("/") >= 2 else None
        result = self.queue.accept(data if isinstance(data, dict) else {}, now)
        if result.status == "full":
            log.warning("pick queue full; NOT acknowledging %s (the Pi will retry)", topic_id)
            return
        ack_id = (data or {}).get("tile_id") if isinstance(data, dict) else None
        ack_id = ack_id or topic_id
        if ack_id:
            body = {"status": result.status, "queue_depth": self.queue.snapshot()["depth"]}
            if result.reason:
                body["reason"] = result.reason
            self._bus.publish(f"tile/{ack_id}/ack", body, qos=1)
        if result.status == "ok" and self._on_job is not None:
            try:
                self._on_job(result.job)
            except Exception:  # noqa: BLE001 - a sequencer bug must not stop acknowledging
                log.exception("on_job callback failed")


class PahoFeedBus:
    """Minimal paho-mqtt bus (1.x and 2.x), auto-reconnect, resubscribes on
    every (re)connect. paho is imported lazily."""

    def __init__(self, host: str, port: int = 1883, client_id: str = "tile-gantry"):
        self._host, self._port, self._client_id = host, port, client_id
        self._subs: list = []
        self._client = None
        self.connected = False

    def subscribe(self, topic_filter: str, callback) -> None:
        self._subs.append((topic_filter, callback))
        if self._client is not None and self.connected:
            self._client.subscribe(topic_filter, qos=1)

    def start(self) -> None:
        import paho.mqtt.client as mqtt

        if hasattr(mqtt, "CallbackAPIVersion"):
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self._client_id)
        else:
            c = mqtt.Client(client_id=self._client_id)
        c.reconnect_delay_set(min_delay=1, max_delay=10)

        def on_connect(cl, userdata, *a):
            self.connected = True
            for f, _ in self._subs:
                cl.subscribe(f, qos=1)

        def on_disconnect(cl, userdata, *a):
            self.connected = False

        def on_message(cl, userdata, msg):
            parts = msg.topic.split("/")
            for f, cb in self._subs:
                fp = f.split("/")
                if len(fp) == len(parts) and all(x in ("+", y) for x, y in zip(fp, parts)):
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
        self.connected = False

    def publish(self, topic: str, payload, qos: int = 1, retain: bool = False) -> bool:
        c = self._client
        if c is None:
            return False
        data = payload if isinstance(payload, (bytes, str)) else json.dumps(payload, default=str)
        return c.publish(topic, data, qos=qos, retain=retain).rc == 0


def main(argv: Optional[list[str]] = None) -> None:
    """Bench tool: print every final the Pi sends and acknowledge it, optionally
    pretending to pick each tile. Use it to prove the contract end to end before
    the real gantry sequencer exists."""
    import argparse

    p = argparse.ArgumentParser(description="Receive tile finals from the Pi station and acknowledge them.")
    p.add_argument("--host", default="192.168.1.11", help="the Pi's MQTT broker")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--auto-pick", type=float, default=None, metavar="SECONDS",
                   help="pretend each pick takes this long (otherwise jobs just queue)")
    args = p.parse_args(argv)

    bus = PahoFeedBus(args.host, args.port)
    feed = FeedClient(bus, on_job=lambda j: print(f"{time.strftime('%H:%M:%S')}  {j.tile_id}  {j.grade:8s} -> {j.destination}"
                                                  f"  (attempt {j.attempt})"))
    feed.start()
    bus.start()
    print(f"Listening for finals on {args.host}:{args.port}. Ctrl+C to stop.")
    try:
        while True:
            if args.auto_pick is not None:
                job = feed.queue.start_next()
                if job is not None:
                    time.sleep(args.auto_pick)
                    feed.queue.finish(job.tile_id, ok=True)
                    continue
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        bus.stop()
        print(feed.queue.snapshot()["counters"])


if __name__ == "__main__":
    main()
