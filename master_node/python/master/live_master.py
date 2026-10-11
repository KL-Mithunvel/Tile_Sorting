"""Master PC entry point: connect to the Pi's broker, collect, serve the page.

    cd master_node/python
    python -m master.live_master                       # config.yaml's broker (192.168.1.11)
    python -m master.live_master --host localhost      # a broker on this machine
    python -m master.live_master --controls            # enable the command buttons

Then open http://localhost:8000/. The line does not need this program.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from master.app import create_app
from master.bus import MqttBus
from master.collector import MasterCollector

REPO_ROOT = Path(__file__).resolve().parents[3]


def load_config() -> dict:
    with open(Path(__file__).with_name("config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Tile station master: observe, collect, command.")
    p.add_argument("--host", default=None, help="MQTT broker (default: config.yaml broker.host)")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--controls", action="store_true", help="enable the command buttons for this run")
    args = p.parse_args(argv)

    cfg = load_config()
    broker = cfg["broker"]
    host = args.host or broker["host"]
    port = args.port or int(broker.get("port", 1883))
    db = Path(cfg["store"]["path"])
    dash = cfg["dashboard"]

    collector = MasterCollector(db if db.is_absolute() else REPO_ROOT / db)
    bus = MqttBus(host, port, client_id=broker.get("client_id", "tile-master"))
    collector.attach(bus)
    bus.start()

    controls = bool(args.controls or dash.get("controls_enabled", False))
    app = create_app(collector, bus, controls_enabled=controls)
    print(f"Master at http://{dash['host']}:{dash['port']}/  broker {host}:{port}  controls {'ON' if controls else 'off'}")
    try:
        app.run(host=dash["host"], port=int(dash["port"]), threaded=True)
    except KeyboardInterrupt:
        pass
    finally:
        bus.stop()
        collector.close()


if __name__ == "__main__":
    sys.exit(main())
