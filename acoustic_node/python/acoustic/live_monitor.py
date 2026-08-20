"""Continuous mic monitor with auto-trigger + FFT plot.

Usage (from the repo root, with the venv active):

    python -m acoustic.live_monitor                # listen, plot on trigger
    python -m acoustic.live_monitor --calibrate     # just print live RMS
    python -m acoustic.live_monitor --list-devices  # show input devices

--calibrate is the first thing to run on a new machine/mic: watch the
printed RMS level while the room is quiet vs. while you make the target
sound, then set trigger.rms_threshold in config.yaml somewhere between the
two.

If config.yaml's trigger.mode is "simulated", running with no flags also
starts a background thread that drives acoustic.tap_sequencer.TapSequencer
against a fake, periodic tile arrival and calls AudioCapture.notify_tap() on
each release — exercises the whole ToF-trigger -> arm -> release ->
audio-capture path on a dev machine with no ToF sensor or solenoids attached
(there's just nothing physical making the impact sound, so expect quiet/noise
clips, not real tile rings). See documents/electrical/schematics/
acoustic_station_wiring.md and acoustic/tap_sequencer.py.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from typing import Optional

from acoustic.capture import AudioCapture, list_input_devices, load_config
from acoustic.plotting import plot_waveform_and_spectrum
from acoustic.tap_sequencer import TapSequencer


def run_simulated_tap_driver(capture: AudioCapture, config: dict, stop_event: threading.Event) -> None:
    """Background thread, only started when trigger.mode is "simulated"."""
    hw_cfg = config["hardware_trigger"]
    tap_cfg = config["tap_mechanism"]
    sequencer = TapSequencer(
        settle_s=tap_cfg["settle_s"],
        arm_s=tap_cfg["arm_s"],
        arm_settle_s=tap_cfg["arm_settle_s"],
        lock_pulse_s=tap_cfg["lock_pulse_s"],
        cooldown_s=tap_cfg["cooldown_s"],
    )
    interval_s = hw_cfg["simulated_tile_interval_s"]
    present_s = hw_cfg["simulated_tile_present_s"]
    tick_s = 0.02
    elapsed_in_cycle = 0.0

    while not stop_event.is_set():
        time.sleep(tick_s)
        elapsed_in_cycle = (elapsed_in_cycle + tick_s) % interval_s
        tile_present = elapsed_in_cycle < present_s
        state = sequencer.advance(tick_s, tile_present)
        if state.tap_fired:
            print(f"\n[simulated] tap #{state.tap_count} fired (state={state.state})")
            capture.notify_tap()


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


def run_monitor(capture: AudioCapture, config: dict) -> None:
    print(f"Listening (trigger.mode={capture.trigger_mode!r}). Ctrl+C to stop.")
    print("Close each plot window to resume listening.\n")

    stop_event = threading.Event()
    tap_thread: Optional[threading.Thread] = None
    if capture.trigger_mode == "simulated":
        tap_thread = threading.Thread(
            target=run_simulated_tap_driver, args=(capture, config, stop_event), daemon=True
        )

    try:
        with capture:
            if tap_thread is not None:
                tap_thread.start()
            while True:
                clip = capture.get_clip()
                print("Triggered — plotting clip.")
                plot_waveform_and_spectrum(clip, capture.sample_rate)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        stop_event.set()
        if tap_thread is not None:
            tap_thread.join(timeout=2.0)


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
        run_monitor(capture, config)


if __name__ == "__main__":
    main()
