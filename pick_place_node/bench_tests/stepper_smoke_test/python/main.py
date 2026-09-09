"""Arduino App Lab entry point for the stepper smoke-test app.

This app is a MCU-only bench test — all the logic is in sketch/sketch.ino.
The Python side does nothing but satisfy the App Bricks project shape and
keep the app alive so `arduino-app-cli app restart` has something to run.
Mirrors pick_place_node/python/main.py's not-yet-verified App.run() pattern.

Watch the sketch's output with `arduino-app-cli monitor` on the board.
"""

from arduino.app_utils import App

App.run()
