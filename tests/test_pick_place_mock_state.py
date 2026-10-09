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


WORKSPACE = {"x_min_mm": 0, "x_max_mm": 100, "y_min_mm": 0, "y_max_mm": 100, "z_min_mm": 0, "z_max_mm": 100}


def _manual_sim():
    wp = [
        Waypoint(x=50, y=50, z=50, travel_state="HOMING", vacuum_on=False, dwell_state="IDLE"),
        Waypoint(x=60, y=50, z=50, travel_state="MOVING", vacuum_on=False, dwell_state="IDLE"),
    ]
    return MockGantrySimulator(wp, travel_speed_mm_s=10, workspace=WORKSPACE)


def test_jog_switches_to_manual_and_moves_at_travel_speed():
    sim = _manual_sim()
    sim.jog("x", 20)
    assert sim.mode == "manual"
    pose = sim.advance(1.0)  # 10 mm/s -> 10 of the 20 mm
    assert pose.x == pytest.approx(60.0)
    assert pose.machine_state == "MOVING"
    pose = sim.advance(5.0)
    assert pose.x == pytest.approx(70.0)
    assert pose.machine_state == "IDLE"


def test_jog_clamps_to_workspace():
    sim = _manual_sim()
    sim.jog("z", 500)
    assert sim.advance(100).z == pytest.approx(100.0)
    sim.jog("y", -500)
    assert sim.advance(100).y == pytest.approx(0.0)


def test_manual_mode_holds_still_without_jog():
    sim = _manual_sim()
    sim.set_mode("manual")
    pose = sim.advance(10.0)
    assert (pose.x, pose.y, pose.z) == (50, 50, 50)


def test_set_vacuum_is_manual_and_persists():
    sim = _manual_sim()
    sim.set_vacuum(True)
    assert sim.mode == "manual"
    assert sim.advance(1.0).vacuum_on is True
    sim.set_vacuum(False)
    assert sim.advance(1.0).vacuum_on is False


def test_returning_to_auto_resumes_patrol():
    sim = _manual_sim()
    sim.jog("x", 20)
    sim.advance(1.0)
    sim.set_mode("auto")
    pose = sim.advance(0.5)  # heads for the first waypoint again
    assert pose.machine_state == "HOMING" or pose.machine_state == "MOVING"


def test_jog_rejects_bad_axis_and_mode():
    sim = _manual_sim()
    with pytest.raises(ValueError):
        sim.jog("w", 1)
    with pytest.raises(ValueError):
        sim.set_mode("turbo")
