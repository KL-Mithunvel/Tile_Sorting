"""Acoustic station entry point: microphone + fusion + production GUI.

Usage (from acoustic_node/python/, venv active):

    python -m acoustic.live_station                 # the real thing (MQTT broker needed)
    python -m acoustic.live_station --demo          # no hardware, no broker: fake camera,
                                                    #   fake tap, fake UNO Q ack — to look at the GUI
    python -m acoustic.live_station --no-mqtt       # real microphone, in-process bus only
    python -m acoustic.live_station --list-devices  # which microphones exist

Then open http://<this machine>:5001/ (run view) or /service. On the Pi it is
started by systemd (acoustic_node/deploy/tile-acoustic.service) through
acoustic_node/python/run_station.py, and Chromium shows the run view fullscreen.

Not to be confused with `acoustic.live_monitor`, the older stand-alone
mic-plot tool, which is unchanged.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from acoustic.dashboard import create_app
from acoustic.station import StationService
from fusion.store import TileStore
from fusion.transport import InMemoryBus, MqttBus, TOPIC_STATUS

REPO_ROOT = Path(__file__).resolve().parents[3]


def load_config() -> dict:
    from acoustic.capture import load_config as _load  # imports sounddevice; fine on the station

    return _load()


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Acoustic station: tap analysis, fusion with the camera, GUI.")
    p.add_argument("--list-devices", action="store_true", help="List microphones, then exit.")
    p.add_argument("--demo", action="store_true", help="Synthetic microphone + fake camera/UNO Q on an in-process bus.")
    p.add_argument("--no-mqtt", action="store_true", help="Use an in-process bus instead of the MQTT broker.")
    p.add_argument("--no-dashboard", action="store_true", help="Run without serving the GUI.")
    p.add_argument("--controls", action="store_true", help="Enable the GUI's controls for this run.")
    return p.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.list_devices:
        from acoustic.capture import list_input_devices

        for i, d in enumerate(list_input_devices()):
            print(f"  {i}: {d['name']}  ({d['max_input_channels']} ch, {d['default_samplerate']:.0f} Hz)")
        return

    config = load_config()
    st_cfg = config.setdefault("station", {})
    dash_cfg = st_cfg.get("dashboard") or {}

    capture_factory = None
    demo_thread = None
    if args.demo:
        from acoustic.demo import FakeCameraAndStriker, SyntheticCapture

        config.setdefault("trigger", {})["mode"] = "simulated"
        bus = InMemoryBus()
        store = TileStore(":memory:")
        capture_factory = lambda cfg: SyntheticCapture(cfg)  # noqa: E731
        demo_thread = FakeCameraAndStriker(bus)
    else:
        mqtt_cfg = st_cfg.get("mqtt") or {}
        if args.no_mqtt or not mqtt_cfg.get("enabled", True):
            bus = InMemoryBus()
        else:
            bus = MqttBus(
                host=mqtt_cfg.get("host", "127.0.0.1"),
                port=int(mqtt_cfg.get("port", 1883)),
                client_id=mqtt_cfg.get("client_id", "tile-acoustic"),
                will=(TOPIC_STATUS, {"state": "offline"}),
            )
        db_path = Path((st_cfg.get("store") or {}).get("path", "data/station/tiles.db"))
        store = TileStore(db_path if db_path.is_absolute() else REPO_ROOT / db_path)

    try:
        service = (
            StationService(config, bus, store, capture_factory=capture_factory)
            if capture_factory
            else StationService(config, bus, store)
        )
        service.start()
    except Exception as exc:  # noqa: BLE001
        print(f"Station could not start: {exc}", file=sys.stderr)
        sys.exit(1)
    if demo_thread is not None:
        demo_thread.start()
        print("DEMO MODE: everything you see is synthetic.")

    # The Nano (striker + belt) on USB serial. A port that cannot be opened is
    # reported, not fatal: the health strip shows the Nano as missing and, once
    # station.nano.required is true, the station is in FAULT.
    bridge = None
    nano_cfg = st_cfg.get("nano") or {}
    if nano_cfg.get("enabled") and not args.demo:
        from acoustic.tap_bridge import TapBridge, open_pyserial

        try:
            ser = open_pyserial(nano_cfg["port"], int(nano_cfg.get("baud", 115200)))
        except Exception as exc:  # noqa: BLE001 - no port / no pyserial / permission
            print(f"Nano serial port {nano_cfg.get('port')!r} could not be opened: {exc}", file=sys.stderr)
        else:
            bridge = TapBridge(
                ser, bus,
                ping_s=float(nano_cfg.get("ping_s", 0.5)),
                stale_s=float(nano_cfg.get("stale_s", 3.0)),
            )
            bridge.start()
            print(f"Nano bridge on {nano_cfg['port']} @ {nano_cfg.get('baud', 115200)}.")

    def _shutdown() -> None:
        if bridge is not None:
            bridge.stop()
        if demo_thread is not None:
            demo_thread.stop()
        service.shutdown()

    if args.no_dashboard:
        print("GUI disabled (--no-dashboard). Ctrl+C to stop.")
        try:
            import time

            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
        finally:
            _shutdown()
        return

    controls = bool(args.controls or args.demo or dash_cfg.get("controls_enabled", False))
    app = create_app(service, controls_enabled=controls)
    host, port = dash_cfg.get("host", "0.0.0.0"), int(dash_cfg.get("port", 5001))
    print(f"Station GUI at http://{host}:{port}/  (service view: /service, controls {'ON' if controls else 'off'})")
    try:
        app.run(host=host, port=port, threaded=True)
    except KeyboardInterrupt:
        pass
    finally:
        _shutdown()


if __name__ == "__main__":
    main()
