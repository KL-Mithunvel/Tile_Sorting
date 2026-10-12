"""StationCoordinator end to end over an in-memory bus: camera result -> tap ->
final -> UNO Q ack, plus every failure path (no tap, orphan tap, no ack,
broker down, commands)."""

import json

import pytest

from fusion.coordinator import FAIL, OK, WARN, StationCoordinator
from fusion.store import TileStore
from fusion.transport import InMemoryBus


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


CFG = {
    "ledger": {"max_wait_s": 10.0, "min_transit_s": 0.0},
    "final": {
        "vote_enabled": True,
        "min_confidence": 0.6,
        "bins": {"Grade A": "bin1", "Grade B": "bin2", "Grade C": "bin3", "Reject": "bin4", "Hold": "manual"},
    },
    "unoq": {"require_ack": True, "ack_timeout_s": 2.0, "retries": 3, "max_outbox": 5},
    "health": {"camera_stale_s": 6.0},
    "heartbeat_s": 2.0,
}

GOOD_TAP = {"valid": True, "grade": None, "confidence": None, "peak_frequency_hz": 2800.0}


@pytest.fixture
def rig():
    clock, bus, store = Clock(), InMemoryBus(), TileStore(":memory:")
    c = StationCoordinator(CFG, bus, store, clock=clock)
    c.start()
    c.set_health("mic", OK, "listening")
    return clock, bus, store, c


def camera(bus, tile_id, grade="Grade A", seq=1):
    bus.publish(f"tile/{tile_id}/camera/result", {"tile_id": tile_id, "grade": grade, "seq": seq})


def finals(bus):
    return bus.messages("tile/+/final")


def test_camera_then_tap_publishes_one_final_and_stores_it(rig):
    clock, bus, store, c = rig
    camera(bus, "T1", "Grade B")
    assert c.snapshot()["awaiting_count"] == 1
    assert c.on_tap(GOOD_TAP) == "T1"
    (final,) = finals(bus)
    assert final["tile_id"] == "T1" and final["final_grade"] == "Grade B" and final["destination"] == "bin2"
    assert final["attempt"] == 1 and final["timed_out"] is False
    assert bus.messages("tile/+/acoustic/result")[0]["status"] == "done"
    row = store.get("T1")
    assert row["final_grade"] == "Grade B" and row["delivery"] == "sent"


def test_ack_marks_delivered_and_releases_the_next_final(rig):
    clock, bus, store, c = rig
    camera(bus, "T1")
    camera(bus, "T2", "Reject", seq=2)
    c.on_tap(GOOD_TAP)
    c.on_tap(GOOD_TAP)
    # stop-and-wait: T2's final is queued behind T1's unacknowledged one
    assert [f["tile_id"] for f in finals(bus)] == ["T1"]
    bus.publish("tile/T1/ack", {"status": "ok"})
    assert [f["tile_id"] for f in finals(bus)] == ["T1", "T2"]
    assert store.get("T1")["delivery"] == "acked"
    bus.publish("tile/T2/ack", {"status": "ok"})
    assert c.snapshot()["health"]["unoq"]["level"] == OK


def test_unacknowledged_final_is_retried_then_link_reported_down(rig):
    clock, bus, store, c = rig
    camera(bus, "T1")
    c.on_tap(GOOD_TAP)
    for _ in range(2):  # first send + 2 retries = 3 unanswered attempts
        clock.t += 2.0
        c.tick()
    assert [f["attempt"] for f in finals(bus)] == [1, 2, 3]
    clock.t += 2.0
    c.tick()  # it keeps retrying after the link is declared down
    assert [f["attempt"] for f in finals(bus)] == [1, 2, 3, 4]
    snap = c.snapshot()
    assert snap["health"]["unoq"]["level"] == FAIL
    assert snap["state"] == "fault"
    assert any(a["kind"] == "no_ack" for a in snap["alarms"])
    # a late ack recovers the link
    bus.publish("tile/T1/ack", {"status": "ok"})
    assert c.snapshot()["health"]["unoq"]["level"] == OK


def test_late_duplicate_ack_is_harmless(rig):
    clock, bus, store, c = rig
    camera(bus, "T1")
    c.on_tap(GOOD_TAP)
    bus.publish("tile/T1/ack", {})
    bus.publish("tile/T1/ack", {})
    assert len(finals(bus)) == 1


def test_no_tap_within_max_wait_is_held_not_guessed(rig):
    clock, bus, store, c = rig
    camera(bus, "T1", "Grade A")
    clock.t += 11.0
    c.tick()
    (final,) = finals(bus)
    assert final["final_grade"] == "Hold" and final["destination"] == "manual" and final["timed_out"] is True
    assert any(a["kind"] == "no_tap_timeout" for a in c.snapshot()["alarms"])


def test_camera_reject_stays_reject_on_timeout(rig):
    clock, bus, store, c = rig
    camera(bus, "T1", "Reject")
    clock.t += 11.0
    c.tick()
    assert finals(bus)[0]["final_grade"] == "Reject"


def test_orphan_tap_is_alarmed_and_decides_nothing(rig):
    clock, bus, store, c = rig
    assert c.on_tap(GOOD_TAP) is None
    assert finals(bus) == []
    snap = c.snapshot()
    assert snap["counters"]["orphan_taps"] == 1
    assert snap["alarms"][0]["kind"] == "orphan_tap"


def test_unreadable_camera_result_keeps_its_place_and_ends_up_held(rig):
    clock, bus, store, c = rig
    bus.publish("tile/T1/camera/result", b"{not json")
    camera(bus, "T2", "Grade A")
    c.on_tap(GOOD_TAP)  # physically T1's tap
    c.on_tap(GOOD_TAP)
    got = {f["tile_id"]: f for f in finals(bus)}
    bus.publish("tile/T1/ack", {})
    got = {f["tile_id"]: f for f in finals(bus)}
    assert got["T1"]["final_grade"] == "Hold"
    assert got["T2"]["final_grade"] == "Grade A"


def test_sound_vote_only_counts_when_enabled_and_confident(rig):
    clock, bus, store, c = rig
    bad = {"valid": True, "grade": "Reject", "confidence": 0.9}
    camera(bus, "T1", "Grade A")
    c.set_vote_enabled(False)
    c.on_tap(bad)
    assert finals(bus)[0]["final_grade"] == "Grade A"
    bus.publish("tile/T1/ack", {})
    c.set_vote_enabled(True)
    camera(bus, "T2", "Grade A", seq=2)
    c.on_tap(bad)
    assert finals(bus)[-1]["final_grade"] == "Reject"


def test_broker_down_is_a_fault_and_finals_wait_then_flush(rig):
    clock, bus, store, c = rig
    camera(bus, "T1")
    bus.link_up = False
    c.on_tap(GOOD_TAP)  # publish refused: nothing leaves, nothing lost
    assert finals(bus) == []
    assert c.snapshot()["health"]["broker"]["level"] == FAIL
    bus.link_up = True
    clock.t += 1.0
    c.tick()
    assert [f["tile_id"] for f in finals(bus)] == ["T1"]


def test_outbox_overflow_drops_oldest_queued_and_says_so(rig):
    clock, bus, store, c = rig
    for i in range(8):
        camera(bus, f"T{i}", seq=i)
        c.on_tap(GOOD_TAP)
    # T0 in flight (never acked); outbox holds 5, so 2 were dropped
    snap = c.snapshot()
    assert any(a["kind"] == "outbox_overflow" for a in snap["alarms"])
    assert any(store.get(f"T{i}")["delivery"] == "dropped" for i in range(8))


def test_commands_from_master_change_settings(rig):
    clock, bus, store, c = rig
    bus.publish("station/cmd", {"cmd": "vote", "enabled": False})
    assert c.vote_enabled is False
    bus.publish("station/cmd", {"cmd": "hold_all", "enabled": True})
    camera(bus, "T1", "Grade A")
    c.on_tap(GOOD_TAP)
    assert finals(bus)[0]["final_grade"] == "Hold"
    bus.publish("station/cmd", {"cmd": "frobnicate"})
    assert any(a["kind"] == "bad_message" for a in c.snapshot()["alarms"])


def test_camera_health_follows_its_heartbeat(rig):
    clock, bus, store, c = rig
    assert c.snapshot()["health"]["camera"]["level"] == FAIL  # nothing heard yet
    bus.publish("station/camera/status", {"source_kind": "camera", "source_status": "running", "tile_count": 4})
    assert c.snapshot()["health"]["camera"]["level"] == OK
    bus.publish("station/camera/status", {"source_kind": "file", "source_status": "running"})
    assert c.snapshot()["health"]["camera"]["level"] == WARN
    clock.t += 10.0
    assert c.snapshot()["health"]["camera"]["level"] == FAIL


def test_heartbeat_is_published_retained(rig):
    clock, bus, store, c = rig
    clock.t += 3.0
    c.tick()
    topic, data, qos, retain = [m for m in bus.published if m[0] == "station/status"][-1]
    assert retain is True
    assert "health" in json.loads(data)


def test_require_ack_false_sends_everything_immediately():
    clock, bus, store = Clock(), InMemoryBus(), TileStore(":memory:")
    cfg = dict(CFG, unoq={"require_ack": False})
    c = StationCoordinator(cfg, bus, store, clock=clock)
    c.start()
    c.set_health("mic", OK)
    for i in range(3):
        camera(bus, f"T{i}", seq=i)
        c.on_tap(GOOD_TAP)
    assert len(finals(bus)) == 3
    assert c.snapshot()["health"]["unoq"]["level"] == WARN


def test_external_tap_topic_calls_the_capture_hook():
    calls = []
    clock, bus, store = Clock(), InMemoryBus(), TileStore(":memory:")
    c = StationCoordinator(CFG, bus, store, clock=clock, on_external_tap=lambda: calls.append(1))
    c.start()
    bus.publish("station/tap", {})
    assert calls == [1]


# ---- striker tile-present flag + Nano link ----------------------------------


def test_strike_with_no_tile_under_it_does_not_use_a_tile_slot(rig):
    clock, bus, store, c = rig
    camera(bus, "T1", "Grade A")
    assert c.on_tap(GOOD_TAP, tile_present=False) is None
    assert c.snapshot()["awaiting_count"] == 1
    assert c.snapshot()["counters"]["empty_strikes"] == 1
    assert c.on_tap(GOOD_TAP, tile_present=True) == "T1"


def test_tile_under_striker_but_no_camera_result_is_sent_as_hold_to_keep_order(rig):
    clock, bus, store, c = rig
    tid = c.on_tap(GOOD_TAP, tile_present=True)  # the camera never reported this tile
    assert tid.startswith("UNKNOWN-")
    final = finals(bus)[0]
    assert final["final_grade"] == "Hold" and final["destination"] == "manual" and final["camera_grade"] is None
    snap = c.snapshot()
    assert snap["counters"]["missed_camera"] == 1
    assert any(a["kind"] == "missed_camera" for a in snap["alarms"])


def test_unknown_presence_stays_a_plain_orphan(rig):
    clock, bus, store, c = rig
    assert c.on_tap(GOOD_TAP, tile_present=None) is None
    assert finals(bus) == []


def test_tap_event_is_kept_for_its_clip_and_expires_if_no_clip_comes(rig):
    clock, bus, store, c = rig
    bus.publish("station/tap", {"seq": 1, "tile_present": True})
    assert c.pop_tap_event()["tile_present"] is True
    assert c.pop_tap_event() is None
    bus.publish("station/tap", {"seq": 2, "tile_present": False})
    clock.t += 6.0  # its clip never arrived
    assert c.pop_tap_event() is None


def test_nano_health_is_a_warning_until_required_then_a_fault():
    for required, expected in ((False, WARN), (True, FAIL)):
        clock, bus, store = Clock(), InMemoryBus(), TileStore(":memory:")
        c = StationCoordinator(dict(CFG, nano={"required": required, "stale_s": 3.0}), bus, store, clock=clock)
        c.start()
        assert c.snapshot()["health"]["nano"]["level"] == expected
        bus.publish("station/nano/status", {"state": "online", "link": True, "belt_pct": 50})
        assert c.snapshot()["health"]["nano"]["level"] == OK
        clock.t += 5.0
        assert c.snapshot()["health"]["nano"]["level"] == expected


def test_transit_times_are_measured_stored_and_summarised(rig):
    clock, bus, store, c = rig
    for i, wait in enumerate([4.0, 6.0, 5.0]):
        camera(bus, f"T{i}", seq=i)
        clock.t += wait
        c.on_tap(GOOD_TAP)
        bus.publish(f"tile/T{i}/ack", {})
    t = c.snapshot()["transit"]
    assert t["n"] == 3 and t["min_s"] == 4.0 and t["max_s"] == 6.0 and t["median_s"] == 5.0
    assert store.get("T1")["transit_s"] == 6.0


def test_an_unusable_recording_does_not_count_as_a_transit(rig):
    clock, bus, store, c = rig
    camera(bus, "T1")
    clock.t += 5.0
    c.on_tap({"valid": False, "grade": None, "confidence": None})
    assert c.snapshot()["transit"]["n"] == 0


def test_uno_q_rejection_is_alarmed_but_releases_the_queue(rig):
    clock, bus, store, c = rig
    camera(bus, "T1")
    camera(bus, "T2", seq=2)
    c.on_tap(GOOD_TAP)
    c.on_tap(GOOD_TAP)
    bus.publish("tile/T1/ack", {"status": "rejected", "reason": "unknown bin"})
    assert store.get("T1")["delivery"] == "rejected"
    assert any(a["kind"] == "unoq_rejected" for a in c.snapshot()["alarms"])
    assert [f["tile_id"] for f in finals(bus)] == ["T1", "T2"]  # T2 was released behind it


# ---- whole-line soft stop / start ------------------------------------------

def _nano(bus, pct):
    bus.publish("station/nano/status", {"state": "online", "link": True, "belt_pct": pct, "ts": 1.0})


def test_line_stop_holds_everything_zeroes_the_belt_and_inhibits_the_striker(rig):
    clock, bus, store, c = rig
    _nano(bus, 60)
    bus.publish("station/camera/status", {"source_kind": "camera", "source_status": "running", "tile_count": 0})
    bus.publish("station/cmd", {"cmd": "line", "state": "stop"})
    assert c.line_stopped is True
    assert bus.messages("station/belt")[-1] == {"percent": 0}
    assert bus.messages("station/strike")[-1] == {"enabled": False}
    snap = c.snapshot()
    assert snap["settings"]["line"] == "stopped" and snap["settings"]["hold_all"] is True
    assert snap["state"] == "line_stopped"
    # a tile already in flight is held for a person, never sorted automatically
    camera(bus, "T1", "Grade A")
    c.on_tap(GOOD_TAP)
    assert store.get("T1")["final_grade"] == "Hold"
    assert finals(bus) == []                      # decided, but not handed to the gantry while stopped
    bus.publish("station/cmd", {"cmd": "line", "state": "start"})
    assert [f["final_grade"] for f in finals(bus)] == ["Hold"]   # delivered, as Hold, once the line runs


def test_line_start_restores_the_speed_and_the_hold_setting_it_overrode(rig):
    clock, bus, store, c = rig
    _nano(bus, 45)
    bus.publish("station/cmd", {"cmd": "line", "state": "stop"})
    bus.publish("station/cmd", {"cmd": "line", "state": "start"})
    assert c.line_stopped is False
    assert bus.messages("station/belt")[-1] == {"percent": 45}
    assert bus.messages("station/strike")[-1] == {"enabled": True}
    snap = c.snapshot()
    assert snap["settings"]["line"] == "running" and snap["settings"]["hold_all"] is False
    # strike goes first, belt last: nothing moves until the line can use it
    order = [t for t, *_ in bus.published if t in ("station/belt", "station/strike")][-2:]
    assert order == ["station/strike", "station/belt"]


def test_hold_all_that_was_on_before_the_stop_is_still_on_after_start(rig):
    clock, bus, store, c = rig
    _nano(bus, 50)
    c.set_hold_all(True)
    bus.publish("station/cmd", {"cmd": "line", "state": "stop"})
    bus.publish("station/cmd", {"cmd": "line", "state": "start"})
    assert c.snapshot()["settings"]["hold_all"] is True


def test_start_refuses_to_guess_a_belt_speed(rig):
    clock, bus, store, c = rig                      # the Nano has never reported a speed
    bus.publish("station/cmd", {"cmd": "line", "state": "stop"})
    before = len(bus.messages("station/belt"))
    bus.publish("station/cmd", {"cmd": "line", "state": "start"})
    assert c.line_stopped is True and len(bus.messages("station/belt")) == before
    assert any(a["kind"] == "line_start" for a in c.snapshot()["alarms"])
    # ...but an explicit speed from the master is accepted
    bus.publish("station/cmd", {"cmd": "line", "state": "start", "belt_percent": 35})
    assert c.line_stopped is False and bus.messages("station/belt")[-1] == {"percent": 35}


def test_stop_twice_does_not_overwrite_what_the_first_stop_saved(rig):
    clock, bus, store, c = rig
    _nano(bus, 70)
    bus.publish("station/cmd", {"cmd": "line", "state": "stop"})
    _nano(bus, 0)                                   # the belt now reports 0
    bus.publish("station/cmd", {"cmd": "line", "state": "stop"})
    bus.publish("station/cmd", {"cmd": "line", "state": "start"})
    assert bus.messages("station/belt")[-1] == {"percent": 70}


def test_bad_line_commands_are_rejected_and_change_nothing(rig):
    clock, bus, store, c = rig
    for bad in ({"cmd": "line"}, {"cmd": "line", "state": "pause"},
                {"cmd": "line", "state": "start", "belt_percent": 400},
                {"cmd": "line", "state": "start", "belt_percent": True}):
        bus.publish("station/cmd", bad)
    assert c.line_stopped is False and bus.messages("station/belt") == []
    assert len([a for a in c.snapshot()["alarms"] if a["kind"] == "bad_message"]) == 4


# ---- gantry feed gate -------------------------------------------------------

def test_paused_feed_holds_finals_back_then_delivers_them_in_order(rig):
    clock, bus, store, c = rig
    camera(bus, "T1", "Grade A", seq=1)
    camera(bus, "T2", "Grade B", seq=2)
    bus.publish("station/cmd", {"cmd": "feed", "state": "pause"})
    assert c.feed_paused is True and c.snapshot()["settings"]["feed"] == "paused"
    c.on_tap(GOOD_TAP)
    c.on_tap(GOOD_TAP)
    assert finals(bus) == []                                    # nothing handed to the UNO Q
    assert store.get("T1")["final_grade"] == "Grade A"          # but both are decided and stored
    assert c.snapshot()["outbox_depth"] == 2
    bus.publish("station/cmd", {"cmd": "feed", "state": "resume"})
    assert [f["tile_id"] for f in finals(bus)] == ["T1"]        # stop-and-wait: next after T1's ack
    bus.publish("tile/T1/ack", {"status": "ok"})
    assert [f["tile_id"] for f in finals(bus)] == ["T1", "T2"]


def test_an_item_already_in_flight_still_completes_while_paused(rig):
    clock, bus, store, c = rig
    camera(bus, "T1")
    camera(bus, "T2", seq=2)
    c.on_tap(GOOD_TAP)                                          # T1 sent, awaiting its ack
    bus.publish("station/cmd", {"cmd": "feed", "state": "pause"})
    c.on_tap(GOOD_TAP)                                          # T2 decided while paused
    bus.publish("tile/T1/ack", {"status": "ok"})                # T1's ack still arrives and is honoured
    assert store.get("T1")["delivery"] == "acked"
    assert [f["tile_id"] for f in finals(bus)] == ["T1"]        # T2 is held back


def test_feed_pause_is_visible_in_health_and_gets_loud_before_it_drops_tiles(rig):
    clock, bus, store, c = rig
    bus.publish("station/cmd", {"cmd": "feed", "state": "pause"})
    h = c.snapshot()["health"]["unoq"]
    assert h["level"] == WARN and "paused" in h["detail"]
    for i in range(CFG["unoq"]["max_outbox"]):
        camera(bus, f"Q{i}", seq=i + 1)
        c.on_tap(GOOD_TAP)
    h = c.snapshot()["health"]["unoq"]
    assert h["level"] == FAIL and "dropped" in h["detail"]


def test_line_stop_and_start_move_the_feed_and_it_can_also_be_set_alone(rig):
    clock, bus, store, c = rig
    _nano(bus, 50)
    bus.publish("station/cmd", {"cmd": "line", "state": "stop"})
    assert c.feed_paused is True
    bus.publish("station/cmd", {"cmd": "line", "state": "start"})
    assert c.feed_paused is False and c.snapshot()["settings"]["feed"] == "running"
    # the feed on its own does not touch the belt or the striker
    n_belt, n_strike = len(bus.messages("station/belt")), len(bus.messages("station/strike"))
    bus.publish("station/cmd", {"cmd": "feed", "state": "pause"})
    assert len(bus.messages("station/belt")) == n_belt and len(bus.messages("station/strike")) == n_strike
    assert c.line_stopped is False
    bus.publish("station/cmd", {"cmd": "feed", "state": "wat"})
    assert any(a["kind"] == "bad_message" for a in c.snapshot()["alarms"])


def test_a_refused_line_start_leaves_the_feed_paused(rig):
    clock, bus, store, c = rig                                  # no known belt speed
    bus.publish("station/cmd", {"cmd": "line", "state": "stop"})
    bus.publish("station/cmd", {"cmd": "line", "state": "start"})
    assert c.line_stopped is True and c.feed_paused is True
