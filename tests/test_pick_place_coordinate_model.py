"""Named locations and stack heights, from the shipped config and from small synthetic ones."""

import copy

import pytest

from pick_place.coordinate_model import (ConfigError, CoordinateModel, OutOfEnvelopeError, StackFullError,
                                         UnknownLocationError)
from pick_place.mock_state import load_config


def cfg(**over):
    c = copy.deepcopy(load_config())
    for key, value in over.items():
        c[key] = value
    return c


@pytest.fixture
def model():
    return CoordinateModel.from_config(load_config())


def test_shipped_config_builds_with_four_stacks_and_one_pickup(model):
    assert model.stack_names == ("bin_1", "bin_2", "bin_3", "bin_4")
    assert model.pickup_names == ("pickup",)
    assert model.tile_thickness_mm == 12.0


def test_each_tile_raises_the_next_release_by_one_thickness(model):
    z0 = model.place_point("bin_1").z
    model.record_placed("bin_1")
    model.record_placed("bin_1")
    assert model.place_point("bin_1").z == pytest.approx(z0 + 24.0)
    assert model.place_point("bin_2").z == pytest.approx(z0)  # other stacks independent
    assert model.counts() == {"bin_1": 2, "bin_2": 0, "bin_3": 0, "bin_4": 0}


def test_full_stack_refuses_and_reset_clears(model):
    for _ in range(10):
        model.record_placed("bin_3")
    assert model.is_full("bin_3")
    with pytest.raises(StackFullError):
        model.place_point("bin_3")
    model.reset_stack("bin_3")
    assert model.place_point("bin_3").z == pytest.approx(20.0)
    with pytest.raises(ValueError):
        model.reset_stack("bin_3", 11)


def test_unknown_names_are_refused_not_guessed(model):
    with pytest.raises(UnknownLocationError):
        model.pickup_point("shelf")
    with pytest.raises(UnknownLocationError):
        model.place_point("manual")
    with pytest.raises(UnknownLocationError):
        model.record_placed("bin_9")


def test_drop_in_bin_does_not_climb_or_fill():
    c = cfg()
    c["stacks"]["bin_4"]["stacking"] = False
    m = CoordinateModel.from_config(c)
    for _ in range(25):
        m.record_placed("bin_4")
    assert m.place_point("bin_4").z == pytest.approx(20.0) and not m.is_full("bin_4")


def test_a_stack_too_tall_for_safe_z_refuses_to_start():
    c = cfg()
    c["stacks"]["bin_1"]["max_height"] = 20  # 20 + 19*12 = 248 mm, above a 200 mm safe z
    with pytest.raises(ConfigError):
        CoordinateModel.from_config(c)


def test_location_outside_the_envelope_refuses_to_start():
    c = cfg()
    c["locations"]["pickup"]["x"] = 9999
    with pytest.raises(ConfigError):
        CoordinateModel.from_config(c)


def test_pickup_at_or_above_safe_z_refuses_to_start():
    c = cfg()
    c["locations"]["pickup"]["z"] = 200
    with pytest.raises(ConfigError):
        CoordinateModel.from_config(c)


def test_missing_config_blocks_are_a_config_error():
    c = cfg()
    del c["stacks"]
    with pytest.raises(ConfigError):
        CoordinateModel.from_config(c)


def test_check_in_envelope(model):
    model.check_in_envelope(0, 400, 200)
    model.check_in_envelope(None, None, 10)
    with pytest.raises(OutOfEnvelopeError):
        model.check_in_envelope(601, None, None)
    with pytest.raises(OutOfEnvelopeError):
        model.check_in_envelope(None, None, -1)
