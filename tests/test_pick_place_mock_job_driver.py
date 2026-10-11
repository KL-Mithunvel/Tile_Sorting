"""The mock job driver: queued tiles -> real plans -> simulated motion -> real stack counts."""

import pytest

from pick_place.coordinate_model import CoordinateModel
from pick_place.mock_job_driver import MockJobDriver
from pick_place.mock_state import MockGantrySimulator, build_default_patrol, load_config
from pick_place.sequencer import Feeds
from pick_place.tile_feed import FinalQueue


@pytest.fixture
def rig():
    cfg = load_config()
    sim = MockGantrySimulator(build_default_patrol(cfg), travel_speed_mm_s=2000, workspace=cfg["workspace"])
    sim.set_mode("jobs")
    queue = FinalQueue()
    model = CoordinateModel.from_config(cfg)
    driver = MockJobDriver(sim, queue, model, Feeds.from_config(cfg), dwell_s=0.05)
    return sim, queue, model, driver


def run(sim, driver, seconds=30.0, dt=0.02):
    t = 0.0
    while t < seconds:
        driver.tick(sim.advance(dt), dt)
        t += dt


def final(tile_id, dest="bin_2", **kw):
    return {"tile_id": tile_id, "final_grade": "Grade B", "destination": dest, "attempt": 1, **kw}


def test_a_queued_tile_ends_up_on_its_stack(rig):
    sim, queue, model, driver = rig
    queue.accept(final("T1", "bin_2"), 0)
    run(sim, driver)
    assert model.count("bin_2") == 1 and model.count("bin_1") == 0
    s = driver.status()
    assert s["completed"] == 1 and s["active"] is None and not s["holding"]
    assert queue.snapshot()["counters"]["done"] == 1


def test_tiles_are_handled_in_order_and_stack_up(rig):
    sim, queue, model, driver = rig
    for i, dest in enumerate(["bin_1", "bin_1", "bin_4"]):
        queue.accept(final(f"T{i}", dest), i)
    run(sim, driver, 60)
    assert model.counts() == {"bin_1": 2, "bin_2": 0, "bin_3": 0, "bin_4": 1}
    assert driver.status()["completed"] == 3


def test_holding_is_true_only_between_grip_and_release(rig):
    sim, queue, model, driver = rig
    queue.accept(final("T1"), 0)
    seen = set()
    for _ in range(3000):
        driver.tick(sim.advance(0.02), 0.02)
        a = driver.status()
        if a["active"]:
            seen.add((a["active"]["step_label"], a["holding"]))
    assert ("descend_pick", False) in seen
    assert ("lift", True) in seen and ("descend_place", True) in seen
    assert ("retract", False) in seen


def test_unknown_destination_fails_the_job_and_moves_nothing(rig):
    sim, queue, model, driver = rig
    queue.accept(final("T1", "manual"), 0)
    run(sim, driver, 2)
    s = driver.status()
    assert s["failed"] == 1 and s["completed"] == 0 and "manual" in s["last_error"]
    assert sum(model.counts().values()) == 0
    assert queue.snapshot()["counters"]["failed"] == 1


def test_full_stack_fails_the_job(rig):
    sim, queue, model, driver = rig
    model.reset_stack("bin_3", 10)
    queue.accept(final("T1", "bin_3"), 0)
    run(sim, driver, 2)
    assert driver.status()["failed"] == 1 and model.count("bin_3") == 10


def test_a_jog_pauses_the_job_and_jobs_mode_resumes_it(rig):
    sim, queue, model, driver = rig
    queue.accept(final("T1"), 0)
    run(sim, driver, 1.0)
    assert driver.status()["active"] is not None
    sim.jog("x", 5)  # -> manual
    run(sim, driver, 5.0)
    assert driver.status()["paused"] and model.count("bin_2") == 0
    sim.set_mode("jobs")
    run(sim, driver, 40.0)
    assert model.count("bin_2") == 1 and driver.status()["completed"] == 1


def test_nothing_happens_outside_jobs_mode(rig):
    sim, queue, model, driver = rig
    sim.set_mode("auto")
    queue.accept(final("T1"), 0)
    run(sim, driver, 5)
    assert queue.snapshot()["depth"] == 1 and sum(model.counts().values()) == 0
