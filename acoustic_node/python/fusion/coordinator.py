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

from fusion.final_grade import FinalDecision, decide_timeout, fuse_final
from fusion.grades import FINAL_GRADES, HOLD
from fusion.ledger import TileEntry, TileLedger
from fusion.stats import summarize_transits
from fusion.store import TileStore
from fusion.transport import (
    ACK_FILTER,
    CAMERA_RESULT_FILTER,
    TOPIC_ALARM,
    TOPIC_CAMERA_STATUS,
    TOPIC_CMD,
    TOPIC_NANO_STATUS,
    TOPIC_STATUS,
    TOPIC_TAP,
    Bus,
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
        self._hold_all = False

        self._require_ack = bool(unoq_cfg.get("require_ack", True))
        self._ack_timeout = float(unoq_cfg.get("ack_timeout_s", 2.0))
        self._retries = int(unoq_cfg.get("retries", 3))
        self._max_outbox = int(unoq_cfg.get("max_outbox", 50))

        self._camera_stale_s = float((cfg.get("health") or {}).get("camera_stale_s", 6.0))
        nano_cfg = cfg.get("nano") or {}
        # Until the Nano exists there is nothing to be stale: not required, shown
        # as a warning only. Once it is wired, set station.nano.required: true so
        # a dead serial link becomes a FAULT instead of a silent loss of taps.
        self._nano_required = bool(nano_cfg.get("required", False))
        self._nano_stale_s = float(nano_cfg.get("stale_s", 3.0))
        self._nano_status: Optional[dict] = None
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
        self._bus.subscribe(TOPIC_CAMERA_STATUS, self._on_camera_status)
        self._bus.subscribe(TOPIC_CMD, self._on_cmd)
        self._bus.subscribe(TOPIC_TAP, self._on_tap_topic)
        self._bus.subscribe(TOPIC_NANO_STATUS, self._on_nano_status)

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

    def clear_alarms(self) -> None:
        with self._lock:
            # Acknowledges the banner only. A fault that is still true (link
            # down, mic dead) keeps showing through `health` and the state.
            self._alarms = deque((a for a in self._alarms if a["level"] != "alarm"), maxlen=60)

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
        self._enqueue(entry.tile_id, payload, now)
        self._pump(now)

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
                if not self._outbox:
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
        with self._lock:
            self._last_ack_ts = now
            self._store.upsert(tile_id, now, delivery="acked", ack_ts=now)
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
            if now - self._last_heartbeat >= self._heartbeat_s:
                self._last_heartbeat = now
                snap = self._snapshot_locked(now, recent=0)
                self._bus.publish(
                    TOPIC_STATUS,
                    {k: snap[k] for k in ("now", "state", "state_detail", "health", "awaiting_count", "settings", "tally", "counters")},
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
        if not self._require_ack:
            h["unoq"] = {"level": WARN, "detail": "acknowledgement not required (bench mode)"}
        elif self._link_down:
            h["unoq"] = {"level": FAIL, "detail": "no acknowledgement"}
        elif self._inflight is not None:
            h["unoq"] = {"level": WARN, "detail": f"waiting for ack of {self._inflight['tile_id']}"}
        elif self._last_ack_ts is None:
            h["unoq"] = {"level": WARN, "detail": "nothing acknowledged yet"}
        else:
            h["unoq"] = {"level": OK, "detail": f"last ack {now - self._last_ack_ts:.0f} s ago"}
        return h

    def _snapshot_locked(self, now: float, recent: int = 15) -> dict:
        health = self._health(now)
        awaiting = self.ledger.awaiting
        failing = [k for k, v in health.items() if v["level"] == FAIL]
        if failing:
            state, detail = "fault", ", ".join(f"{k}: {health[k]['detail']}" for k in failing)
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
            "settings": {
                "vote_enabled": self._vote_enabled,
                "hold_all": self._hold_all,
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
