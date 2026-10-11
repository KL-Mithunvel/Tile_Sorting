"""The UNO Q's side of the station contract, on its own and against the real
fusion coordinator (the two nodes never import each other; this test does)."""

import json

import pytest

from fusion.coordinator import OK, StationCoordinator
from fusion.store import TileStore
from fusion.transport import InMemoryBus
from pick_place.tile_feed import FeedClient, FinalQueue


def final(tile_id="T1", grade="Grade A", dest="bin_1", attempt=1, **kw):
    return {"tile_id": tile_id, "final_grade": grade, "destination": dest, "attempt": attempt, **kw}


# ---- the queue ----------------------------------------------------------------


def test_fifo_and_one_pick_at_a_time():
    q = FinalQueue()
    q.accept(final("T1"), 0.0)
    q.accept(final("T2", "Reject", "bin_4"), 0.1)
    first = q.start_next()
    assert first.tile_id == "T1" and q.start_next() is None  # one in hand
    q.finish("T1")
    assert q.start_next().tile_id == "T2"


def test_duplicate_is_acknowledged_but_queued_once():
    q = FinalQueue()
    assert q.accept(final("T1"), 0).status == "ok"
    again = q.accept(final("T1", attempt=2), 1)
    assert again.status == "duplicate" and again.should_ack
    assert q.snapshot()["depth"] == 1


def test_duplicate_of_an_already_picked_tile_is_still_a_duplicate():
    q = FinalQueue()
    q.accept(final("T1"), 0)
    q.start_next()
    q.finish("T1")
    assert q.accept(final("T1", attempt=3), 5).status == "duplicate"


def test_hold_is_a_real_job_for_the_manual_bin():
    q = FinalQueue()
    r = q.accept(final("T1", "Hold", "manual"), 0)
    assert r.status == "ok" and q.start_next().destination == "manual"


def test_malformed_final_is_rejected_with_an_ack_so_the_pi_is_not_stuck():
    q = FinalQueue()
    for bad in ({}, {"tile_id": "T1"}, final("T2", "Grade Z"), final("T3", dest="")):
        r = q.accept(bad, 0)
        assert r.status == "rejected" and r.should_ack
    assert q.snapshot()["depth"] == 0


def test_full_queue_applies_back_pressure_by_not_acknowledging():
    q = FinalQueue(max_len=2)
    q.accept(final("T1"), 0)
    q.accept(final("T2"), 0)
    r = q.accept(final("T3"), 0)
    assert r.status == "full" and not r.should_ack
    q.start_next()
    assert q.accept(final("T3"), 1).status == "ok"  # the Pi's retry succeeds once there is room


def test_finish_of_the_wrong_tile_is_refused():
    q = FinalQueue()
    q.accept(final("T1"), 0)
    q.start_next()
    with pytest.raises(ValueError):
        q.finish("T2")


# ---- over a bus ---------------------------------------------------------------


def test_feed_acks_new_duplicate_and_garbage():
    bus = InMemoryBus()
    feed = FeedClient(bus, clock=lambda: 1.0)
    feed.start()
    bus.publish("tile/T1/final", final("T1"))
    bus.publish("tile/T1/final", final("T1", attempt=2))
    bus.publish("tile/T9/final", b"{not json")
    acks = {(t.split("/")[1], json.loads(p)["status"]) for t, p, *_ in bus.published if t.endswith("/ack")}
    assert ("T1", "ok") in acks and ("T1", "duplicate") in acks and ("T9", "rejected") in acks
    assert feed.queue.snapshot()["depth"] == 1


def test_a_failing_on_job_callback_does_not_stop_acknowledgements():
    bus = InMemoryBus()

    def boom(job):
        raise RuntimeError("sequencer bug")

    feed = FeedClient(bus, on_job=boom)
    feed.start()
    bus.publish("tile/T1/final", final("T1"))
    assert any(t == "tile/T1/ack" for t, *_ in bus.published)


# ---- against the real coordinator --------------------------------------------


def test_station_and_uno_q_work_through_a_full_hand_off_in_order():
    clock = [1000.0]
    bus, store = InMemoryBus(), TileStore(":memory:")
    cfg = {"ledger": {"max_wait_s": 30}, "unoq": {"require_ack": True, "ack_timeout_s": 2.0, "retries": 3},
           "final": {"vote_enabled": False, "bins": {"Grade A": "bin_1", "Reject": "bin_4", "Hold": "manual"}}}
    station = StationCoordinator(cfg, bus, store, clock=lambda: clock[0])
    station.start()
    station.set_health("mic", OK)
    feed = FeedClient(bus, clock=lambda: clock[0])
    feed.start()

    for i, grade in enumerate(["Grade A", "Reject", "Grade A"]):
        bus.publish(f"tile/T{i}/camera/result", {"tile_id": f"T{i}", "grade": grade, "seq": i})
        station.on_tap({"valid": True, "grade": None, "confidence": None})

    # stop-and-wait + immediate in-memory acks: all three delivered, in order, once
    picked = []
    while (job := feed.queue.start_next()) is not None:
        picked.append((job.tile_id, job.grade, job.destination))
        feed.queue.finish(job.tile_id)
    assert picked == [("T0", "Grade A", "bin_1"), ("T1", "Reject", "bin_4"), ("T2", "Grade A", "bin_1")]
    assert station.snapshot()["health"]["unoq"]["level"] == OK
    assert all(store.get(f"T{i}")["delivery"] == "acked" for i in range(3))


def test_station_retries_resend_do_not_create_a_second_pick():
    clock = [1000.0]
    bus, store = InMemoryBus(), TileStore(":memory:")
    cfg = {"unoq": {"require_ack": True, "ack_timeout_s": 2.0, "retries": 5}, "final": {"bins": {}}}
    station = StationCoordinator(cfg, bus, store, clock=lambda: clock[0])
    station.start()
    bus.publish("tile/T0/camera/result", {"tile_id": "T0", "grade": "Grade A", "seq": 0})
    # The UNO Q is offline while the Pi sends and retries...
    station.on_tap({"valid": True, "grade": None, "confidence": None})
    for _ in range(3):
        clock[0] += 2.0
        station.tick()
    # ...then it comes up and receives the retries it can see (here: all three re-sent finals)
    feed = FeedClient(bus, clock=lambda: clock[0])
    feed.start()
    for topic, data, *_ in list(bus.published):
        if topic == "tile/T0/final":
            bus.publish(topic, data)
    assert feed.queue.snapshot()["depth"] == 1
    assert feed.queue.snapshot()["counters"]["duplicates"] >= 1


# ---- source (where the tile is taken from) --------------------------------------


def test_source_defaults_to_the_conveyor_end():
    q = FinalQueue()
    q.accept(final("T1"), 0)
    assert q.start_next().source == "pickup"


def test_source_named_by_the_pi_is_kept():
    q = FinalQueue()
    q.accept(final("T1", source="pickup_b"), 0)
    assert q.start_next().source == "pickup_b"


def test_unusable_source_is_rejected_not_defaulted():
    q = FinalQueue()
    for bad in (5, "", ["pickup"]):
        r = q.accept(final(f"T{bad!r}", source=bad), 0)
        assert r.status == "rejected" and r.should_ack
    assert q.snapshot()["depth"] == 0
