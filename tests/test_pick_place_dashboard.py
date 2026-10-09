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
