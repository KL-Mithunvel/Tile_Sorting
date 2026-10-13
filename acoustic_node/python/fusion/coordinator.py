"""The station's brain: bus <-> ledger <-> final grade <-> store <-> UNO Q.

Flow for one tile:

    camera publishes tile/<id>/camera/result
        -> ledger: AWAITING_TAP (stored)
    a tap is heard (on_tap)
        -> bound to the oldest waiting tile, sound result published, final
           grade computed, stored, queued for the UNO Q
    ... or no tap within ledger.max_wait_s
        -> final = Hold (or Reject if the camera already rejected it)
    final published as tile/<id>/final; the UNO Q answers tile/<id>/ack

Delivery to the UNO Q is stop-and-wait: one final in flight at a time, in the
physical order the tiles were decided, retried every `ack_timeout_s`. After
`retries` unanswered attempts the link is reported DOWN (alarm, station state
FAULT) and the rest queue behind it — a tile is never silently lost, and the
UNO Q is never handed tile 5 before tile 4. The UNO Q must de-duplicate on
tile_id (a retry resends the identical message).

The master PC is never required: nothing here waits for it. It may observe
every topic and send station/cmd.

Thread safety: called from the MQTT thread (camera results, acks, commands),
the audio-analysis thread (on_tap), a tick thread and Flask request threads,
so every public method takes one re-entrant lock. The clock is injected.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from typing import Callable, Optional

from fusion.clock_skew import SkewTracker
from fusion.final_grade import FinalDecision, decide_timeout, fuse_final
from fusion.grades import FINAL_GRADES, HOLD
from fusion.ledger import TileEntry, TileLedger
from fusion.stats import summarize_transits
from fusion.store import TileStore
from fusion.transport import (
    ACK_FILTER,
    CAMERA_RESULT_FILTER,
    PLACED_FILTER,
    TOPIC_ALARM,
    TOPIC_BELT,
    TOPIC_CAMERA_STATUS,
    TOPIC_CMD,
    TOPIC_DIAG_PING,
    TOPIC_GANTRY_STATUS,
    TOPIC_NANO_STATUS,
    TOPIC_STATUS,
    TOPIC_STRIKE,
    TOPIC_TAP,
    Bus,
    diag_pong_topic,
    tile_id_from_topic,
    tile_topic,
)

log = logging.getLogger(__name__)

OK, WARN, FAIL = "ok", "warn", "fail"


class StationCoordinator:
    _TAP_EVENT_MAX_AGE_S = 5.0

    def __init__(
        self,
        cfg: dict,
        bus: Bus,
        store: TileStore,
        clock: Callable[[], float] = time.time,
        on_external_tap: Optional[Callable[[], None]] = None,
    ):
        ledger_cfg = cfg.get("ledger") or {}
        final_cfg = cfg.get("final") or {}
        unoq_cfg = cfg.get("unoq") or {}

        self._bus, self._store, self._clock = bus, store, clock
        self._lock = threading.RLock()
        self._on_external_tap = on_external_tap

        self.ledger = TileLedger(
            max_wait_s=float(ledger_cfg.get("max_wait_s", 30.0)),
            min_transit_s=float(ledger_cfg.get("min_transit_s", 0.0)),
        )
        self._vote_enabled = bool(final_cfg.get("vote_enabled", False))
        self._min_conf = float(final_cfg.get("min_confidence", 0.6))
        self._bins = dict(final_cfg.get("bins") or {})
        # A Hold tile is NOT sent to the gantry (it has no location for it, and the gantry
        # picks whatever is at the pickup point, so a tile it does not know about would be
        # picked into the NEXT tile's bin). `hold_to_gantry: true` restores the old behaviour.
        self._hold_to_gantry = bool(final_cfg.get("hold_to_gantry", False))
        # ...and so a Hold tile must not reach the pickup while the feed is running: pause the
        # feed until a person has taken it off the belt and resumes it.
        self._hold_pauses_feed = bool(final_cfg.get("hold_pauses_feed", True))
        self._needs_person: list[str] = []
        self._sticky: dict = {}
        self._hold_all = False

        self._require_ack = bool(unoq_cfg.get("require_ack", True))
        self._ack_timeout = float(unoq_cfg.get("ack_timeout_s", 2.0))
        self._retries = int(unoq_cfg.get("retries", 3))
        self._max_outbox = int(unoq_cfg.get("max_outbox", 50))
        # A tile the UNO Q acknowledged should be reported placed (or failed) well
        # inside this; 0 turns the check off (bench runs without a real gantry).
        self._placed_timeout = float(unoq_cfg.get("placed_timeout_s", 180.0))
        gantry_cfg = cfg.get("gantry") or {}
        self._gantry_required = bool(gantry_cfg.get("required", False))
        self._gantry_stale_s = float(gantry_cfg.get("stale_s", 6.0))
        self._gantry_status: Optional[dict] = None
        self._gantry_status_ts: Optional[float] = None
        self._awaiting_placement: dict = {}  # tile_id -> ack time

        self._camera_stale_s = float((cfg.get("health") or {}).get("camera_stale_s", 6.0))
        # Peer clock disagreement, from the timestamps in their heartbeats. Shown
        # as a warning only: nothing is decided from cross-node time.
        self._skew = SkewTracker(warn_s=float((cfg.get("health") or {}).get("clock_warn_s", 1.0)))
        nano_cfg = cfg.get("nano") or {}
        # Until the Nano exists there is nothing to be stale: not required, shown
        # as a warning only. Once it is wired, set station.nano.required: true so
        # a dead serial link becomes a FAULT instead of a silent loss of taps.
        self._nano_required = bool(nano_cfg.get("required", False))
        self._nano_stale_s = float(nano_cfg.get("stale_s", 3.0))
        self._nano_status: Optional[dict] = None
        # Whole-line soft stop (see set_line). `_line_saved` is what stop overrode.
        self._line_stopped = False
        self._line_saved: dict = {}
        # Feed gate to the UNO Q: while paused no NEW final is released from the
        # outbox. An unacknowledged one in flight still completes and is retried.
        self._feed_paused = False
        self._nano_status_ts: Optional[float] = None
        # Strike events (station/tap) waiting to be paired with their clip.
        self._tap_events: deque = deque(maxlen=16)
        self._unknown_n = 0
        # Camera -> tap times, for choosing ledger.max_wait_s from data.
        self._transits: deque = deque(store.recent_transits(200), maxlen=200)
        self._heartbeat_s = float(cfg.get("heartbeat_s", 2.0))
        self._commands_enabled = bool(cfg.get("commands_enabled", True))

        self._outbox: deque = deque()
        self._inflight: Optional[dict] = None
        self._link_down = False
        self._last_ack_ts: Optional[float] = None

        self._camera_status: Optional[dict] = None
        self._camera_status_ts: Optional[float] = None
        self._extra_health: dict[str, dict] = {}  # set by the service (the mic)

        self._alarms: deque = deque(maxlen=60)
        self._tally: dict[str, int] = {}
        self._counters = {"tiles": 0, "taps": 0, "orphan_taps": 0, "timeouts": 0, "bad_messages": 0}
        self._last_final: Optional[dict] = None
        self._last_heartbeat = 0.0
        self._started_ts = clock()

    # ---- wiring ----------------------------------------------------------

    def start(self) -> None:
        self._bus.subscribe(CAMERA_RESULT_FILTER, self._on_camera_result)
        self._bus.subscribe(ACK_FILTER, self._on_ack)
        self._bus.subscribe(PLACED_FILTER, self._on_placed)
        self._bus.subscribe(TOPIC_GANTRY_STATUS, self._on_gantry_status)
        self._bus.subscribe(TOPIC_CAMERA_STATUS, self._on_camera_status)
        self._bus.subscribe(TOPIC_CMD, self._on_cmd)
        self._bus.subscribe(TOPIC_TAP, self._on_tap_topic)
        self._bus.subscribe(TOPIC_NANO_STATUS, self._on_nano_status)
        self._bus.subscribe(TOPIC_DIAG_PING, self._on_diag_ping)

    # ---- settings the operator / master may change -------------------------

    @property
    def vote_enabled(self) -> bool:
        return self._vote_enabled

    def set_vote_enabled(self, enabled: bool, source: str = "operator") -> None:
        with self._lock:
            self._vote_enabled = bool(enabled)
            self._event(f"sound vote {'ON' if enabled else 'OFF'} ({source})")

    def set_hold_all(self, enabled: bool, source: str = "operator") -> None:
        with self._lock:
            self._hold_all = bool(enabled)
            self._event(f"hold-all {'ON' if enabled else 'OFF'} ({source})")

    @property
    def line_stopped(self) -> bool:
        return self._line_stopped

    @property
    def feed_paused(self) -> bool:
        return self._feed_paused

    def set_feed_paused(self, paused: bool, source: str = "operator") -> None:
        """Stop (or resume) handing NEW finals to the gantry.

        The gantry is not frozen: it finishes the tile it is on and drains the
        ones it has already accepted, then idles with nothing in the cup. Held
        finals wait in the outbox here and are delivered, in order, on resume.
        A pause that lasts long enough to fill the outbox drops the oldest and
        raises `outbox_overflow` - the health row says so before that happens."""
        with self._lock:
            if bool(paused) == self._feed_paused:
                return
            self._feed_paused = bool(paused)
            self._event(f"gantry feed {'PAUSED' if paused else 'RESUMED'} ({source})")
            if not paused:
                self._confirm_needs_person(source)
                self._pump(self._clock())

    def set_line(self, running: bool, belt_percent: Optional[float] = None, source: str = "operator") -> bool:
        """Stop or start the whole line from one place.

        STOP: hold-all ON (a tile already in flight is held for a person, never
        sorted automatically), belt to 0, striker inhibited - and remember what
        it overrode. START: restore it (belt to `belt_percent`, else the speed
        it had before the stop; striker allowed; hold-all back to how it was).
        It refuses to start the belt at a speed it does not know rather than
        guess one. Returns whether the request was carried out.

        This is a SOFT stop over the network - not an e-stop (that is wired
        separately) - and it cannot stop the gantry: it only changes what is
        sent to it (held tiles). Tiles already on the belt are not removed."""
        with self._lock:
            if not running:
                if not self._line_stopped:
                    nano = self._nano_status or {}
                    pct = nano.get("belt_pct")
                    self._line_saved = {
                        "hold_all": self._hold_all,
                        "belt_pct": pct if isinstance(pct, (int, float)) and pct > 0 else None,
                    }
                    self._line_stopped = True
                    self._event(f"LINE STOPPED ({source}): hold-all on, belt 0, striker inhibited, gantry feed paused")
                self._hold_all = True
                self._feed_paused = True
                self._bus.publish(TOPIC_BELT, {"percent": 0}, qos=1)
                self._bus.publish(TOPIC_STRIKE, {"enabled": False}, qos=1)
                return True
            pct = belt_percent if belt_percent is not None else self._line_saved.get("belt_pct")
            if pct is None or isinstance(pct, bool) or not 0 < pct <= 100:
                self._alarm(
                    "line_start",
                    "line NOT started: no belt speed to restore - set one from the master and start again",
                    None,
                )
                return False
            self._hold_all = bool(self._line_saved.get("hold_all", False))
            self._line_stopped = False
            self._line_saved = {}
            self._feed_paused = False
            self._confirm_needs_person(source)
            self._event(f"LINE STARTED ({source}): belt {pct:g} %, striker allowed, gantry feed resumed")
            # Striker first, belt last: nothing moves until the line can use it.
            self._bus.publish(TOPIC_STRIKE, {"enabled": True}, qos=1)
            self._bus.publish(TOPIC_BELT, {"percent": pct}, qos=1)
            self._pump(self._clock())      # release whatever was held back, now that the line runs
            return True

    def clear_alarms(self) -> None:
        with self._lock:
            # Acknowledges the banner only. A fault that is still true (link
            # down, mic dead) keeps showing through `health` and the state.
            self._alarms = deque((a for a in self._alarms if a["level"] != "alarm"), maxlen=60)
            self._sticky.clear()

    def set_health(self, name: str, level: str, detail: str = "") -> None:
        with self._lock:
            self._extra_health[name] = {"level": level, "detail": detail}

    # ---- inputs ----------------------------------------------------------

    def _on_camera_result(self, topic: str, payload: bytes) -> None:
        tile_id = tile_id_from_topic(topic)
        if tile_id is None:
            return
        now = self._clock()
        try:
            data = json.loads(payload)
            if not isinstance(data, dict):
                raise ValueError("payload is not a JSON object")
            grade = data.get("grade")
            seq = data.get("seq")
        except ValueError as exc:
            data, grade, seq = {}, None, None
            with self._lock:
                self._counters["bad_messages"] += 1
                self._alarm("bad_message", f"unreadable camera result for {tile_id}: {exc}", tile_id)
        with self._lock:
            # Even an unreadable result keeps its place in the order: the tile
            # is physically there and will be tapped. Its grade is None, so it
            # ends up Hold.
            entry, is_new = self.ledger.add_camera(tile_id, grade, data, now, seq=seq)
            if not is_new:
                return
            self._counters["tiles"] += 1
            self._store.upsert(
                tile_id, now, seq=seq, state="awaiting_tap", camera_grade=grade, camera_json=data,
            )

    def pop_tap_event(self) -> Optional[dict]:
        """The oldest strike event not yet paired with a clip (None when the
        clip did not come from an external event, e.g. rms trigger mode)."""
        now = self._clock()
        with self._lock:
            # An event whose clip never came (capture refused, cooldown) must
            # not be paired with some later, unrelated clip.
            while self._tap_events and now - self._tap_events[0].get("_rx", now) > self._TAP_EVENT_MAX_AGE_S:
                self._tap_events.popleft()
            return self._tap_events.popleft() if self._tap_events else None

    def on_tap(
        self, acoustic: dict, now: Optional[float] = None, tile_present: Optional[bool] = None
    ) -> Optional[str]:
        """A tap was heard and analysed. `acoustic` carries at least `valid`,
        `grade` (or None) and `confidence`. `tile_present` is the striker
        station's own ToF reading from the strike event: False = it fired with
        nothing under it, None = unknown (rms trigger / simulated). Returns the
        tile id it was bound to, or None (orphan / empty strike)."""
        now = self._clock() if now is None else now
        with self._lock:
            self._counters["taps"] += 1
            if tile_present is False:
                # The striker fired on an empty position. That cannot be any
                # tile's tap, so it must not use up a slot.
                self._counters["empty_strikes"] = self._counters.get("empty_strikes", 0) + 1
                self._alarm("empty_strike", "the striker fired with no tile under it", None)
                return None
            entry = self.ledger.bind_tap(now)
            if entry is None:
                self._counters["orphan_taps"] += 1
                if tile_present:
                    # A tile IS under the striker and the camera never reported
                    # it (missed detection). It will travel on to the UNO Q, so
                    # it needs a final or the UNO Q's queue falls out of step.
                    return self._final_for_unreported_tile(acoustic, now)
                self._alarm("orphan_tap", "a tap was heard but no tile was waiting for one", None)
                return None
            entry.acoustic = acoustic
            if acoustic.get("valid"):
                # Only a real strike measures how long the tile took to arrive.
                transit = max(0.0, now - entry.created)
                self._transits.appendleft(transit)
                self._store.upsert(entry.tile_id, now, transit_s=transit)
            self._publish(
                tile_topic(entry.tile_id, "acoustic/result"),
                {
                    "tile_id": entry.tile_id,
                    "station": "acoustic",
                    "status": "done" if acoustic.get("valid") else "invalid",
                    "acoustic_grade": acoustic.get("grade"),
                    "confidence": acoustic.get("confidence"),
                    "peak_frequency_hz": acoustic.get("peak_frequency_hz"),
                    "decay_t20_s": acoustic.get("decay_t20_s"),
                    "ts": now,
                },
            )
            decision = fuse_final(
                entry.camera_grade,
                acoustic.get("grade"),
                acoustic.get("confidence"),
                vote_enabled=self._vote_enabled,
                min_confidence=self._min_conf,
                acoustic_valid=bool(acoustic.get("valid")),
                bins=self._bins,
                hold_all=self._hold_all,
            )
            self._finalize(entry, decision, now, timed_out=False)
            return entry.tile_id

    def _final_for_unreported_tile(self, acoustic: dict, now: float) -> str:
        self._unknown_n += 1
        tile_id = f"UNKNOWN-{int(self._started_ts)}-{self._unknown_n:04d}"
        self._counters["missed_camera"] = self._counters.get("missed_camera", 0) + 1
        self._alarm(
            "missed_camera",
            f"a tile was under the striker but the camera reported none; sent {tile_id} as Hold",
            tile_id,
        )
        entry = TileEntry(tile_id=tile_id, seq=None, camera_grade=None, camera={}, created=now)
        entry.acoustic = acoustic
        decision = FinalDecision(
            HOLD,
            self._bins.get(HOLD, "manual"),
            False,
            ("no camera result for this tile (missed detection), held for a person",),
        )
        self._store.upsert(tile_id, now, state="awaiting_tap", camera_grade=None, camera_json={})
        self._finalize(entry, decision, now, timed_out=False)
        return tile_id

    def note_ignored_tap(self, reason: str) -> None:
        """A sound that triggered a capture but was not a plausible strike. It
        is counted and shown, but uses up no tile's tap slot."""
        with self._lock:
            self._counters["ignored_taps"] = self._counters.get("ignored_taps", 0) + 1
            self._event(f"ignored a sound that was not a strike: {reason}")

    def _finalize(self, entry: TileEntry, decision: FinalDecision, now: float, timed_out: bool) -> None:
        payload = {
            "tile_id": entry.tile_id,
            "seq": entry.seq,
            "station": "decision",
            "final_grade": decision.grade,
            "destination": decision.destination,
            "camera_grade": entry.camera_grade,
            "acoustic_grade": (entry.acoustic or {}).get("grade"),
            "acoustic_counted": decision.acoustic_counted,
            "timed_out": timed_out,
            "reasons": list(decision.reasons),
            "ts": now,
        }
        self._store.upsert(
            entry.tile_id,
            now,
            state="final",
            acoustic_grade=(entry.acoustic or {}).get("grade"),
            acoustic_json=entry.acoustic,
            acoustic_counted=decision.acoustic_counted,
            final_grade=decision.grade,
            destination=decision.destination,
            reasons_json=list(decision.reasons),
            delivery="pending",
        )
        self._tally[decision.grade] = self._tally.get(decision.grade, 0) + 1
        self._last_final = {"tile_id": entry.tile_id, "grade": decision.grade, "destination": decision.destination, "ts": now}
        if decision.grade == HOLD and not self._hold_to_gantry:
            self._hold_for_a_person(entry.tile_id, payload, now)
            return
        self._enqueue(entry.tile_id, payload, now)
        self._pump(now)

    # ---- Hold tiles: a person's job, never the gantry's ---------------------

    def _hold_for_a_person(self, tile_id: str, payload: dict, now: float) -> None:
        self._store.upsert(tile_id, now, delivery="needs_person")
        self._needs_person.append(tile_id)
        # Visible to the master and the Pi's own screens - but NOT on tile/<id>/final, which the
        # UNO Q subscribes to and would turn into a pick job it cannot do correctly.
        self._bus.publish(tile_topic(tile_id, "needs_person"), dict(payload, needs_person=True), qos=1)
        if self._hold_pauses_feed and not self._feed_paused:
            self._feed_paused = True
            self._event(f"gantry feed PAUSED (hold tile {tile_id}): take it off the belt, then Resume feed")
        self._sticky_alarm(
            "needs_person",
            "{n} hold tile(s) need a person: take them off the belt, then Resume feed",
            tile_id,
        )

    def _confirm_needs_person(self, source: str) -> None:
        """Resuming the feed is the operator's confirmation that the hold tiles are gone."""
        if not self._needs_person:
            return
        n = len(self._needs_person)
        self._needs_person.clear()
        cur = self._sticky.pop("needs_person", None)
        if cur and cur.get("item") is not None:
            try:
                self._alarms.remove(cur["item"])
            except ValueError:
                pass
        self._event(f"{n} hold tile(s) confirmed taken off the belt ({source})")

    def _sticky_alarm(self, kind: str, fmt: str, tile_id: Optional[str], republish_s: float = 10.0) -> None:
        """One alarm per cause: the first occurrence is published at once, repeats only raise
        its count and re-publish it (updated) at most every `republish_s`."""
        now = self._clock()
        cur = self._sticky.setdefault(kind, {"count": 0, "pub": None, "item": None})
        cur["count"] += 1
        msg = fmt.format(n=cur["count"])
        if cur["item"] is None:
            item = {"ts": now, "level": "alarm", "kind": kind, "message": msg, "tile_id": tile_id,
                    "count": cur["count"]}
            cur["item"], cur["pub"] = item, now
            self._alarms.appendleft(item)
            log.warning("ALARM %s: %s", kind, msg)
            self._bus.publish(TOPIC_ALARM, dict(item), qos=1)
            return
        item = cur["item"]
        item.update(message=msg, count=cur["count"], tile_id=tile_id)
        if now - cur["pub"] >= republish_s:
            cur["pub"], item["ts"] = now, now
            self._bus.publish(TOPIC_ALARM, dict(item), qos=1)

    # ---- delivery to the UNO Q ------------------------------------------

    def _enqueue(self, tile_id: str, payload: dict, now: float) -> None:
        if len(self._outbox) >= self._max_outbox:
            dropped = self._outbox.popleft()
            self._store.upsert(dropped["tile_id"], now, delivery="dropped")
            self._alarm("outbox_overflow", f"UNO Q queue full, {dropped['tile_id']} was not delivered", dropped["tile_id"])
        self._outbox.append({"tile_id": tile_id, "payload": payload, "attempts": 0, "sent_at": None})

    def _pump(self, now: float) -> None:
        while True:
            if self._inflight is None:
                if not self._outbox or self._feed_paused:
                    return
                self._inflight = self._outbox.popleft()
            item = self._inflight
            due = item["sent_at"] is None or (self._require_ack and now - item["sent_at"] >= self._ack_timeout)
            if not due:
                return
            payload = dict(item["payload"], attempt=item["attempts"] + 1)
            if not self._bus.publish(tile_topic(item["tile_id"], "final"), payload, qos=1):
                item["sent_at"] = None  # broker unreachable: try again next tick
                return
            if self._inflight is not item:
                # The UNO Q answered before publish() returned (an in-process
                # bus delivers synchronously, a fast broker nearly so): the ack
                # handler has already marked it delivered and moved on. Do not
                # overwrite 'acked' with 'sent' or resurrect the item.
                self._store.upsert(item["tile_id"], now, attempts=item["attempts"] + 1)
                continue
            item["attempts"] += 1
            item["sent_at"] = now
            self._store.upsert(item["tile_id"], now, delivery="sent", attempts=item["attempts"])
            if not self._require_ack:
                self._inflight = None  # fire-and-forget: next one straight away
                continue
            if item["attempts"] >= self._retries and not self._link_down:
                self._link_down = True
                self._alarm(
                    "no_ack",
                    f"UNO Q has not acknowledged {item['tile_id']} after {item['attempts']} tries",
                    item["tile_id"],
                )
            return

    def _on_ack(self, topic: str, payload: bytes) -> None:
        tile_id = tile_id_from_topic(topic)
        if tile_id is None:
            return
        now = self._clock()
        try:
            body = json.loads(payload) if payload else {}
        except ValueError:
            body = {}
        status = body.get("status") if isinstance(body, dict) else None
        with self._lock:
            self._last_ack_ts = now
            if status == "rejected":
                # The UNO Q consumed the message but cannot act on it. Release the
                # queue (resending would be refused again) and make it loud: this
                # tile has no pick instruction.
                self._store.upsert(tile_id, now, delivery="rejected", ack_ts=now)
                reason = body.get("reason", "no reason given")
                self._alarm("unoq_rejected", f"UNO Q rejected the final for {tile_id}: {reason}", tile_id)
            else:
                self._store.upsert(tile_id, now, delivery="acked", ack_ts=now)
                if status != "duplicate" and self._placed_timeout > 0 and tile_id not in self._awaiting_placement:
                    self._awaiting_placement[tile_id] = now
                    while len(self._awaiting_placement) > 500:
                        self._awaiting_placement.pop(next(iter(self._awaiting_placement)))
            if self._inflight is not None and self._inflight["tile_id"] == tile_id:
                self._inflight = None
                if self._link_down:
                    self._link_down = False
                    self._event("UNO Q link recovered")
            self._pump(now)

    # ---- time ------------------------------------------------------------

    def tick(self, now: Optional[float] = None) -> None:
        """Call a few times a second: expire tiles that never got a tap, resend
        unacknowledged finals, publish the heartbeat."""
        now = self._clock() if now is None else now
        with self._lock:
            for entry in self.ledger.expire(now):
                self._counters["timeouts"] += 1
                self._alarm(
                    "no_tap_timeout",
                    f"{entry.tile_id}: no tap heard within {self.ledger.max_wait_s:g} s",
                    entry.tile_id,
                )
                decision = decide_timeout(
                    entry.camera_grade, max_wait_s=self.ledger.max_wait_s, bins=self._bins, hold_all=self._hold_all
                )
                self._finalize(entry, decision, now, timed_out=True)
            self._pump(now)
            self._check_placements(now)
            if now - self._last_heartbeat >= self._heartbeat_s:
                self._last_heartbeat = now
                snap = self._snapshot_locked(now, recent=0)
                self._bus.publish(
                    TOPIC_STATUS,
                    {k: snap[k] for k in ("now", "state", "state_detail", "health", "awaiting_count", "outbox_depth", "needs_person", "settings", "tally", "counters")},
                    qos=0,
                    retain=True,
                )

    # ---- other inbound topics ---------------------------------------------

    def _on_camera_status(self, topic: str, payload: bytes) -> None:
        try:
            data = json.loads(payload)
        except ValueError:
            return
        with self._lock:
            self._camera_status = data if isinstance(data, dict) else None
            self._camera_status_ts = self._clock()
            if isinstance(data, dict):
                self._skew.observe("camera", data.get("ts"), self._camera_status_ts)

    def _on_diag_ping(self, topic: str, payload: bytes) -> None:
        try:
            probe = json.loads(payload) if payload else {}
        except ValueError:
            probe = {}
        now = self._clock()
        with self._lock:
            snap = self._snapshot_locked(now, recent=0)
        self._bus.publish(
            diag_pong_topic("station"),
            {
                "id": probe.get("id") if isinstance(probe, dict) else None,
                "node": "station",
                "ts": now,
                "ok": snap["state"] != "fault",
                "detail": {
                    "state": snap["state"],
                    "state_detail": snap["state_detail"],
                    "health": {k: v["level"] for k, v in snap["health"].items()},
                    "awaiting": snap["awaiting_count"],
                    "hold_all": snap["settings"]["hold_all"],
                    "vote_enabled": snap["settings"]["vote_enabled"],
                },
            },
            qos=0,
        )

    def _on_placed(self, topic: str, payload: bytes) -> None:
        """The UNO Q's report on one tile: placed on a stack, or the pick failed."""
        tile_id = tile_id_from_topic(topic)
        if tile_id is None:
            return
        now = self._clock()
        try:
            body = json.loads(payload)
            if not isinstance(body, dict):
                raise ValueError("payload is not a JSON object")
        except ValueError as exc:
            with self._lock:
                self._counters["bad_messages"] += 1
                self._alarm("bad_message", f"unreadable placement report for {tile_id}: {exc}", tile_id)
            return
        outcome = "placed" if body.get("outcome") == "placed" else "failed"
        key = "placed" if outcome == "placed" else "pick_failed"
        with self._lock:
            self._awaiting_placement.pop(tile_id, None)
            self._counters[key] = self._counters.get(key, 0) + 1
            self._store.upsert(
                tile_id, now, placement=outcome, placement_reason=body.get("reason"),
                tile_in_cup=body.get("tile_in_cup"), placed_ts=now,
            )
            # A failed pick is already announced on machine/alarm by the gantry;
            # it is recorded here and shown in the tile's row, not raised twice.
            if outcome == "failed":
                self._event(f"{tile_id}: pick failed ({body.get('reason') or 'no reason given'})")

    def _check_placements(self, now: float) -> None:
        """A tile the UNO Q acknowledged but never reported on is a tile nobody
        knows the fate of. Raised once, then forgotten (the row says 'overdue')."""
        if self._placed_timeout <= 0 or not self._awaiting_placement:
            return
        for tile_id, ack_ts in list(self._awaiting_placement.items()):
            if now - ack_ts < self._placed_timeout:
                continue
            del self._awaiting_placement[tile_id]
            self._counters["overdue"] = self._counters.get("overdue", 0) + 1
            self._store.upsert(tile_id, now, placement="overdue")
            self._alarm(
                "not_placed",
                f"{tile_id} was acknowledged by the UNO Q but not reported placed within "
                f"{self._placed_timeout:g} s",
                tile_id,
            )

    def _on_gantry_status(self, topic: str, payload: bytes) -> None:
        try:
            data = json.loads(payload)
        except ValueError:
            return
        with self._lock:
            self._gantry_status = data if isinstance(data, dict) else None
            self._gantry_status_ts = self._clock()
            if isinstance(data, dict):
                self._skew.observe("gantry", data.get("ts"), self._gantry_status_ts)

    def _on_nano_status(self, topic: str, payload: bytes) -> None:
        try:
            data = json.loads(payload)
        except ValueError:
            return
        with self._lock:
            self._nano_status = data if isinstance(data, dict) else None
            self._nano_status_ts = self._clock()

    def _on_cmd(self, topic: str, payload: bytes) -> None:
        if not self._commands_enabled:
            return
        try:
            cmd = json.loads(payload)
            name = cmd["cmd"]
        except (ValueError, KeyError, TypeError):
            with self._lock:
                self._alarm("bad_message", "unreadable station/cmd", None)
            return
        if name == "vote":
            self.set_vote_enabled(bool(cmd.get("enabled")), source="master")
        elif name == "hold_all":
            self.set_hold_all(bool(cmd.get("enabled")), source="master")
        elif name == "clear_alarms":
            self.clear_alarms()
        elif name == "feed":
            state = cmd.get("state")
            if state not in ("pause", "resume"):
                with self._lock:
                    self._alarm("bad_message", "station/cmd feed needs state pause|resume", None)
            else:
                self.set_feed_paused(state == "pause", source="master")
        elif name == "line":
            state = cmd.get("state")
            pct = cmd.get("belt_percent")
            if state not in ("start", "stop") or (
                pct is not None and (isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 0 < pct <= 100)
            ):
                with self._lock:
                    self._alarm("bad_message", "station/cmd line needs state start|stop and belt_percent in (0,100]", None)
            else:
                self.set_line(state == "start", belt_percent=pct, source="master")
        else:
            with self._lock:
                self._alarm("bad_message", f"unknown station/cmd {name!r}", None)

    def _on_tap_topic(self, topic: str, payload: bytes) -> None:
        """A bridge reports a physical strike. Forwarded to the audio capture
        (its pre-trigger buffer starts the clip); the analysed result then
        arrives through on_tap()."""
        try:
            event = json.loads(payload) if payload else {}
        except ValueError:
            event = {}
        if not isinstance(event, dict):
            event = {}
        event["_rx"] = self._clock()
        with self._lock:
            self._tap_events.append(event)
        if self._on_external_tap is not None:
            try:
                self._on_external_tap()
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._alarm("tap_input", f"station/tap could not start a capture: {exc}", None)

    # ---- reporting -------------------------------------------------------

    def _publish(self, topic: str, payload: dict) -> None:
        self._bus.publish(topic, payload, qos=1)

    def _alarm(self, kind: str, message: str, tile_id: Optional[str]) -> None:
        now = self._clock()
        item = {"ts": now, "level": "alarm", "kind": kind, "message": message, "tile_id": tile_id}
        self._alarms.appendleft(item)
        log.warning("ALARM %s: %s", kind, message)
        self._bus.publish(TOPIC_ALARM, item, qos=1)

    def _event(self, message: str) -> None:
        self._alarms.appendleft({"ts": self._clock(), "level": "info", "kind": "event", "message": message, "tile_id": None})

    def _health(self, now: float) -> dict:
        h: dict[str, dict] = {}
        h["broker"] = (
            {"level": OK, "detail": "connected"} if self._bus.connected else {"level": FAIL, "detail": "not connected"}
        )
        if self._camera_status_ts is None:
            h["camera"] = {"level": FAIL, "detail": "no heartbeat received yet"}
        elif now - self._camera_status_ts > self._camera_stale_s:
            h["camera"] = {"level": FAIL, "detail": f"silent for {now - self._camera_status_ts:.0f} s"}
        else:
            st = self._camera_status or {}
            if st.get("state") == "offline":
                h["camera"] = {"level": FAIL, "detail": "camera service went offline"}
            elif st.get("source_kind") not in (None, "camera"):
                h["camera"] = {"level": WARN, "detail": f"source is {st.get('source_kind')}, results are not live"}
            elif st.get("source_status") not in (None, "running"):
                h["camera"] = {"level": FAIL, "detail": f"source {st.get('source_status')}"}
            else:
                h["camera"] = {"level": OK, "detail": f"{st.get('tile_count', 0)} tiles"}
        h["mic"] = self._extra_health.get("mic", {"level": FAIL, "detail": "not started"})
        miss = FAIL if self._nano_required else WARN
        if self._nano_status_ts is None:
            h["nano"] = {"level": miss, "detail": "no serial bridge heartbeat"}
        elif now - self._nano_status_ts > self._nano_stale_s:
            h["nano"] = {"level": miss, "detail": f"silent for {now - self._nano_status_ts:.0f} s"}
        elif (self._nano_status or {}).get("state") == "offline":
            h["nano"] = {"level": miss, "detail": "serial bridge offline"}
        elif not (self._nano_status or {}).get("link", True):
            h["nano"] = {"level": miss, "detail": "Nano not answering on serial"}
        else:
            h["nano"] = {"level": OK, "detail": f"belt {self._nano_status.get('belt_pct', '?')} %"}
        miss = FAIL if self._gantry_required else WARN
        g = self._gantry_status or {}
        if self._gantry_status_ts is None:
            h["gantry"] = {"level": miss, "detail": "no status from the gantry yet"}
        elif now - self._gantry_status_ts > self._gantry_stale_s:
            h["gantry"] = {"level": miss, "detail": f"silent for {now - self._gantry_status_ts:.0f} s"}
        elif g.get("state") == "offline":
            h["gantry"] = {"level": miss, "detail": "gantry service went offline"}
        elif g.get("level") == "fail":
            why = g.get("alarm") or g.get("fault") or ("MCU link down" if g.get("link") is False else "fault")
            h["gantry"] = {"level": FAIL, "detail": f"needs a person: {why}"}
        elif g.get("level") == "warn":
            why = "paused" if g.get("paused") else ("not homed" if g.get("homed") is False else "check the gantry")
            h["gantry"] = {"level": WARN, "detail": why}
        else:
            h["gantry"] = {"level": OK, "detail": f"{g.get('completed', 0)} placed, queue {g.get('queue_depth', 0)}"}
        if not self._require_ack:
            h["unoq"] = {"level": WARN, "detail": "acknowledgement not required (bench mode)"}
        elif self._link_down:
            h["unoq"] = {"level": FAIL, "detail": "no acknowledgement"}
        elif self._feed_paused and self._needs_person:
            h["unoq"] = {"level": WARN,
                         "detail": f"{len(self._needs_person)} hold tile(s) need a person - take them off the belt, "
                                   f"then Resume feed ({len(self._outbox)} held back)"}
        elif self._feed_paused:
            queued = len(self._outbox)
            if queued >= self._max_outbox:
                h["unoq"] = {"level": FAIL, "detail": f"feed paused and the queue is full ({queued}): tiles are being dropped"}
            else:
                h["unoq"] = {"level": WARN, "detail": f"feed paused by the master, {queued} held back"}
        elif self._inflight is not None:
            h["unoq"] = {"level": WARN, "detail": f"waiting for ack of {self._inflight['tile_id']}"}
        elif self._last_ack_ts is None:
            h["unoq"] = {"level": WARN, "detail": "nothing acknowledged yet"}
        else:
            h["unoq"] = {"level": OK, "detail": f"last ack {now - self._last_ack_ts:.0f} s ago"}
        clock = self._skew.health(now)
        if clock is not None:
            h["clock"] = clock
        return h

    def _snapshot_locked(self, now: float, recent: int = 15) -> dict:
        health = self._health(now)
        awaiting = self.ledger.awaiting
        failing = [k for k, v in health.items() if v["level"] == FAIL]
        if failing:
            state, detail = "fault", ", ".join(f"{k}: {health[k]['detail']}" for k in failing)
        elif self._line_stopped:
            state, detail = "line_stopped", "line stopped (soft stop from the master - not an e-stop)"
        elif self._hold_all:
            state, detail = "hold_all", "every tile is being held for a person"
        elif awaiting:
            state, detail = "awaiting_tap", f"{len(awaiting)} tile(s) waiting for a tap"
        elif self._last_final and now - self._last_final["ts"] < 3.0:
            state, detail = "sent", f"{self._last_final['grade']} -> {self._last_final['destination']}"
        else:
            state, detail = "waiting", "waiting for a tile"
        return {
            "now": now,
            "state": state,
            "state_detail": detail,
            "health": health,
            "awaiting_count": len(awaiting),
            "awaiting": [
                {"tile_id": e.tile_id, "seq": e.seq, "camera_grade": e.camera_grade, "age_s": round(now - e.created, 1)}
                for e in awaiting
            ],
            "tally": dict(self._tally),
            "counters": dict(self._counters),
            "last_final": dict(self._last_final) if self._last_final else None,
            "alarms": list(self._alarms)[:20],
            "outbox_depth": len(self._outbox) + (1 if self._inflight else 0),
            "needs_person": len(self._needs_person),
            "needs_person_tiles": list(self._needs_person)[-10:],
            "gantry": dict(self._gantry_status) if self._gantry_status else None,
            "awaiting_placement": len(self._awaiting_placement),
            "settings": {
                "vote_enabled": self._vote_enabled,
                "hold_all": self._hold_all,
                "line": "stopped" if self._line_stopped else "running",
                "feed": "paused" if self._feed_paused else "running",
                "hold_to_gantry": self._hold_to_gantry,
                "min_confidence": self._min_conf,
                "max_wait_s": self.ledger.max_wait_s,
                "require_ack": self._require_ack,
            },
            "transit": summarize_transits(self._transits),
            "recent": self._store.recent(recent) if recent else [],
            "uptime_s": round(now - self._started_ts, 1),
        }

    def snapshot(self, recent: int = 15) -> dict:
        with self._lock:
            return self._snapshot_locked(self._clock(), recent=recent)


__all__ = ["StationCoordinator", "FINAL_GRADES", "HOLD", "OK", "WARN", "FAIL"]
