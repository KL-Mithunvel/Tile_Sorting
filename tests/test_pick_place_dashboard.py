import pytest

from pick_place.dashboard import create_app
from pick_place.mock_state import MockGantrySimulator, Waypoint
from pick_place.worker import SharedState

WS = {"x_min_mm": 0, "x_max_mm": 100, "y_min_mm": 0, "y_max_mm": 100,
      "z_min_mm": 0, "z_max_mm": 100}


@pytest.fixture
def client_and_sim():
    wp = [Waypoint(50, 50, 50, "HOMING", False, "IDLE"),
          Waypoint(60, 50, 50, "MOVING", False, "IDLE")]
    sim = MockGantrySimulator(wp, travel_speed_mm_s=10, workspace=WS)
    state = SharedState()
    app = create_app(state, WS, 0.1, backend=sim)
    return app.test_client(), sim


def test_jog_moves_target(client_and_sim):
    client, sim = client_and_sim
    assert client.post("/api/jog", json={"axis": "x", "delta_mm": 10}).status_code == 200
    assert sim.mode == "manual"
    assert sim.advance(10).x == pytest.approx(60.0)


def test_vacuum_and_mode(client_and_sim):
    client, sim = client_and_sim
    assert client.post("/api/vacuum", json={"on": True}).status_code == 200
    assert sim.advance(0.1).vacuum_on is True
    assert client.post("/api/mode", json={"mode": "auto"}).status_code == 200
    assert sim.mode == "auto"


def test_bad_requests_rejected(client_and_sim):
    client, _ = client_and_sim
    assert client.post("/api/jog", json={"axis": "w", "delta_mm": 1}).status_code == 400
    assert client.post("/api/jog", json={}).status_code == 400
    assert client.post("/api/vacuum", json={"on": "yes"}).status_code == 400
    assert client.post("/api/mode", json={"mode": "turbo"}).status_code == 400


def test_no_backend_returns_503():
    app = create_app(SharedState(), WS, 0.1)
    assert app.test_client().post("/api/jog", json={"axis": "x", "delta_mm": 1}).status_code == 503


def test_page_renders_control_panel(client_and_sim):
    html = client_and_sim[0].get("/").get_data(as_text=True)
    for label in ("+X", "-X", "+Y", "-Y", "Z+", "Z-", "Vacuum ON", "Vacuum OFF"):
        assert label in html


def test_page_view_only_without_backend():
    html = create_app(SharedState(), WS, 0.1).test_client().get("/").get_data(as_text=True)
    assert "CONTROLS_ENABLED = false" in html


def test_mock_conforms_to_backend_protocol_via_factory():
    from pick_place.gantry_backend import GantryBackend, create_backend
    from pick_place.mock_state import load_config

    backend = create_backend(load_config())
    assert isinstance(backend, GantryBackend)


def test_factory_rejects_unknown_and_reports_an_unopenable_serial_port():
    from pick_place.gantry_backend import create_backend
    from pick_place.mock_state import load_config
    from pick_place.serial_link import LinkError

    cfg = load_config()
    cfg["gantry"]["serial"]["port"] = "NO_SUCH_PORT"
    with pytest.raises(LinkError):  # pyserial missing or port absent: a clear error either way
        create_backend({**cfg, "backend": "serial"})
    with pytest.raises(ValueError):
        create_backend({**cfg, "backend": "nope"})


# ---- layout / job / feed panels (conveyor end + four stacks) -------------------


def _rig():
    from pick_place.coordinate_model import CoordinateModel
    from pick_place.mock_job_driver import MockJobDriver
    from pick_place.mock_state import build_default_patrol, load_config
    from pick_place.sequencer import Feeds
    from pick_place.tile_feed import FeedClient

    cfg = load_config()
    sim = MockGantrySimulator(build_default_patrol(cfg), travel_speed_mm_s=2000, workspace=cfg["workspace"])
    model = CoordinateModel.from_config(cfg)
    feed = FeedClient(None)
    driver = MockJobDriver(sim, feed.queue, model, Feeds.from_config(cfg))
    app = create_app(SharedState(), cfg["workspace"], 0.1, backend=sim, model=model, driver=driver,
                     feed=feed, feed_source="demo")
    return app.test_client(), model, feed


def test_layout_has_pickup_four_stacks_and_live_counts():
    client, model, _ = _rig()
    layout = client.get("/api/layout").get_json()["layout"]
    assert [s["name"] for s in layout["stacks"]] == ["bin_1", "bin_2", "bin_3", "bin_4"]
    assert [s["label"] for s in layout["stacks"]] == ["Grade A", "Grade B", "Grade C", "Reject"]
    assert layout["pickups"][0]["name"] == "pickup"
    assert layout["tile"] == {"size_mm": 82.55, "thickness_mm": 12.0, "weight_g": 300.0}
    model.record_placed("bin_2")
    model.record_placed("bin_2")
    s2 = client.get("/api/layout").get_json()["layout"]["stacks"][1]
    assert s2["count"] == 2 and s2["next_release_z_mm"] == pytest.approx(44.0) and not s2["full"]


def test_full_stack_is_reported_full_with_no_next_release():
    client, model, _ = _rig()
    model.reset_stack("bin_4", 10)
    s4 = client.get("/api/layout").get_json()["layout"]["stacks"][3]
    assert s4["full"] and s4["next_release_z_mm"] is None


def test_feed_reports_queue_and_source():
    client, _, feed = _rig()
    feed.queue.accept({"tile_id": "T1", "final_grade": "Grade A", "destination": "bin_1", "attempt": 1}, 0)
    body = client.get("/api/feed").get_json()
    assert body["connected"] and body["source"] == "demo" and body["queue"]["depth"] == 1
    assert body["queue"]["queued"][0]["source"] == "pickup"


def test_job_endpoint_reports_idle_driver():
    client, _, _ = _rig()
    job = client.get("/api/job").get_json()["job"]
    assert job["active"] is None and job["completed"] == 0 and job["failed"] == 0


def test_layout_job_feed_degrade_without_a_station():
    client = create_app(SharedState(), WS, 0.1).test_client()
    assert client.get("/api/layout").get_json() == {"layout": None}
    assert client.get("/api/job").get_json() == {"job": None}
    assert client.get("/api/feed").get_json()["connected"] is False


def test_page_has_the_new_panels_and_jobs_mode():
    html = _rig()[0].get("/").get_data(as_text=True)
    for needle in ('id="job-panel"', 'id="stacks-panel"', 'id="queue-panel"', 'id="setup-panel"',
                   'data-mode="jobs"', "/api/layout", "/api/job", "Conveyor end"):
        assert needle in html, needle
