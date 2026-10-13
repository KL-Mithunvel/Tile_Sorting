"""The gantry's outbound half of the station contract: placed / status / alarms /
remote commands, driven by the real JobRunner against the simulated MCU, and then
consumed by the real StationCoordinator (the two nodes never import each other;
this test does)."""

import json

from fusion.coordinator import FAIL, OK, WARN, StationCoordinator
from fusion.store import TileStore
from fusion.transport import InMemoryBus
from pick_place.coordinate_model import CoordinateModel
from pick_place.fake_mcu import LoopbackLink, SimulatedMcu
from pick_place.gantry_session import GantrySession
from pick_place.gantry_backend import SerialGantryBackend
from pick_place.job_runner import JobRunner
from pick_place.mock_state import load_config
from pick_place.sequencer import Feeds
from pick_place.station_link import GantryCommands, GantryReporter
from pick_place.tile_feed import FeedClient, FinalQueue


class Rig:
    def __init__(self, motion_commands=False, commands=True, vacuum_failures=0, **mcu_kw):
        self.cfg = load_config()
        ws = self.cfg["workspace"]
        self.mcu = SimulatedMcu(envelope=((ws["x_min_mm"], ws["x_max_mm"]), (ws["y_min_mm"], ws["y_max_mm"]),
                                          (ws["z_min_mm"], ws["z_max_mm"])), speed_mm_s=5000,
                                honor_feed=False, **mcu_kw)
        self.mcu.vacuum_failures = vacuum_failures
        self.t = 0.0
        self.bus = InMemoryBus()
        self.session = GantrySession(LoopbackLink(self.mcu), clock=lambda: self.t)
        self.backend = SerialGantryBackend(self.session, initial_mode="jobs")
        self.queue = FinalQueue()
        self.model = CoordinateModel.from_config(self.cfg)
        self.reporter = None
        self.runner = JobRunner(self.session, self.queue, self.model, Feeds.from_config(self.cfg), self.cfg,
                                clock=lambda: self.t, enabled=lambda: self.backend.mode == "jobs",
                                on_job_done=lambda *a: self.reporter.on_job_done(*a))
        self.reporter = GantryReporter(self.bus, self.runner, self.model, self.queue, backend=self.backend,
                                       backend_name="sim", clock=lambda: self.t, status_interval_s=2.0)
        self.commands = GantryCommands(self.bus, self.runner, self.backend, self.model, self.reporter,
                                       commands_enabled=commands, motion_commands_enabled=motion_commands,
                                       clock=lambda: self.t)
        self.commands.attach()

    def run(self, seconds, dt=0.01):
        for _ in range(int(seconds / dt)):
            self.t += dt
            self.mcu.tick(dt)
            self.runner.tick()
            self.reporter.tick()

    def ready(self):
        self.run(0.5)
        assert self.runner.request_home()
        self.run(1.0)
        assert self.session.homed
        return self

    def add(self, tile_id="T1", dest="bin_2"):
        self.queue.accept({"tile_id": tile_id, "final_grade": "Grade B", "destination": dest, "attempt": 1}, 0)

    def cmd(self, name, **kw):
        self.bus.publish("station/gantry/cmd", {"id": f"c-{len(self.bus.published)}", "cmd": name, **kw})
        self.run(0.05)
        return self.bus.messages("station/gantry/cmd/result")[-1]


# ---- the per-tile outcome ---------------------------------------------------------


def test_a_placed_tile_is_reported_with_where_and_how_high():
    r = Rig().ready()
    r.add("T1", "bin_2")
    r.run(5.0)
    (placed,) = r.bus.messages("tile/T1/placed")
    assert placed["outcome"] == "placed" and placed["destination"] == "bin_2" and placed["source"] == "pickup"
    assert placed["stack_count"] == 1 and placed["tile_in_cup"] is False and placed["reason"] is None
    assert placed["grade"] == "Grade B" and placed["station"] == "gantry"
    # QoS 1, not retained: it is an event, not a state
    topic, _, qos, retain = next(p for p in r.bus.published if p[0] == "tile/T1/placed")
    assert qos == 1 and retain is False


def test_two_tiles_two_reports_in_order_with_counts_rising():
    r = Rig().ready()
    r.add("T1", "bin_1")
    r.add("T2", "bin_1")
    r.run(10.0)
    reports = [m for m in r.bus.messages("tile/+/placed")]
    assert [(m["tile_id"], m["stack_count"]) for m in reports] == [("T1", 1), ("T2", 2)]


def test_a_missed_grip_is_a_failed_report_with_nothing_on_the_cup():
    r = Rig(vacuum_failures=2).ready()
    r.add("T1")
    r.run(8.0)
    (m,) = r.bus.messages("tile/T1/placed")
    assert m["outcome"] == "failed" and "VACUUM_TIMEOUT" in m["reason"] and m["tile_in_cup"] is False
    assert m["stack_count"] is None
    alarms = [a for a in r.bus.messages("machine/alarm") if a["kind"] == "gantry_pick_failed"]
    assert len(alarms) == 1 and alarms[0]["tile_id"] == "T1" and "cup" not in alarms[0]["message"]


def test_a_plan_the_gantry_refuses_is_reported_failed_not_swallowed():
    r = Rig().ready()
    r.add("T1", "no_such_stack")
    r.run(1.0)
    (m,) = r.bus.messages("tile/T1/placed")
    assert m["outcome"] == "failed" and "no_such_stack" in m["reason"]


def test_a_failure_with_the_tile_on_the_cup_says_so():
    r = Rig().ready()
    r.add("T1")
    # let the grip happen, then fail the machine while the tile hangs on the cup
    for _ in range(2000):
        r.run(0.01)
        if r.runner.holding:
            break
    assert r.runner.holding
    r.mcu.inject_alarm("LOST_STEPS_X")
    r.run(0.5)
    (m,) = r.bus.messages("tile/T1/placed")
    assert m["outcome"] == "failed" and m["tile_in_cup"] is True
    assert any("cup" in a["message"] for a in r.bus.messages("machine/alarm"))


# ---- status + alarms --------------------------------------------------------------------


def test_status_is_retained_qos0_and_describes_the_machine():
    r = Rig().ready()
    r.run(2.5)
    topic, data, qos, retain = [p for p in r.bus.published if p[0] == "station/gantry/status"][-1]
    s = json.loads(data)
    assert qos == 0 and retain is True
    assert s["state"] == "online" and s["level"] == "ok" and s["homed"] is True and s["backend"] == "sim"
    assert s["queue_depth"] == 0 and set(s["stacks"]) == {"bin_1", "bin_2", "bin_3", "bin_4"}


def test_status_is_not_republished_every_tick_but_changes_go_out_at_once():
    r = Rig().ready()
    r.run(2.5)
    n = len(r.bus.messages("station/gantry/status"))
    r.run(0.5)  # nothing changed, interval not due
    assert len(r.bus.messages("station/gantry/status")) == n
    r.add("T1")
    r.run(0.05)  # queue depth / active tile changed
    assert len(r.bus.messages("station/gantry/status")) > n


def test_an_alarm_is_raised_once_per_event_and_shows_in_the_status():
    r = Rig().ready()
    r.mcu.inject_alarm("ESTOP")
    r.run(0.5)
    kinds = [a["kind"] for a in r.bus.messages("machine/alarm")]
    assert kinds.count("gantry_alarm") == 1
    s = r.bus.messages("station/gantry/status")[-1]
    assert s["level"] == "fail" and s["alarm"] == "ESTOP"
    r.run(2.0)  # still latched: no second alarm message
    assert [a["kind"] for a in r.bus.messages("machine/alarm")].count("gantry_alarm") == 1


def test_a_cleared_alarm_can_be_raised_again():
    r = Rig(motion_commands=True).ready()
    r.mcu.inject_alarm("ESTOP")
    r.run(0.5)
    assert r.cmd("recover")["ok"]
    r.run(2.0)
    assert r.reporter.snapshot()["alarm"] is None
    r.mcu.inject_alarm("LIMIT_X_MIN")
    r.run(0.5)
    assert [a["kind"] for a in r.bus.messages("machine/alarm")].count("gantry_alarm") == 2


def test_reports_survive_a_broker_outage_and_status_is_not_replayed():
    r = Rig().ready()
    r.add("T1")
    r.bus.link_up = False
    r.run(6.0)  # the job completes while the broker is unreachable
    assert r.bus.messages("tile/T1/placed") == [] and r.reporter.pending >= 1
    r.bus.link_up = True
    r.run(0.1)
    assert len(r.bus.messages("tile/T1/placed")) == 1 and r.reporter.pending == 0


def test_a_bus_that_raises_cannot_stop_the_gantry():
    r = Rig().ready()
    r.bus.publish = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("broker exploded"))
    r.add("T1")
    r.run(6.0)
    assert r.runner.status()["completed"] == 1


# ---- remote commands -----------------------------------------------------------------------


def test_pause_and_resume_gate_the_job_runner():
    r = Rig().ready()
    assert r.cmd("pause")["ok"] and r.backend.mode == "manual"
    r.add("T1")
    r.run(2.0)
    assert r.bus.messages("tile/T1/placed") == []  # paused: nothing picked
    assert r.reporter.snapshot()["paused"] is True
    assert r.cmd("resume")["ok"]
    r.run(6.0)
    assert len(r.bus.messages("tile/T1/placed")) == 1


def test_reset_stack_empties_it_and_bad_input_is_refused():
    r = Rig().ready()
    r.add("T1", "bin_1")
    r.run(5.0)
    assert r.model.count("bin_1") == 1
    assert r.cmd("reset_stack", stack="bin_1")["ok"] and r.model.count("bin_1") == 0
    bad = r.cmd("reset_stack", stack="nope")
    assert bad["ok"] is False and "nope" in bad["reason"]
    assert r.cmd("reset_stack")["ok"] is False
    assert r.cmd("reset_stack", stack="bin_1", count=999)["ok"] is False


def test_motion_commands_are_refused_unless_enabled():
    r = Rig().ready()
    res = r.cmd("home")
    assert res["ok"] is False and "motion_commands_enabled" in res["reason"]
    assert r.cmd("recover")["ok"] is False


def test_home_and_recover_work_when_motion_commands_are_enabled():
    r = Rig(motion_commands=True)
    r.run(0.5)
    assert r.cmd("home")["ok"]
    r.run(1.0)
    assert r.session.homed
    r.mcu.inject_alarm("LOST_STEPS_Z")
    r.run(0.5)
    assert r.cmd("recover")["ok"]
    r.run(2.0)
    assert r.session.homed and r.reporter.snapshot()["alarm"] is None


def test_commands_can_be_switched_off_entirely():
    r = Rig(commands=False).ready()
    res = r.cmd("pause")
    assert res["ok"] is False and r.backend.mode == "jobs"


def test_unreadable_and_unknown_commands_get_a_refusal_not_silence():
    r = Rig().ready()
    r.bus.publish("station/gantry/cmd", b"{nope")
    assert r.bus.messages("station/gantry/cmd/result")[-1]["ok"] is False
    assert "unknown" in r.cmd("self_destruct")["reason"]


def test_acknowledge_is_refused_when_nothing_is_held():
    r = Rig().ready()
    res = r.cmd("acknowledge")
    assert res["ok"] is False


# ---- consumed by the real coordinator -----------------------------------------------------------


def station_rig(clock, **cfg_extra):
    bus, store = InMemoryBus(), TileStore(":memory:")
    cfg = {"ledger": {"max_wait_s": 30}, "unoq": {"require_ack": True, "ack_timeout_s": 2.0, "retries": 3,
                                                 "placed_timeout_s": 60.0},
           "final": {"vote_enabled": False, "bins": {"Grade B": "bin_2", "Hold": "manual"}}, **cfg_extra}
    c = StationCoordinator(cfg, bus, store, clock=clock)
    c.start()
    c.set_health("mic", OK)
    return bus, store, c


def test_the_station_records_what_the_gantry_reports():
    clock = [1000.0]
    bus, store, c = station_rig(lambda: clock[0])
    # a real gantry node on the SAME bus: finals in, acks + placed out
    r = Rig()
    r.bus = bus
    r.reporter._bus = bus
    r.commands._bus = bus
    r.commands.attach()
    feed = FeedClient(bus, queue=r.queue, clock=lambda: r.t)
    feed.start()
    r.ready()

    bus.publish("tile/T1/camera/result", {"tile_id": "T1", "grade": "Grade B", "seq": 1})
    assert c.on_tap({"valid": True, "grade": None, "confidence": None}) == "T1"
    r.run(6.0)
    row = store.get("T1")
    assert row["delivery"] == "acked" and row["placement"] == "placed" and row["placed_ts"] is not None
    assert row["tile_in_cup"] is False
    assert c.snapshot()["counters"]["placed"] == 1
    assert c.snapshot()["awaiting_placement"] == 0
    assert c.snapshot()["health"]["gantry"]["level"] == OK
    assert store.placement_tally() == {"placed": 1}


def test_a_failed_pick_is_recorded_and_a_gantry_alarm_makes_the_station_fault():
    clock = [1000.0]
    bus, store, c = station_rig(lambda: clock[0])
    bus.publish("tile/T1/camera/result", {"tile_id": "T1", "grade": "Grade B", "seq": 1})
    c.on_tap({"valid": True, "grade": None, "confidence": None})
    bus.publish("tile/T1/placed", {"tile_id": "T1", "outcome": "failed", "reason": "grip: VACUUM_TIMEOUT",
                                   "tile_in_cup": False})
    row = store.get("T1")
    assert row["placement"] == "failed" and row["placement_reason"] == "grip: VACUUM_TIMEOUT"
    assert c.snapshot()["counters"]["pick_failed"] == 1
    bus.publish("station/gantry/status", {"state": "online", "level": "fail", "alarm": "LOST_STEPS_X"})
    snap = c.snapshot()
    assert snap["health"]["gantry"]["level"] == FAIL and "LOST_STEPS_X" in snap["health"]["gantry"]["detail"]
    assert snap["state"] == "fault"


def test_a_tile_acknowledged_but_never_reported_is_flagged_once():
    clock = [1000.0]
    bus, store, c = station_rig(lambda: clock[0])
    bus.publish("tile/T1/camera/result", {"tile_id": "T1", "grade": "Grade B", "seq": 1})
    c.on_tap({"valid": True, "grade": None, "confidence": None})
    bus.publish("tile/T1/ack", {"status": "ok"})
    assert c.snapshot()["awaiting_placement"] == 1
    clock[0] += 59
    c.tick()
    assert not [a for a in bus.messages("machine/alarm") if a["kind"] == "not_placed"]
    clock[0] += 2
    c.tick()
    c.tick()
    alarms = [a for a in bus.messages("machine/alarm") if a["kind"] == "not_placed"]
    assert len(alarms) == 1 and alarms[0]["tile_id"] == "T1"
    assert store.get("T1")["placement"] == "overdue"
    # a late report still lands and corrects the row
    bus.publish("tile/T1/placed", {"tile_id": "T1", "outcome": "placed"})
    assert store.get("T1")["placement"] == "placed"


def test_placement_check_can_be_turned_off_for_bench_runs():
    clock = [1000.0]
    bus, store, c = station_rig(lambda: clock[0], unoq={"require_ack": True, "placed_timeout_s": 0})
    bus.publish("tile/T1/camera/result", {"tile_id": "T1", "grade": "Grade B", "seq": 1})
    c.on_tap({"valid": True, "grade": None, "confidence": None})
    bus.publish("tile/T1/ack", {"status": "ok"})
    clock[0] += 10_000
    c.tick()
    assert c.snapshot()["awaiting_placement"] == 0
    assert not [a for a in bus.messages("machine/alarm") if a["kind"] == "not_placed"]


def test_gantry_health_levels():
    clock = [1000.0]
    bus, store, c = station_rig(lambda: clock[0])
    assert c.snapshot()["health"]["gantry"]["level"] == WARN  # not required, nothing heard
    bus.publish("station/gantry/status", {"state": "online", "level": "warn", "paused": True})
    assert c.snapshot()["health"]["gantry"]["detail"] == "paused"
    bus.publish("station/gantry/status", {"state": "offline"})
    assert c.snapshot()["health"]["gantry"]["level"] == WARN
    clock[0] += 30
    assert "silent" in c.snapshot()["health"]["gantry"]["detail"]


def test_gantry_required_turns_a_missing_gantry_into_a_fault():
    clock = [1000.0]
    bus, store, c = station_rig(lambda: clock[0], gantry={"required": True})
    assert c.snapshot()["health"]["gantry"]["level"] == FAIL
    bus.publish("station/gantry/status", {"state": "online", "level": "ok", "completed": 3, "queue_depth": 1})
    assert c.snapshot()["health"]["gantry"]["level"] == OK
