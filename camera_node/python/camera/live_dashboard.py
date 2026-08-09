"""Camera node entry point: webcam capture + tile detection + WiFi dashboard.

Usage (from camera_node/python/, with the venv active):

    python -m camera.live_dashboard

Then open http://<this machine's IP>:5000/ from any browser on the same
WiFi network (host defaults to 0.0.0.0 in config.yaml, so it's not limited to
localhost). Ctrl+C to stop.

Mirrors acoustic_node's live_monitor.py CLI shape.
"""

from __future__ import annotations

import sys

from camera.capture import WebcamCapture, load_config
from camera.dashboard import create_app
from camera.worker import CameraWorker, SharedState


def main() -> None:
    config = load_config()

    capture = WebcamCapture(config)
    state = SharedState()
    worker = CameraWorker(capture, config, state)

    try:
        worker.start()
    except RuntimeError as exc:
        print(f"Failed to start camera capture: {exc}", file=sys.stderr)
        sys.exit(1)

    app = create_app(state)
    dash_cfg = config["dashboard"]
    print(f"Dashboard running at http://{dash_cfg['host']}:{dash_cfg['port']}/ — Ctrl+C to stop.")

    try:
        app.run(host=dash_cfg["host"], port=dash_cfg["port"], threaded=True)
    except KeyboardInterrupt:
        pass
    finally:
        worker.stop()


if __name__ == "__main__":
    main()
