"""The sim-backed demo line: the master's collector sees what the REAL station
coordinator + gantry runner (on the simulated MCU) actually publish.

Real time, so it is kept short: a fast simulated gantry and a tile every 0.5 s.
"""

import json
import time

import pytest

from master.bus import StreamingInMemoryBus
from master.collector import MasterCollector
from master.plant import build_plant
from master.sim_line import SimLine


def wait_for(cond, timeout_s, step=0.1):
    end = time.time() + timeout_s
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return cond()


@pytest.fixture
def sim():
    bus, col = StreamingInMemoryBus(), MasterCollector()
    col.attach(bus)
    placed = []
    bus.subscribe("tile/+/placed", lambda t, p: placed.append(json.loads(p)))
    line = SimLine(bus, interval_s=0.5, transit_s=0.2, gantry_speed_mm_s=4000)
    line.start()
    yield line, bus, col, placed
    line.stop()


def test_real_components_sort_tiles_end_to_end(sim):
    line, bus, col, placed = sim
    assert wait_for(lambda: len(placed) >= 2, 30), (col.gantry_status, col.recent(5))
    assert all(p.get("outcome") == "placed" for p in placed)
    rows = col.recent(50)
    finals = [r for r in rows if r["final_grade"]]
    assert finals and all(r["destination"] in ("bin_1", "bin_2", "bin_3", "bin_4", "manual") for r in finals)
    # the gantry's own retained status is published on change / every status_interval_s
    assert wait_for(lambda: (col.gantry_status or {}).get("completed", 0) >= 2, 15), col.gantry_status
    assert col.gantry_status.get("homed") is True


def test_the_plant_model_reads_the_real_gantry_status(sim):
    line, bus, col, placed = sim
    assert wait_for(lambda: col.gantry_status is not None and col.station_status is not None, 20)
    p = build_plant(col.snapshot(), time.time())
    by = {s["id"]: s for s in p["stations"]}
    assert by["gantry"]["level"] in ("ok", "warn")
    assert p["line"]["state"] in ("waiting", "awaiting_tap", "sent")


def test_line_stop_pauses_the_belt_the_striker_and_the_feed_for_real(sim):
    line, bus, col, placed = sim
    assert wait_for(lambda: len(placed) >= 1, 30)
    bus.publish("station/cmd", {"cmd": "line", "state": "stop"})
    assert wait_for(lambda: line.pi.snapshot()["settings"]["line"] == "stopped", 5)
    assert line.belt_pct == 0 and line.strike_enabled is False and line.pi.feed_paused is True
    n = len(placed)
    # after the gantry drains what it had, nothing new reaches it while stopped
    time.sleep(6)
    settled = len(placed)
    time.sleep(3)
    assert len(placed) == settled and settled >= n
    snap = line.pi.snapshot()
    assert snap["state"] == "line_stopped"

    bus.publish("station/cmd", {"cmd": "line", "state": "start"})
    assert wait_for(lambda: line.belt_pct > 0 and not line.pi.feed_paused, 5)
    assert wait_for(lambda: len(placed) > settled, 30)         # sorting continues after the start
