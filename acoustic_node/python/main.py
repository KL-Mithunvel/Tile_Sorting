"""Arduino App Lab entry point for the acoustic node.

NOT YET VERIFIED against a real App Lab environment (see acoustic_node/README.md) —
this follows the shape of Arduino's own examples (app-bricks-examples,
core-and-foundational/02-led-matrix/03-led-matrix-animation-mcu/python/main.py) but
has not been run on this project's UNO Q board. The proven, tested entry point for
this node's logic is still the CLI: `python -m acoustic.live_monitor`, run from this
python/ directory (see README.md).

Intended wiring, once App Bricks' actual bridge API is confirmed on hardware
(documents/electrical/schematics/acoustic_station_wiring.md,
acoustic_node/sketch/sketch.ino): the sketch runs the ToF-triggered arm/lock tap
sequence and exposes an incrementing `tapCount` bridge variable, incremented the
instant the ball is released. This file should poll that variable and call
AudioCapture.notify_tap() each time it changes, with acoustic/config.yaml's
trigger.mode set to "hardware" so AudioCapture uses HardwareTapDetector instead
of the RMS trigger. poll_bridge_and_capture() below is written in that shape
already, so it's ready to test the moment `arduino.app_bricks`' real import
path/API for reading a sketch bridge variable from Python is confirmed — but
that import and the `read_tap_count()` call are unverified placeholders, not a
working integration yet. It is deliberately NOT wired into App.run() below
until that's confirmed, to avoid silently doing nothing (or crashing on import)
in the meantime.
"""

from arduino.app_utils import App

from acoustic.capture import AudioCapture, load_config


def read_tap_count() -> int:
    """UNVERIFIED placeholder — replace with the real App Bricks call once
    confirmed, e.g. something like `from arduino.app_bricks import sketch`
    then `return sketch.tapCount`. Returns 0 (no taps yet) until then."""
    return 0


def poll_bridge_and_capture(capture: AudioCapture) -> None:
    """Not yet called from App.run() — see module docstring. Once
    read_tap_count() is real, wiring this in is just calling it from
    whatever hook App Lab provides for a per-loop/per-tick callback."""
    last_tap_count = read_tap_count()
    while True:
        tap_count = read_tap_count()
        if tap_count != last_tap_count:
            last_tap_count = tap_count
            capture.notify_tap()
        App.delay(10)  # unverified helper name/signature


App.run()
