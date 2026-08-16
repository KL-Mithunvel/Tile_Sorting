"""Digital twin prototype entry point: mock gantry motion + WiFi dashboard,
decoupled from any real GantryWorker or command routing
(documents/project/Pick_Place_Sorting_Subsystem.md §3.1).

Usage (from pick_place_node/python/, with the venv active):

    python -m pick_place.twin_prototype

Then open http://<this machine's IP>:5050/ from any browser on the same WiFi
network. Ctrl+C to stop. Mirrors acoustic_node/camera_node's live_*.py CLI
shape.
"""

from __future__ import annotations

from pick_place.dashboard import create_app
from pick_place.mock_state import MockGantrySimulator, build_default_patrol, load_config
from pick_place.worker import SharedState, TwinWorker


def main() -> None:
    config = load_config()

    waypoints = build_default_patrol(config)
    simulator = MockGantrySimulator(waypoints, travel_speed_mm_s=config["mock"]["travel_speed_mm_s"])
    state = SharedState()
    dash_cfg = config["dashboard"]
    worker = TwinWorker(simulator, state, tick_hz=dash_cfg["tick_hz"])
    worker.start()

    app = create_app(state, workspace=config["workspace"], poll_interval_s=dash_cfg["poll_interval_s"])
    print(f"Digital twin prototype running at http://{dash_cfg['host']}:{dash_cfg['port']}/ — Ctrl+C to stop.")

    try:
        app.run(host=dash_cfg["host"], port=dash_cfg["port"], threaded=True)
    except KeyboardInterrupt:
        pass
    finally:
        worker.stop()


if __name__ == "__main__":
    main()
