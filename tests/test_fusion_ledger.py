"""TileLedger: which tile does a tap belong to, and what happens when the
physical world and the camera's count disagree."""

import pytest

from fusion.ledger import AWAITING_TAP, TAPPED, TIMED_OUT, TileLedger


def make(max_wait=30.0, min_transit=0.0, keep=500):
    return TileLedger(max_wait_s=max_wait, min_transit_s=min_transit, keep=keep)


def test_taps_bind_to_tiles_in_physical_order():
    led = make()
    led.add_camera("T1", "Grade A", {}, now=0.0)
    led.add_camera("T2", "Grade B", {}, now=2.0)
    assert led.bind_tap(now=5.0).tile_id == "T1"
    assert led.bind_tap(now=7.0).tile_id == "T2"
    assert led.awaiting == []


def test_tap_with_no_waiting_tile_is_an_orphan_not_attached_to_anything():
    led = make()
    assert led.bind_tap(now=1.0) is None
    assert led.orphan_taps == 1
    # ... and a tile arriving afterwards is NOT handed that earlier tap
    led.add_camera("T1", "Grade A", {}, now=2.0)
    assert [e.tile_id for e in led.awaiting] == ["T1"]


def test_redelivered_camera_message_does_not_take_a_second_slot():
    led = make()
    first, new1 = led.add_camera("T1", "Grade A", {}, now=0.0)
    again, new2 = led.add_camera("T1", "Grade A", {}, now=0.1)
    assert new1 and not new2 and again is first
    assert len(led.awaiting) == 1


def test_tile_that_waits_too_long_expires_and_does_not_steal_the_next_tap():
    led = make(max_wait=10.0)
    led.add_camera("T1", "Grade A", {}, now=0.0)
    led.add_camera("T2", "Grade A", {}, now=6.0)
    expired = led.expire(now=11.0)
    assert [e.tile_id for e in expired] == ["T1"]
    assert expired[0].state == TIMED_OUT
    # the next tap belongs to T2, not to the expired T1
    assert led.bind_tap(now=12.0).tile_id == "T2"


def test_expire_leaves_young_tiles_alone():
    led = make(max_wait=10.0)
    led.add_camera("T1", "Grade A", {}, now=0.0)
    assert led.expire(now=9.9) == []
    assert led.get("T1").state == AWAITING_TAP


def test_min_transit_blocks_a_tap_on_a_tile_that_cannot_have_arrived():
    led = make(min_transit=3.0)
    led.add_camera("T1", "Grade A", {}, now=0.0)
    assert led.bind_tap(now=1.0) is None  # too early: orphan
    assert led.orphan_taps == 1
    assert led.bind_tap(now=3.0).tile_id == "T1"
    assert led.get("T1").state == TAPPED


def test_prune_forgets_finished_tiles_but_never_waiting_ones():
    led = make(keep=3)
    for i in range(5):
        led.add_camera(f"T{i}", "Grade A", {}, now=float(i))
    led.bind_tap(now=10.0)  # T0 tapped (finished)
    led.bind_tap(now=10.0)  # T1 tapped
    led.add_camera("T5", "Grade A", {}, now=11.0)
    assert led.get("T0") is None and led.get("T1") is None
    assert [e.tile_id for e in led.awaiting] == ["T2", "T3", "T4", "T5"]


def test_bad_config_is_refused():
    with pytest.raises(ValueError):
        TileLedger(max_wait_s=0)
    with pytest.raises(ValueError):
        TileLedger(max_wait_s=5, min_transit_s=5)
