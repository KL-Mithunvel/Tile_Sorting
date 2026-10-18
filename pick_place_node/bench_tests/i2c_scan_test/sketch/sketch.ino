// I2C bus finder. Scans Wire, Wire1 and Wire2 (UNO Q Zephyr core: i2c2, i2c4, i2c3
// in the board's bus order; A4/A5 = PC1/PC0 = i2c3 -> Wire2 per the variant overlay).
// Bridge first, Wire used only in loop(), "stage" shows where it is.
// NOT the gantry firmware. 2026-10-18.
#include <Wire.h>
#include "Arduino_RouterBridge.h"

String result = "boot";
String stage = "setup";
unsigned long scanNo = 0;

String bridgeScan() { return String(scanNo) + " | " + stage + " | " + result; }

String scanBus(TwoWire &w, const char *name) {
  String found = "";
  for (int addr = 1; addr < 127; addr++) {
    stage = String(name) + " 0x" + String(addr, HEX);
    w.beginTransmission(addr);
    if (w.endTransmission() == 0) found += "0x" + String(addr, HEX) + " ";
  }
  return String(name) + ": " + (found.length() ? found : String("-")) + " ";
}

bool started = false;

void setup() {
  Bridge.begin();
  Bridge.provide("i2c_scan", bridgeScan);
  stage = "idle";
}

void loop() {
  if (!started) {
    delay(2000);
    stage = "begin";
    Wire.begin();
    Wire1.begin();
    Wire2.begin();
    started = true;
  }
  String r = scanBus(Wire, "Wire") + scanBus(Wire1, "Wire1") + scanBus(Wire2, "Wire2");
  result = r;
  scanNo++;
  stage = "idle";
  delay(1000);
}
