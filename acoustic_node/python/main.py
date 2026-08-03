"""Arduino App Lab entry point for the acoustic node.

NOT YET VERIFIED against a real App Lab environment (see acoustic_node/README.md) —
this follows the shape of Arduino's own examples (app-bricks-examples,
core-and-foundational/02-led-matrix/03-led-matrix-animation-mcu/python/main.py) but
has not been run on this project's UNO Q board. The proven, tested entry point for
this node's logic is still the CLI: `python -m acoustic.live_monitor`, run from this
python/ directory (see README.md).

Once App Lab's `arduino.app_utils.App` behavior is confirmed for this project
(what App.run() actually orchestrates, how it talks to the sketch/ side), this file
should be wired to drive acoustic.capture.AudioCapture / TriggerDetector instead of
being a bare pass-through.
"""

from arduino.app_utils import App

App.run()
