"""JobRunner against the line-level SimulatedMcu, on a fake clock: the whole real
path (session -> protocol -> MCU rules) with no serial port and no sleeping."""

import pytest

from pick_place.coordinate_model import CoordinateModel
from pick_place.fake_mcu import LoopbackLink, SimulatedMcu
from pick_place.gantry_session import GantrySession
from pick_place.job_runner import JobRunner
from pick_place.mock_state import load_config
from pick_place.sequencer import Feeds
from pick_place.tile_feed import FinalQueue


class Rig:
    def __init__(self, enabled=True, **mcu_kw):
        self.cfg = load_config()
        ws = self.cfg["workspace"]
        self.mcu = SimulatedMcu(envelope=((ws["x_min_mm"], ws["x_max_mm"]), (ws["y_min_mm"], ws["y_max_mm"]),
                                          (ws["z_min_mm"], ws["z_max_mm"])), speed_mm_s=5000,
                           honor_feed=False, **mcu_kw)
        self.t = 0.0
        self.session = GantrySession(LoopbackLink(self.mcu), clock=lambda: self.t)
        self.queue = FinalQueue()
        self.model = CoordinateModel.from_config(self.cfg)
        self.enabled = enabled
        self.done_events = []
        self.runner = JobRunner(self.session, self.queue, self.model, Feeds.from_config(self.cfg), self.cfg,
                                clock=lambda: self.t, enabled=lambda: self.enabled,
                                on_job_done=lambda *a: self.done_events.append(a))

    def run(self, seconds, dt=0.01):
        for _ in range(int(seconds / dt)):
            self.t += dt
            self.mcu.tick(dt)
            self.runner.tick()

    def ready(self):
        """Pushed config + homed, idle."""
        self.run(0.5)
        assert self.runner.request_home()
        self.run(1.0)
        assert self.session.homed and self.runner.status()["phase"] == "idle", self.runner.status()
        return self

    def add(self, tile_id="T1", dest="bin_2", **kw):
        self.queue.accept({"tile_id": tile_id, "final_grade": "Grade B", "destination": dest, "attempt": 1, **kw}, 0)


def verbs(rig):
    return [line.split()[1] for line in rig.mcu.received]


# ---- startup / homing -------------------------------------------------------------


def test_startup_pushes_config_and_waits_for_an_explicit_home():
    r = Rig()
    r.run(0.5)
    assert verbs(r)[:2] == ["SETENV", "SETSPEED"] and "SETTOL" in verbs(r)
    r.add()
    r.run(2.0)
    assert r.queue.snapshot()["depth"] == 1 and "MOVE" not in verbs(r)  # not homed: nothing moves
    assert r.runner.status()["homed"] is False


def test_home_then_job_runs_to_the_stack():
    r = Rig().ready()
    r.add("T1", "bin_3")
    r.run(5.0)
    s = r.runner.status()
    assert s["completed"] == 1 and s["failed"] == 0 and s["active"] is None and not s["holding"]
    assert r.model.count("bin_3") == 1
    assert r.queue.snapshot()["counters"]["done"] == 1
    assert r.done_events == [("T1", True, None)]


def test_commands_are_sent_one_at_a_time_in_plan_order():
    r = Rig().ready()
    before = len(r.mcu.received)
    r.add("T1", "bin_1")
    r.run(5.0)
    sent = [ln.split(maxsplit=1)[1] for ln in r.mcu.received[before:] if not ln.endswith(" PING")]
    assert [c.split()[0] for c in sent] == ["MOVE", "MOVE", "MOVE", "VAC", "MOVE", "MOVE", "MOVE", "VAC", "MOVE"]
    assert "VAC ON" in sent and sent.index("VAC ON") < sent.index("VAC OFF")


def test_tiles_in_order_and_stack_heights_climb():
    r = Rig().ready()
    for i, dest in enumerate(["bin_1", "bin_1", "bin_4"]):
        r.add(f"T{i}", dest)
    r.run(15.0)
    assert r.model.counts() == {"bin_1": 2, "bin_2": 0, "bin_3": 0, "bin_4": 1}
    second_release = [ln for ln in r.mcu.received if ln.endswith("Z32.0 F2000.0")]
    assert len(second_release) == 1  # the second tile on bin_1 releases 12 mm higher (20 + 12)


# ---- vacuum miss ----------------------------------------------------------------------


def test_one_vacuum_miss_is_retried_and_the_job_still_completes():
    r = Rig().ready()
    r.mcu.vacuum_failures = 1
    r.add("T1", "bin_2")
    r.run(8.0)
    s = r.runner.status()
    assert s["completed"] == 1 and s["failed"] == 0
    assert verbs(r).count("VAC") == 3 and r.model.count("bin_2") == 1  # ON (miss), ON, OFF
    descents = [ln for ln in r.mcu.received if ln.endswith("MOVE Z20.0 F2000.0")]
    assert len(descents) == 3  # pick descent, its retry, then the place descent


def test_two_vacuum_misses_fail_the_job_drop_the_pump_and_raise():
    r = Rig().ready()
    r.mcu.vacuum_failures = 2
    r.add("T1", "bin_2")
    r.run(8.0)
    s = r.runner.status()
    assert s["failed"] == 1 and s["completed"] == 0 and r.model.count("bin_2") == 0
    assert r.mcu.vac is False and r.mcu.pos["Z"] == pytest.approx(200.0)  # parked at safe Z
    assert "VACUUM_TIMEOUT" in s["last_error"]
    assert s["phase"] == "idle"  # nothing in the cup, so no operator needed
    assert r.queue.snapshot()["counters"]["failed"] == 1


# ---- alarms -------------------------------------------------------------------------------


def test_an_alarm_aborts_the_job_and_sends_nothing_further():
    r = Rig().ready()
    r.add("T1", "bin_2")
    r.run(0.12)
    assert r.runner.status()["active"] is not None
    r.mcu.inject_alarm("LIMIT_X_MAX")
    sent_at_alarm = len(r.mcu.received)
    r.run(2.0)
    s = r.runner.status()
    assert s["failed"] == 1 and s["alarm"] == "LIMIT_X_MAX" and s["phase"] == "fault"
    assert len(r.mcu.received) == sent_at_alarm  # not even a PING
    assert r.model.count("bin_2") == 0


def test_closed_loop_lost_steps_raise_an_alarm_that_stops_the_job():
    r = Rig().ready()
    r.add("T1", "bin_2")
    r.run(0.12)
    r.mcu.enc_offset["X"] = 2.5  # beyond the 1.0 mm LOST tolerance pushed by SETTOL
    r.run(1.0)
    s = r.runner.status()
    assert s["alarm"] == "LOST_STEPS_X" and s["failed"] == 1 and s["homed"] is False
    assert s["deviation_mm"]["X"] == pytest.approx(2.5)


def test_y_vs_a_skew_raises_skew_alarm():
    r = Rig().ready()
    r.mcu.enc_offset["A"] = 1.0  # above the 0.5 mm skew tolerance, below the 1.0 lost-steps one
    r.run(0.5)
    assert r.runner.status()["alarm"] == "SKEW_Y"


def test_recover_clears_the_alarm_and_rehomes_only_when_asked():
    r = Rig().ready()
    r.mcu.inject_alarm("ESTOP")
    r.run(0.5)
    assert r.runner.status()["phase"] == "fault"
    assert r.runner.acknowledge() is False  # cannot acknowledge past a live alarm
    assert r.runner.request_home() is False
    assert r.runner.recover()
    r.run(2.0)
    s = r.runner.status()
    assert s["alarm"] is None and s["homed"] is True and s["phase"] == "idle"
    r.add("T1", "bin_1")
    r.run(5.0)
    assert r.runner.status()["completed"] == 1


# ---- silence is a fault -----------------------------------------------------------------------


def test_a_silent_mcu_stops_the_job_and_holds():
    r = Rig().ready()
    r.add("T1", "bin_2")
    r.run(0.05)
    r.mcu.write = lambda line: r.mcu.received.append(line)  # accept bytes, never answer
    r.run(5.0)
    s = r.runner.status()
    assert s["failed"] == 1 and s["phase"] == "fault" and "no answer" in s["fault"]
    assert r.model.count("bin_2") == 0


def test_idle_ping_is_sent_and_a_dead_link_is_noticed():
    r = Rig().ready()
    pings = verbs(r).count("PING")
    r.run(3.0)
    assert verbs(r).count("PING") > pings
    r.mcu.write = lambda line: r.mcu.received.append(line)
    r.run(5.0)
    assert r.runner.status()["phase"] == "fault" and "silent" in r.runner.status()["fault"]


# ---- refusals / pause -------------------------------------------------------------------------


def test_unknown_destination_is_refused_with_no_motion():
    r = Rig().ready()
    before = verbs(r).count("MOVE")
    r.add("T1", "manual")
    r.run(2.0)
    s = r.runner.status()
    assert s["failed"] == 1 and "manual" in s["last_error"] and verbs(r).count("MOVE") == before


def test_a_jog_pause_finishes_the_inflight_command_and_resumes_via_safe_z():
    r = Rig().ready()
    r.add("T1", "bin_2")
    r.run(0.08)
    r.enabled = False  # a jog click
    r.run(1.0)
    n_paused = len(r.mcu.received)
    r.run(1.0)
    assert len(r.mcu.received) == n_paused  # paused: nothing new (pings are for the idle runner only)
    assert r.runner.status()["paused"]
    r.mcu.pos["Z"] = 50.0  # a manual jog left the cup low
    r.enabled = True
    r.run(8.0)
    assert r.runner.status()["completed"] == 1 and r.model.count("bin_2") == 1
    resumed = r.mcu.received[n_paused:]
    assert resumed[0].split(maxsplit=1)[1] == "MOVE Z200.0 F4000.0"  # rises first


def test_not_enabled_means_no_job_starts():
    r = Rig(enabled=False).ready()
    r.add("T1", "bin_1")
    r.run(2.0)
    assert r.queue.snapshot()["depth"] == 1 and r.runner.status()["paused"]


def test_acknowledge_clears_a_fault_once_no_alarm():
    r = Rig().ready()
    r.mcu.write = lambda line: r.mcu.received.append(line)
    r.run(5.0)
    assert r.runner.status()["phase"] == "fault"
    assert r.runner.acknowledge() is True
    assert r.runner.status()["phase"] == "idle"
