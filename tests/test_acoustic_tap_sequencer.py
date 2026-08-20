import pytest

from acoustic.tap_sequencer import TapSequencer


def make_sequencer(**overrides) -> TapSequencer:
    kwargs = dict(
        settle_s=0.2,
        arm_s=0.3,
        arm_settle_s=0.1,
        lock_pulse_s=0.1,
        cooldown_s=0.5,
    )
    kwargs.update(overrides)
    return TapSequencer(**kwargs)


def run_until(sequencer: TapSequencer, dt: float, max_ticks: int, predicate):
    for _ in range(max_ticks):
        state = sequencer.advance(dt, tile_present=True)
        if predicate(state):
            return state
    raise AssertionError("predicate never satisfied within max_ticks")


def test_no_tile_never_leaves_wait_state():
    sequencer = make_sequencer()
    for _ in range(50):
        state = sequencer.advance(0.05, tile_present=False)
        assert state.state == "WAIT_TILE"
    assert sequencer.tap_count == 0


def test_full_cycle_fires_exactly_one_tap():
    sequencer = make_sequencer()
    state = run_until(sequencer, 0.01, 200, lambda s: s.tap_fired)

    assert state.state == "RELEASE"
    assert state.lock_on is True
    assert state.arm_on is False
    assert state.tap_count == 1


def test_arm_solenoid_energizes_before_release():
    sequencer = make_sequencer()
    saw_arm_on = False
    for _ in range(200):
        state = sequencer.advance(0.01, tile_present=True)
        if state.arm_on:
            saw_arm_on = True
        if state.tap_fired:
            break
    assert saw_arm_on, "ARM solenoid should energize at some point before release"


def test_tile_present_held_does_not_retrigger_after_cooldown():
    sequencer = make_sequencer(cooldown_s=0.05)
    # Run well past one full cycle plus cooldown, tile never leaves.
    for _ in range(500):
        sequencer.advance(0.01, tile_present=True)
    assert sequencer.tap_count == 1
    assert sequencer.state == "WAIT_TILE"


def test_tile_removed_and_returned_arms_a_second_cycle():
    sequencer = make_sequencer(cooldown_s=0.05)
    run_until(sequencer, 0.01, 200, lambda s: s.tap_fired)
    # let cooldown finish
    for _ in range(20):
        sequencer.advance(0.01, tile_present=True)
    assert sequencer.tap_count == 1

    # tile leaves, then a new one arrives -> should arm again
    sequencer.advance(0.01, tile_present=False)
    second = run_until(sequencer, 0.01, 200, lambda s: s.tap_fired)
    assert second.tap_count == 2


def test_rejects_negative_duration():
    with pytest.raises(ValueError):
        make_sequencer(arm_s=-1.0)


def test_rejects_negative_dt():
    sequencer = make_sequencer()
    with pytest.raises(ValueError):
        sequencer.advance(-0.1, tile_present=True)
