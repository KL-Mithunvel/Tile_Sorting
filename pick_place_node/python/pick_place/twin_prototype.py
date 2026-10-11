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

import threading
import time

from pick_place.coordinate_model import CoordinateModel
from pick_place.dashboard import create_app
from pick_place.gantry_backend import create_backend
from pick_place.mock_job_driver import MockJobDriver
from pick_place.mock_state import load_config
from pick_place.sequencer import Feeds
from pick_place.tile_feed import FeedClient
from pick_place.worker import SharedState, TwinWorker

DEMO_GRADES = ("Grade A", "Grade B", "Grade C", "Reject")


def _demo_feeder(feed, model, stop: threading.Event, interval_s: float) -> None:
    """Stand-in for the Pi: one tile every `interval_s`, cycling the stacks. A full
    stack is emptied (as an operator would) so the demo can run forever."""
    n = 0
    names = model.stack_names
    while not stop.wait(interval_s):
        if feed.queue.snapshot()["depth"] >= 5:
            continue
        dest = names[n % len(names)]
        if model.is_full(dest):
            model.reset_stack(dest)
        n += 1
        feed.queue.accept({"tile_id": f"DEMO-{n:04d}", "final_grade": DEMO_GRADES[(n - 1) % len(DEMO_GRADES)],
                           "destination": dest, "attempt": 1}, time.time())


def _scene(config: dict) -> dict:
    mock = config["mock"]
    return {**config["scene"], "pick_xy": mock["pick_xy"], "place_xy": mock["place_xy"]}


def main(argv=None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Digital twin prototype (mock gantry + WiFi dashboard).")
    parser.add_argument("--station-host", default=None, metavar="HOST",
                        help="Also receive tile finals from the Pi station's MQTT broker at HOST; the "
                             "mock gantry then picks each tile from the conveyor end and places it on "
                             "the stack the Pi names.")
    parser.add_argument("--station-port", type=int, default=1883)
    parser.add_argument("--demo", action="store_true",
                        help="No Pi: feed the queue with synthetic tiles so the stacks fill on screen.")
    parser.add_argument("--demo-interval", type=float, default=4.0, metavar="SECONDS")
    args = parser.parse_args(argv)
    config = load_config()

    if args.demo and args.station_host:
        parser.error("--demo and --station-host are alternatives (synthetic tiles vs the real Pi)")

    simulator = create_backend(config)
    state = SharedState()
    dash_cfg = config["dashboard"]
    model = CoordinateModel.from_config(config)

    feed = feed_bus = None
    feed_source = "station"
    if args.station_host:
        from pick_place.tile_feed import PahoFeedBus

        feed_bus = PahoFeedBus(args.station_host, args.station_port)
        feed = FeedClient(feed_bus)
        feed.start()
        feed_bus.start()
        print(f"Receiving tile finals from the station broker at {args.station_host}:{args.station_port}.")
    elif args.demo:
        feed = FeedClient(None)  # a queue with no broker behind it
        feed_source = "demo"

    driver = None
    if feed is not None:
        driver = MockJobDriver(simulator, feed.queue, model, Feeds.from_config(config))
        simulator.set_mode("jobs")  # jog / vacuum / Auto in the page pauses the jobs

    worker = TwinWorker(simulator, state, tick_hz=dash_cfg["tick_hz"], driver=driver)
    worker.start()

    demo_stop = threading.Event()
    if args.demo:
        threading.Thread(target=_demo_feeder, args=(feed, model, demo_stop, args.demo_interval),
                         daemon=True).start()
        print(f"Demo: one synthetic tile every {args.demo_interval:g} s.")

    app = create_app(state, workspace=config["workspace"], poll_interval_s=dash_cfg["poll_interval_s"],
                     backend=simulator, scene=_scene(config), jog_steps_mm=dash_cfg["jog_steps_mm"], feed=feed,
                     model=model, driver=driver, feed_source=feed_source)
    print(f"Digital twin prototype running at http://{dash_cfg['host']}:{dash_cfg['port']}/ — Ctrl+C to stop.")

    try:
        app.run(host=dash_cfg["host"], port=dash_cfg["port"], threaded=True)
    except KeyboardInterrupt:
        pass
    finally:
        demo_stop.set()
        worker.stop()
        if feed_bus is not None:
            feed_bus.stop()


if __name__ == "__main__":
    main()
