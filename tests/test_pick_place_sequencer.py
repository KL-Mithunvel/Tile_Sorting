"""Job -> command list: ordering, safety, and that planning changes nothing."""

import pytest

from pick_place import protocol
from pick_place.coordinate_model import (CoordinateModel, OutOfEnvelopeError, StackFullError,
                                         UnknownLocationError)
from pick_place.mock_state import load_config
from pick_place.sequencer import Feeds, plan_park, plan_pick_place


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def model(cfg):
    return CoordinateModel.from_config(cfg)


@pytest.fixture
def feeds(cfg):
    return Feeds.from_config(cfg)


def wire(plan, start=100):
    return [s.command.encode(start + i) for i, s in enumerate(plan.steps)]


def test_plan_has_the_protocol_doc_shape(model, feeds):
    plan = plan_pick_place("pickup", "bin_2", model, feeds)
    assert [s.label for s in plan.steps] == [
        "raise", "over_source", "descend_pick", "grip", "lift",
        "over_destination", "descend_place", "release", "retract"]
    assert wire(plan) == [
        "100 MOVE Z200.0 F4000.0",
        "101 MOVE X500.0 Y100.0 F8000.0",
        "102 MOVE Z20.0 F2000.0",
        "103 VAC ON",
        "104 MOVE Z200.0 F3000.0",
        "105 MOVE X100.0 Y250.0 F8000.0",
        "106 MOVE Z20.0 F2000.0",
        "107 VAC OFF",
        "108 MOVE Z200.0 F4000.0",
    ]


def test_xy_only_ever_changes_at_safe_z(model, feeds):
    """Replay the plan against a position tracker: every XY move starts and ends at safe Z."""
    for dest in model.stack_names:
        z = None
        for step in plan_pick_place("pickup", dest, model, feeds).steps:
            args = {a[0]: float(a[1:]) for a in step.command.args if a[0] in "XYZ" and a[1:].replace(".", "").isdigit()}
            if "X" in args or "Y" in args:
                assert z == model.safe_z_mm, f"XY move at z={z} on the way to {dest}"
            if "Z" in args:
                z = args["Z"]


def test_vacuum_is_on_exactly_between_grip_and_release(model, feeds):
    plan = plan_pick_place("pickup", "bin_1", model, feeds)
    labels = [s.label for s in plan.steps]
    assert labels.index("grip") < labels.index("lift") < labels.index("descend_place") < labels.index("release")
    assert plan.steps[plan.release_index].command == protocol.vac(False)


def test_pick_steps_are_the_retry_unit(model, feeds):
    plan = plan_pick_place("pickup", "bin_1", model, feeds)
    assert [s.label for s in plan.pick_steps] == ["descend_pick", "grip"]


def test_release_height_follows_the_stack(model, feeds):
    assert plan_pick_place("pickup", "bin_1", model, feeds).place_z == pytest.approx(20.0)
    model.record_placed("bin_1")
    model.record_placed("bin_1")
    plan = plan_pick_place("pickup", "bin_1", model, feeds)
    assert plan.place_z == pytest.approx(44.0)
    assert "MOVE Z44.0 F2000.0" == plan.steps[6].command.encode(0)[2:]


def test_planning_does_not_touch_the_stacks(model, feeds):
    before = model.counts()
    plan_pick_place("pickup", "bin_3", model, feeds)
    assert model.counts() == before


def test_bad_jobs_are_refused_before_any_command_exists(model, feeds):
    with pytest.raises(UnknownLocationError):
        plan_pick_place("pickup", "manual", model, feeds)
    with pytest.raises(UnknownLocationError):
        plan_pick_place("shelf", "bin_1", model, feeds)
    model.reset_stack("bin_4", 10)
    with pytest.raises(StackFullError):
        plan_pick_place("pickup", "bin_4", model, feeds)


def test_every_planned_move_is_inside_the_envelope(model, feeds):
    env = model.envelope
    for dest in model.stack_names:
        for _ in range(10):
            plan = plan_pick_place("pickup", dest, model, feeds)
            for step in plan.steps:
                for a in step.command.args:
                    if step.command.verb == "MOVE" and a[0] in "XYZ":
                        v = float(a[1:])
                        lo, hi = {"X": (env.x_min, env.x_max), "Y": (env.y_min, env.y_max),
                                  "Z": (env.z_min, env.z_max)}[a[0]]
                        assert lo <= v <= hi
            model.record_placed(dest)


def test_safe_z_above_the_envelope_refuses_to_start(cfg):
    cfg["workspace"]["z_max_mm"] = 150  # below safe z (200)
    with pytest.raises(Exception):  # ConfigError at model build: safe z outside the envelope
        CoordinateModel.from_config(cfg)


def test_a_pickup_outside_the_envelope_at_plan_time(model, feeds):
    # A model built fine, then the envelope shrinks under it (e.g. a re-taught limit).
    model.envelope = type(model.envelope)(0, 300, 0, 400, 0, 200)
    with pytest.raises(OutOfEnvelopeError):
        plan_pick_place("pickup", "bin_1", model, feeds)


def test_park_is_a_single_raise_to_safe_z(model, feeds):
    step = plan_park(model, feeds)
    assert step.label == "park" and step.command.encode(1) == "1 MOVE Z200.0 F4000.0"
