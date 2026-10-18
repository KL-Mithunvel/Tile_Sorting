import time
from arduino.app_utils import Bridge

while True:
    try:
        print("SCAN", Bridge.call("i2c_scan"), flush=True)
    except Exception as e:
        print("SCAN call failed:", e, flush=True)
    time.sleep(2)
