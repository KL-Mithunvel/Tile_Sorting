"""Camera node entry point: camera capture + tile detection + WiFi dashboard.

Usage (from camera_node/python/, with the venv active):

    python -m camera.live_dashboard                  # run it
    python -m camera.live_dashboard --list-devices   # what can this machine open?
    python -m camera.live_dashboard --backend usb    # override config.yaml
    python -m camera.live_dashboard --device 1       # a USB camera by index
    python -m camera.live_dashboard --video data/recordings/2026-10-11_14-30-05/video.mp4 --loop

The source can also be changed from the dashboard itself (Source panel: pick a
camera, open a recording or a file under data/, record the live feed), so none
of these flags is required.

Then open http://<this machine's IP>:5000/ from any browser on the same
network (host defaults to 0.0.0.0 in config.yaml, so it's not limited to
localhost). On the Pi station itself — which runs Raspberry Pi OS *Desktop*,
so it has a browser — http://localhost:5000/ works directly on an attached
monitor or over VNC. Ctrl+C to stop.

Target hardware: Raspberry Pi 5 (16 GB), Raspberry Pi OS Desktop 64-bit
(Automation_Architecture.md §5.2, decided 2026-09-20). Runs unchanged on the
Windows dev laptop against its built-in webcam, which is still the only way
it has actually been exercised.

Mirrors acoustic_node's live_monitor.py CLI shape.
"""

from __future__ import annotations

import argparse
import sys

from camera.capture import list_cameras, load_config
from camera.dashboard import create_app
from camera.publisher import TilePublisher, status_from_shared_state
from camera.station import StationController
from camera.worker import SharedState


def print_devices() -> None:
    """Print every camera this machine can actually open, so camera.device_index
    / camera.picamera2.camera_num in config.yaml can be pinned per machine
    instead of guessed (same role as acoustic's --list-devices)."""
    devices = list_cameras()

    print("USB / UVC cameras (camera.backend: \"usb\", camera.device_index):")
    if devices["usb"]:
        for dev in devices["usb"]:
            print(
                f"  index {dev['index']}: {dev['width']}x{dev['height']} @ {dev['fps']:.0f} fps"
            )
    else:
        print("  (none found)")

    print("\nCSI camera modules (camera.backend: \"picamera2\", camera.picamera2.camera_num):")
    if devices["csi"]:
        for dev in devices["csi"]:
            print(f"  camera_num {dev['camera_num']}: {dev['info']}")
    else:
        print("  (none found — not a Raspberry Pi, or picamera2 not installed)")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Camera inspection station: live capture, tile grading, WiFi dashboard."
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List the cameras this machine can open, then exit.",
    )
    parser.add_argument(
        "--backend",
        choices=["auto", "usb", "picamera2"],
        default=None,
        help="Override config.yaml's camera.backend for this run.",
    )
    parser.add_argument(
        "--device",
        type=int,
        default=None,
        metavar="N",
        help="Open USB camera index N (see --list-devices) instead of config.yaml's camera.device_index.",
    )
    parser.add_argument(
        "--video",
        default=None,
        metavar="PATH",
        help="Start on a saved video instead of a camera (must be under data/), "
        "e.g. one of the dashboard's recordings.",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="With --video, replay the file in a loop instead of stopping at the end.",
    )
    parser.add_argument(
        "--mqtt",
        action="store_true",
        help="Forward graded tiles to the station's MQTT broker even if config.yaml's mqtt.enabled is false "
        "(the Pi's tile-camera service gets this from a systemd drop-in, so a code push never "
        "silently turns the hand-off off).",
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Run the vision pipeline without serving the web dashboard "
        "(headless grading only; results still go to the log and snapshots).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    if args.list_devices:
        print_devices()
        return

    if args.device is not None and args.video is not None:
        print("--device and --video are alternatives: pick one.", file=sys.stderr)
        sys.exit(2)
    if args.loop and args.video is None:
        print("--loop only applies with --video.", file=sys.stderr)
        sys.exit(2)

    config = load_config()
    if args.backend is not None:
        config["camera"]["backend"] = args.backend

    state = SharedState()
    # Optional hand-off to the Pi's acoustic/decision service. Off unless
    # config.yaml's mqtt.enabled is true, so a stand-alone camera is unchanged.
    publisher = None
    mqtt_cfg = config.get("mqtt") or {}
    if args.mqtt:
        mqtt_cfg = {**mqtt_cfg, "enabled": True}
    if mqtt_cfg.get("enabled"):
        publisher = TilePublisher(mqtt_cfg, status_source=lambda: status_from_shared_state(state))
        publisher.start()
        state.update_info(mqtt=True)
        print(f"Forwarding graded tiles to the station broker at {mqtt_cfg.get('host')}:{mqtt_cfg.get('port')} "
              f"(run {publisher.run_id}).")
    try:
        controller = StationController(config, state, publisher=publisher)
    except ValueError as exc:
        print(f"Bad configuration: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        controller.start(device=args.device, video=args.video, loop=args.loop)
    except (RuntimeError, ValueError) as exc:
        if args.no_dashboard:
            print(f"Failed to start the video source: {exc}", file=sys.stderr)
            sys.exit(1)
        # With a dashboard this is not fatal: it can still be pointed at a
        # camera or a file from the page.
        print(f"No video source yet: {exc}\nOpen the dashboard and choose one.", file=sys.stderr)
        state.set_source({"kind": "none", "label": "No source"}, status="error", detail=str(exc))

    source = controller.worker.source
    print(f"Video source: {source.get('label')}")

    if args.no_dashboard:
        print("Dashboard disabled (--no-dashboard). Ctrl+C to stop.")
        try:
            controller.worker.join()
        except KeyboardInterrupt:
            pass
        finally:
            controller.shutdown()
            if publisher is not None:
                publisher.stop()
        return

    dash_cfg = config["dashboard"]
    app = create_app(state, controller=controller, controls_enabled=dash_cfg.get("controls_enabled", True))
    print(f"Dashboard running at http://{dash_cfg['host']}:{dash_cfg['port']}/ — Ctrl+C to stop.")

    try:
        app.run(host=dash_cfg["host"], port=dash_cfg["port"], threaded=True)
    except KeyboardInterrupt:
        pass
    finally:
        controller.shutdown()
        if publisher is not None:
            publisher.stop()


if __name__ == "__main__":
    main()
