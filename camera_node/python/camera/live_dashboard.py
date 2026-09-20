"""Camera node entry point: camera capture + tile detection + WiFi dashboard.

Usage (from camera_node/python/, with the venv active):

    python -m camera.live_dashboard                  # run it
    python -m camera.live_dashboard --list-devices   # what can this machine open?
    python -m camera.live_dashboard --backend usb    # override config.yaml

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

from camera.capture import create_capture, list_cameras, load_config
from camera.dashboard import create_app
from camera.worker import CameraWorker, SharedState


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

    config = load_config()
    if args.backend is not None:
        config["camera"]["backend"] = args.backend

    try:
        capture = create_capture(config)
    except ValueError as exc:
        print(f"Bad camera configuration: {exc}", file=sys.stderr)
        sys.exit(1)

    state = SharedState()
    worker = CameraWorker(capture, config, state)

    print(f"Camera backend: {type(capture).__name__}")

    try:
        worker.start()
    except RuntimeError as exc:
        print(f"Failed to start camera capture: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.no_dashboard:
        print("Dashboard disabled (--no-dashboard). Ctrl+C to stop.")
        try:
            worker.join()
        except KeyboardInterrupt:
            pass
        finally:
            worker.stop()
        return

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
