"""Arduino App Lab entry point for the stepper-library spike.

MCU-only bench test - everything happens in sketch/sketch.ino and its result
table is read from the serial monitor (`arduino-app-cli monitor` on the board).
This Python side only keeps the app alive, same as stepper_smoke_test.
"""

from arduino.app_utils import App

App.run()
