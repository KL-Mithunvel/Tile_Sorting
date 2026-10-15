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
from master.llm import build_provider

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
    p.add_argument("--sim", action="store_true",
                   help="no broker: run the REAL station coordinator + gantry runner on a simulated machine "
                        "(implies --controls; see master/sim_line.py)")
    p.add_argument("--pi-url", default=None,
                   help="Pi station GUI to pull the record from (default: config.yaml sync.pi_url)")
    p.add_argument("--no-sync", action="store_true", help="do not pull the Pi's record in this run")
    p.add_argument("--ai", choices=("none", "ollama", "anthropic"), default=None,
                   help="language model that explains anomalies (default: config.yaml insights.provider)")
    args = p.parse_args(argv)
    if args.sim and args.demo:
        p.error("--sim and --demo are alternatives; pick one")

    cfg = load_config()
    broker = cfg["broker"]
    host = args.host or broker["host"]
    port = args.port or int(broker.get("port", 1883))
    db = Path(cfg["store"]["path"])
    dash = cfg["dashboard"]

    # A demo run must never write synthetic tiles into the real record.
    collector = MasterCollector(":memory:" if (args.demo or args.sim) else (db if db.is_absolute() else REPO_ROOT / db))
    demo_line = None
    if args.demo:
        from master.bus import InMemoryBus
        from master.demo import DemoLine

        bus = InMemoryBus()
        collector.attach(bus)
        demo_line = DemoLine(bus)
        demo_line.start()
        where = "DEMO (synthetic line, no broker)"
    elif args.sim:
        from master.bus import StreamingInMemoryBus
        from master.sim_line import SimLine

        bus = StreamingInMemoryBus()
        collector.attach(bus)
        demo_line = SimLine(bus)
        demo_line.start()
        where = "SIM (real station + gantry logic, simulated machine, no broker)"
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

    # The Pi's own record. Never in a demo/sim run (those would mix synthetic tiles into the real copy).
    mirror = syncer = None
    sync_cfg = cfg.get("sync") or {}
    if sync_cfg.get("enabled", True) and not (args.no_sync or args.demo or args.sim):
        from master.mirror import PiMirror
        from master.sync import PiSyncer

        def _abs(path: str) -> Path:
            return Path(path) if Path(path).is_absolute() else REPO_ROOT / path

        mirror = PiMirror(_abs(sync_cfg.get("mirror_path", "data/master/pi_mirror.db")),
                          _abs(sync_cfg.get("blob_dir", "data/master/pi_blobs")))
        syncer = PiSyncer(mirror, args.pi_url or sync_cfg.get("pi_url", "http://192.168.1.11:5001"),
                          token=sync_cfg.get("token"), fetch_blobs=bool(sync_cfg.get("fetch_blobs", True)))
        syncer.start(float(sync_cfg.get("interval_s", 15)))
        where += f"; Pi record from {syncer._base}"

    controls = bool(args.controls or args.demo or args.sim or dash.get("controls_enabled", False))
    ai_cfg = dict(cfg.get("insights") or {})
    if args.ai:
        ai_cfg["provider"] = args.ai
    llm = build_provider(ai_cfg)
    app = create_app(collector, bus, controls_enabled=controls, demo=args.demo or args.sim, llm=llm,
                     mirror=mirror, syncer=syncer,
                     demo_label="sim - real station logic, simulated machine" if args.sim else "demo — synthetic line")
    print(f"Master at http://{dash['host']}:{dash['port']}/  {where}  controls {'ON' if controls else 'off'}  "
          f"AI explainer {llm.name + ' (' + str(getattr(llm, 'model', '')) + ')' if llm else 'off'}")
    try:
        app.run(host=dash["host"], port=int(dash["port"]), threaded=True)
    except KeyboardInterrupt:
        pass
    finally:
        if demo_line is not None:
            demo_line.stop()
        if syncer is not None:
            syncer.stop()
        bus.stop()
        collector.close()
        if mirror is not None:
            mirror.close()


if __name__ == "__main__":
    sys.exit(main())
