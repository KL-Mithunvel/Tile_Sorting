"""Entry point for the io_smoke_test bench app -- starts the serial backend
and runs the Flask GUI.

Unlike stepper_smoke_test's python/main.py (a pure App Lab stub -- that
sketch does everything and the Python side just keeps the container alive),
this app's Python side has real work to do: read the sketch's status lines
and drive the vacuum command. So this deliberately does NOT follow the
`from arduino.app_utils import App; App.run()` shape used elsewhere in this
repo (acoustic_node/python/main.py, pick_place_node/python/main.py) --
that API's exact behaviour (what App.run() blocks on, what hooks it offers
for background work) is itself unverified (see acoustic_node/README.md), and
stacking a second unverified assumption on top of the already-unverified
serial port (see serial_backend.py) isn't worth it. Flask's own app.run()
blocks and keeps the process running, which is all this needs from an App
Lab container's perspective -- the deploy mechanism itself (compile, flash,
run this Python side) IS confirmed working (Deployment Notes, 2026-09-01).

If that assumption turns out wrong -- e.g. arduino-app-cli expects
App.run() specifically for supervision/health-checks -- wrap this in a
thread and call App.run() afterwards; nothing else here would need to change.
"""

from __future__ import annotations

from dashboard import create_app
from serial_backend import SerialIOBackend, SharedState, load_config


def main() -> None:
    config = load_config()
    state = SharedState()
    backend = SerialIOBackend(config, state)
    backend.start()

    app = create_app(state, backend, config["dashboard"]["poll_interval_s"])
    app.run(host=config["dashboard"]["host"], port=config["dashboard"]["port"])


if __name__ == "__main__":
    main()
