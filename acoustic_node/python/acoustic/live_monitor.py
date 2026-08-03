"""Continuous mic monitor with auto-trigger + FFT plot.

Usage (from the repo root, with the venv active):

    python -m acoustic.live_monitor                # listen, plot on trigger
    python -m acoustic.live_monitor --calibrate     # just print live RMS
    python -m acoustic.live_monitor --list-devices  # show input devices

--calibrate is the first thing to run on a new machine/mic: watch the
printed RMS level while the room is quiet vs. while you make the target
sound, then set trigger.rms_threshold in config.yaml somewhere between the
two.
"""

from __future__ import annotations

import argparse
import sys
import time

from acoustic.capture import AudioCapture, list_input_devices, load_config
from acoustic.plotting import plot_waveform_and_spectrum


def run_calibrate(capture: AudioCapture) -> None:
    print("Calibration mode — printing live RMS level. Ctrl+C to stop.")
    print("Watch this while quiet, then while making your target sound.\n")
    try:
        with capture:
            while True:
                print(f"\rRMS: {capture.last_rms:.4f}", end="", flush=True)
                time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nStopped.")


def run_monitor(capture: AudioCapture) -> None:
    print("Listening for sound above the configured trigger threshold. Ctrl+C to stop.")
    print("Close each plot window to resume listening.\n")
    try:
        with capture:
            while True:
                clip = capture.get_clip()
                print("Triggered — plotting clip.")
                plot_waveform_and_spectrum(clip, capture.sample_rate)
    except KeyboardInterrupt:
        print("\nStopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibrate", action="store_true", help="print live RMS instead of triggering")
    parser.add_argument("--list-devices", action="store_true", help="list input-capable audio devices and exit")
    args = parser.parse_args()

    if args.list_devices:
        for i, d in enumerate(list_input_devices()):
            print(f"[{i}] {d['name']} (inputs: {d['max_input_channels']}, default SR: {d['default_samplerate']:.0f})")
        return

    config = load_config()

    try:
        capture = AudioCapture(config)
    except Exception as exc:
        print(f"Failed to initialize audio capture: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.calibrate:
        run_calibrate(capture)
    else:
        run_monitor(capture)


if __name__ == "__main__":
    main()
