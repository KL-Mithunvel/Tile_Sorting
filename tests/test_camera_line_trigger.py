"""Synthetic-sequence tests for camera/line_trigger.py.

Same pattern as test_camera_tile_tracker.py: the detector is pure, so it is
driven with a hand-written sequence of normalized centre positions and checked
for firing on exactly the right frames. No camera, no frames, no I/O.
"""

import pytest

from camera.line_trigger import LineCrossingDetector


def run(detector, xs, y=0.5):
    """Feed a sequence of x positions, return the indices that fired."""
    return [i for i, x in enumerate(xs) if detector.update((x, y))]


class TestConstruction:
    @pytest.mark.parametrize("bad", [-0.1, 1.1])
    def test_rejects_position_outside_frame(self, bad):
        with pytest.raises(ValueError, match="position"):
            LineCrossingDetector(position=bad)

    def test_rejects_unknown_orientation(self):
        with pytest.raises(ValueError, match="orientation"):
            LineCrossingDetector(orientation="diagonal")

    def test_rejects_unknown_direction(self):
        with pytest.raises(ValueError, match="direction"):
            LineCrossingDetector(direction="sideways")

    def test_rejects_negative_hysteresis(self):
        with pytest.raises(ValueError, match="hysteresis"):
            LineCrossingDetector(hysteresis=-0.01)


class TestBasicCrossing:
    def test_fires_once_when_centre_crosses(self):
        d = LineCrossingDetector(position=0.5, hysteresis=0.0)
        fired = run(d, [0.2, 0.4, 0.6, 0.8])
        assert fired == [2]
        assert d.crossing_count == 1

    def test_does_not_fire_without_reaching_the_line(self):
        d = LineCrossingDetector(position=0.5, hysteresis=0.0)
        assert run(d, [0.1, 0.2, 0.3, 0.4]) == []
        assert d.crossing_count == 0

    def test_first_sighting_never_counts_as_a_crossing(self):
        """A tile that enters the frame already past the line did not cross it
        in view, so it must not fire on its first frame."""
        d = LineCrossingDetector(position=0.5, hysteresis=0.0)
        assert run(d, [0.9, 0.95]) == []

    def test_two_tiles_fire_twice(self):
        d = LineCrossingDetector(position=0.5, hysteresis=0.0)
        fired_first = run(d, [0.2, 0.6])
        d.update(None)  # tile leaves
        fired_second = run(d, [0.2, 0.6])
        assert fired_first == [1] and fired_second == [1]
        assert d.crossing_count == 2


class TestHysteresis:
    def test_jitter_on_the_line_does_not_fire_repeatedly(self):
        """The real failure this guards: segmentation noise moves the centre a
        pixel either side of the line for several frames."""
        d = LineCrossingDetector(position=0.5, hysteresis=0.02)
        jitter = [0.2, 0.499, 0.501, 0.498, 0.502, 0.499, 0.503]
        assert run(d, jitter) == []
        assert d.crossing_count == 0

    def test_still_fires_once_the_centre_travels_clear(self):
        d = LineCrossingDetector(position=0.5, hysteresis=0.02)
        fired = run(d, [0.2, 0.499, 0.501, 0.55])
        assert fired == [3]

    def test_zero_hysteresis_fires_on_the_bare_sign_change(self):
        d = LineCrossingDetector(position=0.5, hysteresis=0.0)
        assert run(d, [0.499, 0.501]) == [1]


class TestDirection:
    def test_forward_only_ignores_reverse_travel(self):
        d = LineCrossingDetector(position=0.5, direction="forward", hysteresis=0.0)
        assert run(d, [0.8, 0.2]) == []
        assert d.crossing_count == 0

    def test_forward_only_accepts_forward_travel(self):
        d = LineCrossingDetector(position=0.5, direction="forward", hysteresis=0.0)
        assert run(d, [0.2, 0.8]) == [1]

    def test_reverse_only_accepts_reverse_travel(self):
        d = LineCrossingDetector(position=0.5, direction="reverse", hysteresis=0.0)
        assert run(d, [0.8, 0.2]) == [1]

    def test_both_accepts_either(self):
        d = LineCrossingDetector(position=0.5, direction="both", hysteresis=0.0)
        assert run(d, [0.2, 0.8, 0.2]) == [1, 2]
        assert d.crossing_count == 2

    def test_wrong_way_still_updates_side_so_a_later_correct_pass_fires(self):
        """A tile nudged backwards across the line must not be counted, but it
        must also not leave the detector stuck unable to count its real pass."""
        d = LineCrossingDetector(position=0.5, direction="forward", hysteresis=0.0)
        assert run(d, [0.8, 0.2, 0.8]) == [2]
        assert d.crossing_count == 1

    def test_event_reports_direction_even_when_it_does_not_fire(self):
        d = LineCrossingDetector(position=0.5, direction="forward", hysteresis=0.0)
        d.update((0.8, 0.5))
        event = d.update((0.2, 0.5))
        assert not event.crossed
        assert event.direction == "reverse"


class TestOrientation:
    def test_horizontal_line_uses_the_y_coordinate(self):
        d = LineCrossingDetector(position=0.5, orientation="horizontal", hysteresis=0.0)
        fired = [i for i, y in enumerate([0.2, 0.4, 0.6]) if d.update((0.5, y))]
        assert fired == [2]

    def test_vertical_line_ignores_y_movement(self):
        d = LineCrossingDetector(position=0.5, orientation="vertical", hysteresis=0.0)
        fired = [i for i, y in enumerate([0.1, 0.9, 0.1]) if d.update((0.3, y))]
        assert fired == []


class TestAbsence:
    def test_none_resets_the_remembered_side(self):
        d = LineCrossingDetector(position=0.5, hysteresis=0.0)
        d.update((0.2, 0.5))
        assert d.armed
        d.update(None)
        assert not d.armed

    def test_none_does_not_fire_or_count(self):
        d = LineCrossingDetector(position=0.5, hysteresis=0.0)
        assert not d.update(None)
        assert d.crossing_count == 0

    def test_new_tile_on_the_far_side_is_not_a_continuation(self):
        """Without the reset, tile B appearing past the line after tile A left
        before it would read as one tile crossing."""
        d = LineCrossingDetector(position=0.5, hysteresis=0.0)
        d.update((0.2, 0.5))   # tile A, before the line
        d.update(None)         # tile A gone
        assert not d.update((0.8, 0.5))  # tile B, already past
        assert d.crossing_count == 0


class TestEventObject:
    def test_event_is_falsy_when_nothing_crossed(self):
        d = LineCrossingDetector(position=0.5, hysteresis=0.0)
        assert not d.update((0.2, 0.5))

    def test_event_carries_direction_and_coordinate_on_firing(self):
        d = LineCrossingDetector(position=0.5, hysteresis=0.0)
        d.update((0.2, 0.5))
        event = d.update((0.7, 0.5))
        assert event.crossed
        assert event.direction == "forward"
        assert event.coordinate == pytest.approx(0.7)
