"""Acoustic station entry point: microphone + fusion + production GUI.

Usage (from acoustic_node/python/, venv active):

    python -m acoustic.live_station                 # the real thing (MQTT broker needed)
    python -m acoustic.live_station --demo          # no hardware, no broker: fake camera,
                                                    #   fake tap, fake UNO Q ack — to look at the GUI
    python -m acoustic.live_station --no-mqtt       # real microphone, in-process bus only
    python -m acoustic.live_station --list-devices  # which microphones exist
    python -m acoustic.live_station --collect       # data-collection setup: Nano + hardware trigger,
                                                    #   no UNO Q needed, controls on (see Sessions in /service)

Then open http://<this machine>:5001/ (run view) or /service. On the Pi it is
started by systemd (acoustic_node/deploy/tile-acoustic.service) through
acoustic_node/python/main.py, and Chromium shows the run view fullscreen.

Not to be confused with `acoustic.live_monitor`, the older stand-alone
mic-plot tool, which is unchanged.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path

from acoustic.dashboard import create_app
from acoustic.session_control import SessionController
from acoustic.station import StationService
from acoustic.striker_arming import AutoArm, CameraArming
from fusion.session import SessionRecorder
from fusion.blobs import BlobStore
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
    p.add_argument("--collect", action="store_true",
                   help="Data-collection run: hardware trigger (the Nano's strikes start the clips), no UNO Q "
                        "needed (finals are not held back waiting for an acknowledgement and Hold tiles do not "
                        "stop the feed), GUI controls on.")
    p.add_argument("--trigger-mode", choices=["rms", "hardware", "simulated"], default=None,
                   help="Override config.yaml's trigger.mode for this run.")
    p.add_argument("--synthetic-mic", action="store_true",
                   help="Use a synthetic microphone with the REAL broker (bring-up self-test: proves the "
                        "software chain end to end with no microphone; implies --trigger-mode simulated).")
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
    if args.trigger_mode:
        config.setdefault("trigger", {})["mode"] = args.trigger_mode
    if args.synthetic_mic:
        config.setdefault("trigger", {})["mode"] = "simulated"
    st_cfg = config.setdefault("station", {})
    if args.collect:
        # Nothing here touches the line's safety rules; it only stops a bench with no gantry from
        # raising "UNO Q down" alarms and pausing a feed that does not exist.
        config.setdefault("trigger", {})["mode"] = args.trigger_mode or "hardware"
        st_cfg.setdefault("unoq", {})["require_ack"] = False
        st_cfg.setdefault("final", {})["hold_to_gantry"] = False
        st_cfg["final"]["hold_pauses_feed"] = False
        st_cfg.setdefault("dashboard", {})["controls_enabled"] = True
    dash_cfg = st_cfg.get("dashboard") or {}

    capture_factory = None
    demo_thread = None
    if args.demo:
        from acoustic.demo import FakeCameraAndStriker, SyntheticCapture

        config.setdefault("trigger", {})["mode"] = "simulated"
        bus = InMemoryBus()
        store = TileStore(":memory:")
        blob_root = Path(tempfile.mkdtemp(prefix="tile_demo_blobs_"))   # demo evidence never touches data/
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
        if args.synthetic_mic:
            # A self-test must not pollute the line's real record.
            db_path = Path("data/station/selftest.db")
        store = TileStore(db_path if db_path.is_absolute() else REPO_ROOT / db_path)
        store_cfg = st_cfg.get("store") or {}
        blob_dir = Path(store_cfg.get("blob_dir", "data/station/blobs"))
        if args.synthetic_mic:
            blob_dir = Path("data/station/selftest_blobs")
        blob_root = blob_dir if blob_dir.is_absolute() else REPO_ROOT / blob_dir
        if args.synthetic_mic:
            from acoustic.demo import SyntheticCapture

            capture_factory = lambda cfg: SyntheticCapture(cfg)  # noqa: E731

    budget = float((st_cfg.get("store") or {}).get("blob_budget_gb", 20.0))
    blobs = BlobStore(blob_root, store, budget_bytes=int(budget * 1024 ** 3))

    # The striker rests down (the coil overheated held up all the time); the camera's detection raises it.
    striker_cfg = st_cfg.get("striker") or {}
    if striker_cfg.get("arm_on_camera", True):
        CameraArming(bus).attach()
    # ... and it is ARMED only while a tile is in the system (the coil is energised when armed and wanted).
    auto_arm = AutoArm(bus, enabled=bool(striker_cfg.get("auto_arm", True)),
                       tile_mm=int(striker_cfg.get("tile_mm", 100)),
                       grace_s=float(striker_cfg.get("disarm_grace_s", 2.0)))

    session = None
    sess_cfg = st_cfg.get("session") or {}
    if sess_cfg.get("enabled", True):
        sess_root = Path(sess_cfg.get("root", "data/sessions"))
        if args.demo:
            sess_root = Path(tempfile.mkdtemp(prefix="tile_demo_sessions_"))
        elif args.synthetic_mic:
            sess_root = Path("data/station/selftest_sessions")
        if not sess_root.is_absolute():
            sess_root = REPO_ROOT / sess_root
        recorder = SessionRecorder(
            sess_root, repo_root=REPO_ROOT,
            chunk_s=float(sess_cfg.get("chunk_s", 30.0)),
            min_free_gb=float(sess_cfg.get("min_free_gb", 2.0)),
            stop_free_gb=float(sess_cfg.get("stop_free_gb", 0.5)),
            clip_pre_s=float(sess_cfg.get("clip_pre_s", 0.3)),
            clip_post_s=float(sess_cfg.get("clip_post_s", 1.2)),
        )
        session = SessionController(
            bus, recorder, data_root=REPO_ROOT / "data",
            sample_rate=int((config.get("audio") or {}).get("sample_rate", 44100)),
            config_snapshot=lambda: {"acoustic": config},
        )
    try:
        service = (
            StationService(config, bus, store, capture_factory=capture_factory, blobs=blobs, session=session,
                           auto_arm=auto_arm)
            if capture_factory
            else StationService(config, bus, store, blobs=blobs, session=session, auto_arm=auto_arm)
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
                status_s=float(nano_cfg.get("status_s", 0.5)),   # the striker panel shows a live countdown
                resend_on_recover=bool(nano_cfg.get("resend_on_recover", False)),
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
    app = create_app(service, controls_enabled=controls, sync_token=(st_cfg.get("sync") or {}).get("token"))
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
