import pytest

from pick_place.mock_state import MockGantrySimulator, Waypoint, build_default_patrol


def test_moves_toward_target_without_overshoot():
    waypoints = [
        Waypoint(x=0, y=0, z=0, travel_state="IDLE", vacuum_on=False, dwell_state="IDLE"),
        Waypoint(x=100, y=0, z=0, travel_state="MOVING", vacuum_on=False, dwell_state="IDLE"),
    ]
    sim = MockGantrySimulator(waypoints, travel_speed_mm_s=50)

    pose = sim.advance(1.0)  # 50 mm/s * 1s = 50mm toward x=100

    assert pose.x == pytest.approx(50.0)
    assert pose.machine_state == "MOVING"


def test_reaches_target_exactly_and_advances_index():
    waypoints = [
        Waypoint(x=0, y=0, z=0, travel_state="IDLE", vacuum_on=False, dwell_state="IDLE"),
        Waypoint(x=10, y=0, z=0, travel_state="MOVING", vacuum_on=True, dwell_state="ARRIVED"),
    ]
    sim = MockGantrySimulator(waypoints, travel_speed_mm_s=100)

    pose = sim.advance(1.0)  # step = 100mm > dist = 10mm -> snaps to target

    assert pose.x == pytest.approx(10.0)
    assert pose.vacuum_on is True
    assert pose.machine_state == "ARRIVED"


def test_dwell_holds_position_then_advances_to_next_waypoint():
    waypoints = [
        Waypoint(x=0, y=0, z=0, travel_state="IDLE", vacuum_on=False, dwell_state="IDLE"),
        Waypoint(x=10, y=0, z=0, travel_state="MOVING", vacuum_on=True, dwell_state="PICKING", dwell_s=0.5),
        Waypoint(x=20, y=0, z=0, travel_state="MOVING", vacuum_on=False, dwell_state="IDLE"),
    ]
    sim = MockGantrySimulator(waypoints, travel_speed_mm_s=100)

    sim.advance(1.0)  # reaches x=10, starts dwelling
    pose_mid_dwell = sim.advance(0.2)  # still dwelling
    assert pose_mid_dwell.x == pytest.approx(10.0)
    assert pose_mid_dwell.machine_state == "PICKING"

    sim.advance(0.4)  # dwell (0.5s total) elapses; index advances to the x=20 waypoint
    pose_after_dwell = sim.advance(0.01)  # next tick actually moves toward it (step=1mm < 10mm dist)
    assert pose_after_dwell.machine_state == "MOVING"
    assert pose_after_dwell.x > 10.0


def test_loops_back_to_first_waypoint():
    waypoints = [
        Waypoint(x=0, y=0, z=0, travel_state="HOMING", vacuum_on=False, dwell_state="IDLE"),
        Waypoint(x=10, y=0, z=0, travel_state="MOVING", vacuum_on=False, dwell_state="IDLE"),
    ]
    sim = MockGantrySimulator(waypoints, travel_speed_mm_s=5)

    sim.advance(1.0)  # partway to x=10 (step=5mm < 10mm dist)
    sim.advance(1.0)  # arrives at x=10, wraps target back to index 0 (home)
    pose = sim.advance(1.0)  # now travelling back toward x=0

    assert pose.x < 10.0
    assert pose.machine_state == "HOMING"


def test_rejects_empty_waypoints():
    with pytest.raises(ValueError):
        MockGantrySimulator([], travel_speed_mm_s=10)


def test_rejects_non_positive_speed():
    waypoints = [Waypoint(x=0, y=0, z=0, travel_state="IDLE", vacuum_on=False, dwell_state="IDLE")]
    with pytest.raises(ValueError):
        MockGantrySimulator(waypoints, travel_speed_mm_s=0)


def test_rejects_negative_dt():
    waypoints = [
        Waypoint(x=0, y=0, z=0, travel_state="IDLE", vacuum_on=False, dwell_state="IDLE"),
        Waypoint(x=10, y=0, z=0, travel_state="MOVING", vacuum_on=False, dwell_state="IDLE"),
    ]
    sim = MockGantrySimulator(waypoints, travel_speed_mm_s=10)
    with pytest.raises(ValueError):
        sim.advance(-1.0)


def test_build_default_patrol_toggles_vacuum_through_pick_and_place():
    config = {
        "mock": {
            "travel_speed_mm_s": 30,
            "home": {"x": 0, "y": 0, "z": 100},
            "pick_xy": {"x": 50, "y": 0},
            "pick_z_mm": 10,
            "place_xy": {"x": 0, "y": 50},
            "place_z_mm": 10,
            "dwell_s": 0.1,
        }
    }
    waypoints = build_default_patrol(config)
    sim = MockGantrySimulator(waypoints, travel_speed_mm_s=config["mock"]["travel_speed_mm_s"])

    seen_states = set()
    vacuum_was_on = False
    for _ in range(1000):
        pose = sim.advance(0.05)
        seen_states.add(pose.machine_state)
        if pose.vacuum_on:
            vacuum_was_on = True

    assert vacuum_was_on
    assert "PICKING" in seen_states
    assert "PLACING" in seen_states
    assert "HOMING" in seen_states
