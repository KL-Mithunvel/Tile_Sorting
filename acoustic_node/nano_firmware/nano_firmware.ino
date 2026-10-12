// Nano firmware: ToF-triggered ball-drop striker + conveyor belt PWM, driven by
// the Raspberry Pi over USB serial (documents/programming/nano_serial_protocol.md).
//
// All of the logic is in nano_core.h (portable C++, tested on a laptop against
// the real Python protocol module). This file is only the board glue: pins,
// the two ToF sensors, serial, PWM.
//
// NEVER COMPILED FOR AVR AND NEVER RUN ON A NANO. Everything marked UNVERIFIED
// below is written from documentation:
//   * pin numbers -- the striker pins are carried over from the UNO Q sketch
//     (acoustic_node/sketch/sketch.ino); PIN_BELT_PWM and PIN_TOF_END_XSHUT are
//     new and are NOT yet in documents/electrical/schematics/acoustic_station_wiring.md
//   * the VL53L1X library calls (Pololu "VL53L1X" library API assumed)
//   * flash / RAM fit on the ATmega328P (2 KB RAM; two sensor objects)
//   * PWM frequency / motor driver behaviour at pin 9 (Timer1, ~490 Hz default)
//
// Without the VL53L1X library installed this still compiles and runs, but both
// sensors report "not answering": the belt stays stopped and no strike happens
// (fail-safe), and the Pi sees ERR TOF1 / TOF3.
//
// Install: Arduino IDE / arduino-cli, board "Arduino Nano" (ATmega328P; choose
// the "Old Bootloader" option for clones), library "VL53L1X" by Pololu.

#include <Arduino.h>
#include <Wire.h>

#include "nano_core.h"

#if defined(__has_include)
#if __has_include(<VL53L1X.h>)
#include <VL53L1X.h>
#define NANO_HAVE_TOF 1
#endif
#endif

// ---- pins (see the header comment: two are new and unconfirmed) -----------------
const uint8_t PIN_TOF_STRIKER_XSHUT = 7;   // ToF 1 (striker station), from sketch.ino
const uint8_t PIN_TOF_END_XSHUT     = 8;   // ToF 3 (belt end)         NEW, UNCONFIRMED
const uint8_t PIN_SOLENOID_ARM      = 5;   // from sketch.ino
const uint8_t PIN_SOLENOID_LOCK     = 6;   // from sketch.ino
const uint8_t PIN_ESTOP             = 2;   // normally-closed loop, INPUT_PULLUP; LOW = tripped
const uint8_t PIN_TAP_LED           = 4;
const uint8_t PIN_STATUS_LED        = 13;
const uint8_t PIN_BELT_PWM          = 9;   // PWM-capable (Timer1)     NEW, UNCONFIRMED

// Two VL53L1X boot at 0x29. ToF 1 is moved to 0x30 at every boot while ToF 3 is
// held in reset (documents/electrical/i2c_bus_plan.md 3.1); ToF 3 keeps 0x29.
const uint8_t TOF_STRIKER_ADDRESS = 0x30;

#ifdef NANO_HAVE_TOF
VL53L1X tofStriker;
VL53L1X tofEnd;
bool tofStrikerUp = false, tofEndUp = false;
#endif

class NanoHal : public nano::Hal {
 public:
  uint32_t nowMs() override { return millis(); }

  void setBelt(uint8_t percent) override {
    analogWrite(PIN_BELT_PWM, (uint16_t)percent * 255 / 100);
  }
  void setArm(bool on) override { digitalWrite(PIN_SOLENOID_ARM, on ? HIGH : LOW); }
  void setLock(bool on) override { digitalWrite(PIN_SOLENOID_LOCK, on ? HIGH : LOW); }
  void setTapLed(bool on) override { digitalWrite(PIN_TAP_LED, on ? HIGH : LOW); }
  void setStatusLed(bool on) override { digitalWrite(PIN_STATUS_LED, on ? HIGH : LOW); }
  bool estopAsserted() override { return digitalRead(PIN_ESTOP) == LOW; }

  bool readTof(int sensor, uint16_t* mm) override {
#ifdef NANO_HAVE_TOF
    VL53L1X& s = (sensor == nano::TOF_STRIKER) ? tofStriker : tofEnd;
    bool up = (sensor == nano::TOF_STRIKER) ? tofStrikerUp : tofEndUp;
    if (!up) return false;
    // UNVERIFIED: continuous ranging was started in setup(); read(false) returns the
    // latest result without blocking, timeoutOccurred() flags a sensor that stopped.
    uint16_t d = s.read(false);
    if (s.timeoutOccurred()) return false;
    // "No target" / out of range: the sensor answered, nothing is there.
    if (s.ranging_data.range_status != VL53L1X::RangeValid) d = 0xFFFF;
    *mm = d;
    return true;
#else
    (void)sensor; (void)mm;
    return false;  // no library: "not answering" -> belt held, no strikes
#endif
  }

  void writeLine(const char* line) override { Serial.write(line); }
};

NanoHal hal;
nano::Core core(hal, nano::defaultConfig());

void setup() {
  // Outputs first, in the safe state, before anything that can hang.
  pinMode(PIN_SOLENOID_ARM, OUTPUT);
  digitalWrite(PIN_SOLENOID_ARM, LOW);
  pinMode(PIN_SOLENOID_LOCK, OUTPUT);
  digitalWrite(PIN_SOLENOID_LOCK, LOW);   // de-energized = latch CLOSED (holding)
  pinMode(PIN_BELT_PWM, OUTPUT);
  analogWrite(PIN_BELT_PWM, 0);
  pinMode(PIN_TAP_LED, OUTPUT);
  pinMode(PIN_STATUS_LED, OUTPUT);
  pinMode(PIN_ESTOP, INPUT_PULLUP);

  Serial.begin(115200);

#ifdef NANO_HAVE_TOF
  Wire.begin();
  pinMode(PIN_TOF_STRIKER_XSHUT, OUTPUT);
  pinMode(PIN_TOF_END_XSHUT, OUTPUT);
  digitalWrite(PIN_TOF_STRIKER_XSHUT, LOW);   // both held in reset
  digitalWrite(PIN_TOF_END_XSHUT, LOW);
  delay(10);

  digitalWrite(PIN_TOF_STRIKER_XSHUT, HIGH);  // wake ToF 1 alone at 0x29, move it away
  delay(10);
  tofStriker.setTimeout(100);
  if (tofStriker.init()) {
    tofStriker.setAddress(TOF_STRIKER_ADDRESS);
    tofStriker.setDistanceMode(VL53L1X::Short);
    tofStriker.setMeasurementTimingBudget(20000);
    tofStriker.startContinuous(25);
    tofStrikerUp = true;
  }

  digitalWrite(PIN_TOF_END_XSHUT, HIGH);      // now ToF 3 wakes at 0x29, free
  delay(10);
  tofEnd.setTimeout(100);
  if (tofEnd.init()) {
    tofEnd.setDistanceMode(VL53L1X::Short);
    tofEnd.setMeasurementTimingBudget(20000);
    tofEnd.startContinuous(25);
    tofEndUp = true;
  }
#endif

  core.begin();   // sends HELLO
}

void loop() {
  while (Serial.available() > 0) {
    core.feedByte((char)Serial.read());
  }
  core.update();
}
