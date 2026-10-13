"""The gantry feed gate, end to end on the REAL components: the Pi's
StationCoordinator -> bus -> FeedClient -> FinalQueue -> JobRunner -> GantrySession
-> SimulatedMcu, all on one fake clock (no serial port, no sleeping).

Question these answer: when the Pi pauses the feed (a line stop does this), does a
gantry with a backlog really finish what it has accepted and then sit idle with an
empty cup - and does it pick the rest up in order on resume? And, for contrast,
what a hard pause (the runner disabled) does mid-carry, which is why the line stop
uses the feed gate instead.
"""

import pytest

from fusion.coordinator import OK, StationCoordinator
from fusion.store import TileStore
from fusion.transport import InMemoryBus
from pick_place.coordinate_model import CoordinateModel
from pick_place.fake_mcu import LoopbackLink, SimulatedMcu
from pick_place.gantry_session import GantrySession
from pick_place.job_runner import JobRunner
from pick_place.mock_state import load_config
from pick_place.sequencer import Feeds
from pick_place.tile_feed import FeedClient, FinalQueue

# Grade A -> bin_1, Grade B -> bin_2: names the gantry's coordinate model knows.
STATION_CFG = {
    "ledger": {"max_wait_s": 60.0, "min_transit_s": 0.0},
    "final": {"vote_enabled": False, "min_confidence": 0.6,
              "bins": {"Grade A": "bin_1", "Grade B": "bin_2", "Grade C": "bin_3", "Reject": "bin_4", "Hold": "bin_4"}},
    "unoq": {"require_ack": True, "ack_timeout_s": 2.0, "retries": 3, "max_outbox": 20},
    "health": {"camera_stale_s": 6.0},
    "heartbeat_s": 2.0,
}
GOOD_TAP = {"valid": True, "grade": None, "confidence": None, "peak_frequency_hz": 2800.0}


class Line:
    """Pi coordinator + gantry (queue, runner, simulated MCU) sharing one clock."""

    def __init__(self, station_cfg=None):
        self.t = 1000.0
        self.cfg = load_config()
        ws = self.cfg["workspace"]
        self.mcu = SimulatedMcu(
            envelope=((ws["x_min_mm"], ws["x_max_mm"]), (ws["y_min_mm"], ws["y_max_mm"]),
                      (ws["z_min_mm"], ws["z_max_mm"])),
            speed_mm_s=5000, honor_feed=False)
        self.session = GantrySession(LoopbackLink(self.mcu), clock=self._now)
        self.model = CoordinateModel.from_config(self.cfg)
        self.bus = InMemoryBus()
        self.feed = FeedClient(self.bus, queue=FinalQueue(), clock=self._now)
        self.queue = self.feed.queue
        self.enabled = True
        self.done_events = []            # (tile_id, ok, reason) exactly as the reporter would receive them
        self.runner = JobRunner(self.session, self.queue, self.model, Feeds.from_config(self.cfg), self.cfg,
                                clock=self._now, enabled=lambda: self.enabled,
                                on_job_done=lambda *a: self.done_events.append(a))
        self.store = TileStore(":memory:")
        self.pi = StationCoordinator(station_cfg or STATION_CFG, self.bus, self.store, clock=self._now)
        self.pi.start()
        self.feed.start()
        self.pi.set_health("mic", OK, "listening")
        self._n = 0

    def _now(self):
        return self.t

    # ---- driving ------------------------------------------------------------
    def run(self, seconds, dt=0.01):
        for _ in range(int(round(seconds / dt))):
            self.t += dt
            self.mcu.tick(dt)
            self.runner.tick()
            self.pi.tick(self.t)

    def run_until(self, cond, max_s=30.0, dt=0.01):
        for _ in range(int(max_s / dt)):
            if cond():
                return True
            self.run(dt, dt)
        return cond()

    def ready(self):
        self.run(0.5)
        assert self.runner.request_home()
        self.run(1.0)
        assert self.session.homed and self.runner.status()["phase"] == "idle", self.runner.status()
        return self

    # ---- the Pi side ---------------------------------------------------------
    def decide(self, grade="Grade A"):
        """One tile photographed and tapped: the Pi decides its final grade."""
        self._n += 1
        tid = f"T{self._n}"
        self.bus.publish(f"tile/{tid}/camera/result", {"tile_id": tid, "grade": grade, "seq": self._n})
        assert self.pi.on_tap(GOOD_TAP) == tid
        return tid

    def feed_cmd(self, state):
        self.bus.publish("station/cmd", {"cmd": "feed", "state": state})

    def moves(self):
        return sum(1 for ln in self.mcu.received if ln.split()[1:2] == ["MOVE"])

    def placed(self):
        return sum(self.model.counts().values())


@pytest.fixture
def line():
    return Line().ready()


def test_a_backlog_on_the_gantry_is_drained_and_it_idles_with_an_empty_cup(line):
    for _ in range(3):
        line.decide("Grade A" if _ % 2 == 0 else "Grade B")
    assert line.queue.snapshot()["depth"] == 3          # delivered and acknowledged; the gantry has not started
    assert line.run_until(lambda: line.runner.status()["active"] is not None, 5)   # it is mid-job...

    line.feed_cmd("pause")                              # ...when the line is stopped
    for _ in range(2):
        line.decide()                                   # decided while paused
    assert line.pi.snapshot()["outbox_depth"] == 2
    assert line.queue.snapshot()["depth"] + (1 if line.runner.status()["active"] else 0) == 3   # nothing new arrived

    line.run(40)
    s = line.runner.status()
    assert s["completed"] == 3 and s["failed"] == 0, s  # everything it had accepted was sorted
    assert s["active"] is None and s["phase"] == "idle" and s["holding"] is False, s
    assert line.placed() == 3 and line.queue.snapshot()["depth"] == 0
    assert line.pi.snapshot()["outbox_depth"] == 2      # the two held tiles are still on the Pi, undelivered

    moves = line.moves()
    line.run(10)
    assert line.moves() == moves                        # idle means idle: no further motion at all
    assert line.runner.status()["phase"] == "idle" and line.runner.status()["fault"] is None


def test_the_held_tiles_are_delivered_in_order_and_sorted_on_resume(line):
    line.feed_cmd("pause")
    ids = [line.decide("Grade A"), line.decide("Grade B"), line.decide("Grade A")]
    line.run(10)
    assert line.placed() == 0 and line.queue.snapshot()["depth"] == 0      # nothing reached the gantry

    line.feed_cmd("resume")
    line.run(60)
    s = line.runner.status()
    assert s["completed"] == 3 and s["active"] is None and s["holding"] is False, s
    assert line.model.counts()["bin_1"] == 2 and line.model.counts()["bin_2"] == 1
    assert line.pi.snapshot()["outbox_depth"] == 0
    assert [line.store.get(t)["delivery"] for t in ids] == ["acked"] * 3


def test_a_hard_pause_mid_carry_freezes_with_the_tile_on_the_cup_unlike_the_feed_gate(line):
    line.decide()
    assert line.run_until(lambda: line.runner.status()["holding"], 15)     # the cup has grabbed the tile
    line.enabled = False                                                   # what station/gantry/cmd pause does
    line.run(10)
    s = line.runner.status()
    assert s["holding"] is True and s["completed"] == 0 and s["paused"] is True, s
    assert line.placed() == 0                                              # it did NOT finish the job

    line.enabled = True                                                    # resume: it carries on and finishes
    line.run(40)
    s = line.runner.status()
    assert s["completed"] == 1 and s["holding"] is False and line.placed() == 1, s


def test_feed_pause_during_a_full_gantry_queue_loses_nothing(line):
    """Pause with several tiles still queued on the gantry AND some held on the Pi:
    after resume every tile is placed exactly once."""
    for _ in range(4):
        line.decide()
    line.feed_cmd("pause")
    for _ in range(3):
        line.decide("Grade B")
    line.run(30)
    line.feed_cmd("resume")
    line.run(80)
    s = line.runner.status()
    assert s["completed"] == 7 and s["failed"] == 0 and line.placed() == 7, s
    assert sorted(line.model.counts().items()) == [("bin_1", 4), ("bin_2", 3), ("bin_3", 0), ("bin_4", 0)]
    assert line.queue.snapshot()["counters"].get("duplicate", 0) == 0


def test_KNOWN_GAP_a_hold_tile_is_acked_then_fails_because_manual_is_not_a_gantry_location():
    """Characterization, not a statement of what SHOULD happen. The station config sends Hold to
    destination "manual" (documented as "put in the manual bin, not skip"), but the gantry has no
    location of that name. Observed with the real components: the gantry ACKs it, then refuses the
    job at planning time - no motion, `placed: failed`, one alarm per tile - so the tile is never
    taken off the belt. Flip this test when the intended behaviour is decided (see
    documents/programming/master_gui_handover.md, section 4)."""
    import copy

    cfg = copy.deepcopy(STATION_CFG)
    cfg["final"]["bins"]["Hold"] = "manual"          # what acoustic/config.yaml really says
    line = Line(cfg).ready()
    placed, alarms = [], []
    import json
    line.bus.subscribe("tile/+/placed", lambda t, p: placed.append(json.loads(p)))
    line.bus.subscribe("machine/alarm", lambda t, p: alarms.append(json.loads(p)))
    # no GantryReporter in this rig, so look at the runner itself:
    line.pi.set_hold_all(True)
    tid = line.decide("Grade A")
    line.pi.set_hold_all(False)
    after = line.decide("Grade B")                   # a normal tile right behind it still gets sorted
    moves_before = line.moves()
    line.run(30)

    assert line.store.get(tid)["final_grade"] == "Hold" and line.store.get(tid)["destination"] == "manual"
    assert line.store.get(tid)["delivery"] == "acked"           # the UNO Q said ok ...
    s = line.runner.status()
    assert s["failed"] == 1 and s["completed"] == 1, s          # ... then refused the job
    refused = [e for e in line.done_events if e[0] == tid]
    assert len(refused) == 1 and refused[0][1] is False, line.done_events
    assert "unknown destination 'manual'" in refused[0][2] and "bin_1" in refused[0][2], refused
    assert line.model.counts()["bin_2"] == 1                    # the tile behind it was sorted
    assert s["fault"] is None and s["active"] is None           # not a machine fault: it just carries on
    assert line.store.get(after)["final_grade"] == "Grade B"
    assert line.moves() > moves_before                          # (moves belong to the second tile only)
