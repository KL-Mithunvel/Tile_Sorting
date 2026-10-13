"""The headless gantry service wiring (`attach_station_link`) end to end: a real
coordinator on one side of an in-memory bus, the simulated-MCU gantry on the
other, no sleeping. Also the pieces main() adds around it."""

import json

from fusion.coordinator import OK, StationCoordinator
from fusion.store import TileStore
from fusion.transport import InMemoryBus
from pick_place.coordinate_model import CoordinateModel
from pick_place.gantry_backend import create_backend
from pick_place.gantry_station import attach_station_link, station_settings
from pick_place.mock_state import load_config
from pick_place.sequencer import Feeds


class WillBus(InMemoryBus):
    """An InMemoryBus that records the last-will / connect hooks like PahoFeedBus."""

    def __init__(self):
        super().__init__()
        self.will = None
        self.on_connect_cbs = []

    def set_will(self, topic, payload, retain=True):
        self.will = (topic, payload, retain)

    def on_connect(self, cb):
        self.on_connect_cbs.append(cb)


class Sim:
    """Backend + fake clock, advanced explicitly: no real time passes."""

    def __init__(self, kind="sim", patch=None):
        from pick_place.fake_mcu import LoopbackLink, SimulatedMcu
        from pick_place.gantry_backend import SerialGantryBackend
        from pick_place.gantry_session import GantrySession

        self.cfg = load_config()
        self.cfg["backend"] = kind
        for k, v in (patch or {}).items():
            self.cfg["gantry"]["station"][k] = v
        self.t = 1000.0
        self.mcu = None
        if kind == "sim":
            ws = self.cfg["workspace"]
            self.mcu = SimulatedMcu(envelope=((ws["x_min_mm"], ws["x_max_mm"]), (ws["y_min_mm"], ws["y_max_mm"]),
                                              (ws["z_min_mm"], ws["z_max_mm"])), speed_mm_s=5000, honor_feed=False)
            self.backend = SerialGantryBackend(GantrySession(LoopbackLink(self.mcu), clock=lambda: self.t))
        else:
            self.backend = create_backend(self.cfg)
        self.bus = WillBus()
        self.model = CoordinateModel.from_config(self.cfg)
        self.link = attach_station_link(self.cfg, self.bus, self.backend, self.model, Feeds.from_config(self.cfg),
                                        kind, clock=lambda: self.t)
        # the driver's own clock (JobRunner defaults to time.monotonic): replace with ours
        if hasattr(self.link.driver, "_clock"):
            self.link.driver._clock = lambda: self.t

    def run(self, seconds, dt=0.02, hook=None):
        for _ in range(int(seconds / dt)):
            self.t += dt
            if self.mcu is not None:
                self.mcu.tick(dt)
            pose = self.backend.advance(dt)
            self.link.driver.tick(pose, dt)
            (hook or self.link.reporter.tick)()


def test_settings_defaults_are_safe():
    st = station_settings(load_config())
    assert st["commands_enabled"] is True and st["motion_commands_enabled"] is False
    assert st["home_on_start"] is False and st["port"] == 1883


def test_a_last_will_and_a_reconnect_hook_are_registered():
    sim = Sim()
    bus = sim.bus
    topic, payload, retain = bus.will
    assert topic == "station/gantry/status" and payload["state"] == "offline" and retain is True
    assert len(bus.on_connect_cbs) == 1
    bus.on_connect_cbs[0]()  # a reconnect re-announces the gantry
    assert bus.messages("station/gantry/status")[-1]["state"] == "online"


def test_a_final_in_a_placed_report_out_on_the_simulated_gantry():
    sim = Sim()
    bus = sim.bus
    sim.run(0.5)
    assert sim.link.driver.request_home()
    sim.run(1.5)
    bus.publish("tile/T1/final", {"tile_id": "T1", "final_grade": "Grade A", "destination": "bin_1", "attempt": 1})
    assert bus.messages("tile/T1/ack")[0]["status"] == "ok"
    sim.run(6.0)
    (placed,) = bus.messages("tile/T1/placed")
    assert placed["outcome"] == "placed" and placed["destination"] == "bin_1" and placed["stack_count"] == 1
    assert sim.model.count("bin_1") == 1


def test_mock_backend_also_reports_placed():
    sim = Sim("mock")
    sim.bus.publish("tile/T1/final", {"tile_id": "T1", "final_grade": "Grade B", "destination": "bin_2",
                                      "attempt": 1})
    sim.run(40.0, dt=0.05)
    assert [m["outcome"] for m in sim.bus.messages("tile/T1/placed")] == ["placed"]


def test_home_on_start_homes_once_when_asked_and_never_otherwise():
    sim = Sim(patch={"home_on_start": True})
    hook = sim.link.tick_hook(min_interval_s=0.0, clock=lambda: sim.t)
    sim.run(3.0, hook=hook)
    assert sim.link.driver.status()["homed"] is True
    homes = [l for l in sim.mcu.received if " HOME " in l]
    assert len(homes) == 1

    off = Sim()
    off.run(3.0, hook=off.link.tick_hook(min_interval_s=0.0, clock=lambda: off.t))
    assert off.link.driver.status()["homed"] is False  # homing is an operator action by default


def test_clean_shutdown_publishes_a_retained_offline():
    sim = Sim()
    bus, link = sim.bus, sim.link
    link.reporter.close()
    topic, data, qos, retain = [p for p in bus.published if p[0] == "station/gantry/status"][-1]
    assert json.loads(data)["state"] == "offline" and retain is True
    link.reporter.close()  # idempotent
    assert len([p for p in bus.published if p[0] == "station/gantry/status"
                and json.loads(p[1])["state"] == "offline"]) == 1


def test_the_station_sees_this_node_as_healthy_after_a_hand_off():
    sim = Sim()
    bus = sim.bus
    coord = StationCoordinator({"unoq": {"require_ack": True}, "final": {"bins": {"Grade A": "bin_1"}}},
                               bus, TileStore(":memory:"), clock=lambda: sim.t)
    coord.start()
    coord.set_health("mic", OK)
    sim.run(0.5)
    sim.link.driver.request_home()
    sim.run(1.5)
    bus.publish("tile/T1/camera/result", {"tile_id": "T1", "grade": "Grade A", "seq": 1})
    coord.on_tap({"valid": True, "grade": None, "confidence": None})
    sim.run(6.0)
    snap = coord.snapshot()
    assert snap["counters"]["placed"] == 1 and snap["health"]["gantry"]["level"] == OK
