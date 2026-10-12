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
import threading
import time
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
    p.add_argument("--demo", action="store_true",
                   help="no broker: drive the pages from a synthetic line (implies --controls)")
    args = p.parse_args(argv)

    cfg = load_config()
    broker = cfg["broker"]
    host = args.host or broker["host"]
    port = args.port or int(broker.get("port", 1883))
    db = Path(cfg["store"]["path"])
    dash = cfg["dashboard"]

    # A demo run must never write synthetic tiles into the real record.
    collector = MasterCollector(":memory:" if args.demo else (db if db.is_absolute() else REPO_ROOT / db))
    demo_line = None
    if args.demo:
        from master.bus import InMemoryBus
        from master.demo import DemoLine

        bus = InMemoryBus()
        collector.attach(bus)
        demo_line = DemoLine(bus)
        demo_line.start()
        where = "DEMO (synthetic line, no broker)"
    else:
        bus = MqttBus(host, port, client_id=broker.get("client_id", "tile-master"))
        collector.attach(bus)
        bus.start()
        where = f"broker {host}:{port}"

    def _watch() -> None:
        # Station health is logged on a timer so a station going silent is
        # recorded even when nobody has the Troubleshoot page open.
        while True:
            time.sleep(2.0)
            try:
                collector.tick(broker_connected=bool(bus.connected))
            except Exception:  # noqa: BLE001 - the watcher must never take the master down
                pass

    threading.Thread(target=_watch, daemon=True, name="health-watch").start()

    controls = bool(args.controls or args.demo or dash.get("controls_enabled", False))
    app = create_app(collector, bus, controls_enabled=controls, demo=args.demo)
    print(f"Master at http://{dash['host']}:{dash['port']}/  {where}  controls {'ON' if controls else 'off'}")
    try:
        app.run(host=dash["host"], port=int(dash["port"]), threaded=True)
    except KeyboardInterrupt:
        pass
    finally:
        if demo_line is not None:
            demo_line.stop()
        bus.stop()
        collector.close()


if __name__ == "__main__":
    sys.exit(main())
