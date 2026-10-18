"""Entry point for the encoder_smoke_test bench app -- starts the Bridge
backend and runs the Flask GUI. Same structure (and same caveat about not
using arduino.app_utils.App.run()) as io_smoke_test/python/main.py."""

from __future__ import annotations

from bridge_backend import BridgeEncBackend, SharedState, load_config
from dashboard import create_app


def main() -> None:
    config = load_config()
    state = SharedState(config)
    backend = BridgeEncBackend(config, state)
    backend.start()

    app = create_app(state, backend, config)
    app.run(host=config["dashboard"]["host"], port=config["dashboard"]["port"])


if __name__ == "__main__":
    main()
