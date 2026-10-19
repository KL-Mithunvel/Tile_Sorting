// Nano firmware: ToF-triggered ball-drop striker + conveyor belt PWM, driven by
// the Raspberry Pi over USB serial (documents/programming/nano_serial_protocol.md).
//
// All of the logic is in nano_core.h (portable C++, tested on a laptop against
// the real Python protocol module). This file is only the board glue: pins,
// the two ToF sensors, serial, PWM.
//
// Compiled for AVR and flashed to a real Nano on 2026-10-19 (boots, speaks the protocol); the ToF
// and the solenoid have NOT been exercised. Everything marked UNVERIFIED below is written from
// documentation:
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

// Which striker is fitted. 1 = the PROTOTYPE (decided 2026-10-19): ONE sprung solenoid above the
// tile on PIN_SOLENOID_ARM, energised = retracted, released = strike; PIN_SOLENOID_LOCK is unused.
// 0 = the full-system ARM + LOCK ball drop. Hand-turned belt in the prototype: no belt-end ToF
// is required (the belt command has nothing to drive).
#define NANO_STRIKER_SPRUNG 1

// Which ToF sensor is fitted. 1 = the PROTOTYPE's single "TINY LIDAR" VL53L0X module (I2C, A4/A5,
// 2 m range), library "VL53L0X" by Pololu: one sensor, default address 0x29, XSHUT not used.
// 0 = the full system's two VL53L1X (re-addressed at boot). The prototype has no belt-end sensor.
#define NANO_TOF_VL53L0X 1

// A plain #include (not behind __has_include): arduino-cli finds the library to link from the
// include lines, and silently links NOTHING if the include is guarded -- the first 2026-10-19 build
// did exactly that (no ToF code in the flashed firmware). The library must therefore be installed:
//   arduino-cli lib install VL53L0X      (Pololu)   [prototype]   or   VL53L1X
#if NANO_TOF_VL53L0X
#include <VL53L0X.h>
#else
#include <VL53L1X.h>
#endif
#define NANO_HAVE_TOF 1

// ---- pins (see the header comment: two are new and unconfirmed) -----------------
const uint8_t PIN_TOF_STRIKER_XSHUT = 7;   // ToF 1 (striker station), from sketch.ino
const uint8_t PIN_TOF_END_XSHUT     = 12;  // ToF 3 (belt end)         NEW, UNCONFIRMED (D8 stays reserved for the arm limit switch)
const uint8_t PIN_SOLENOID_ARM      = 5;   // from sketch.ino
const uint8_t PIN_SOLENOID_LOCK     = 6;   // from sketch.ino
const uint8_t PIN_ESTOP             = 2;   // normally-closed loop, INPUT_PULLUP; LOW = tripped
const uint8_t PIN_TAP_LED           = 4;
const uint8_t PIN_STATUS_LED        = 13;
const uint8_t PIN_BELT_PWM          = 9;   // PWM-capable (Timer1)     NEW, UNCONFIRMED

// Prototype striker: the ONE solenoid is driven by a Cytron MDD10A (PWM + DIR), not a bare
// MOSFET (owner, 2026-10-19). Energised = PWM duty on, DIR held at one level; the duty can be
// lowered once the striker is up, to cut heating and the coil's own hum. D10 shares Timer1 with D9.
const uint8_t PIN_STRIKER_PWM       = 10;  // MDD10A PWM input         owner-confirmed 2026-10-19
const uint8_t PIN_STRIKER_DIR       = 8;   // MDD10A DIR input         owner-confirmed (was reserved for the arm limit switch)
// PROVISIONAL, all three unmeasured. Pull in hard, then hold. TOO LOW A HOLD DROPS THE STRIKER
// BY ITSELF: that is a strike the Nano never reports. Raise STRIKER_HOLD_PCT until it holds for
// the whole of retract_hold_ms and retract_max_ms with margin, then check the coil is not hot.
const uint8_t  STRIKER_PULL_PCT = 100;     // duty while the striker is being pulled up
const uint16_t STRIKER_PULL_MS  = 150;     // how long that lasts
// (the hold duty is set by nano::Config::hold_pct and the Pi's HOLD n command, not here)

// Two VL53L1X boot at 0x29. ToF 1 is moved to 0x30 at every boot while ToF 3 is
// held in reset (documents/electrical/i2c_bus_plan.md 3.1); ToF 3 keeps 0x29.
const uint8_t TOF_STRIKER_ADDRESS = 0x30;

#ifdef NANO_HAVE_TOF
#if NANO_TOF_VL53L0X
VL53L0X tofStriker;                 // the only sensor; sensor 3 (belt end) does not exist in the prototype
bool tofStrikerUp = false;
uint16_t tofLastMm = 0xFFFF;        // latest reading
uint32_t tofLastAt = 0;             // when it arrived (millis)
const uint16_t TOF_STALE_MS = 300;  // no new reading for this long = the sensor stopped answering
#else
VL53L1X tofStriker;
VL53L1X tofEnd;
bool tofStrikerUp = false, tofEndUp = false;
#endif
#endif

class NanoHal : public nano::Hal {
 public:
  uint32_t nowMs() override { return millis(); }

  void setBelt(uint8_t percent) override {
    analogWrite(PIN_BELT_PWM, (uint16_t)percent * 255 / 100);
  }
#if NANO_STRIKER_SPRUNG
  // THE striker coil, through the MDD10A: full duty to pull it up, a lower hold duty after.
  void setArm(bool on) override {
    strikerUp_ = on;
    strikerHeld_ = false;
    if (on) {
      strikerAt_ = millis();
      analogWrite(PIN_STRIKER_PWM, (uint16_t)STRIKER_PULL_PCT * 255 / 100);
    } else {
      analogWrite(PIN_STRIKER_PWM, 0);   // released: gravity + spring
    }
  }
  // Called every pass of loop(): step the duty down once the pull-in time is over.
  void service() {
    if (strikerUp_ && !strikerHeld_ && (uint32_t)(millis() - strikerAt_) >= STRIKER_PULL_MS) {
      strikerHeld_ = true;
      analogWrite(PIN_STRIKER_PWM, (uint16_t)holdPct_ * 255 / 100);
    }
  }
  void setHoldPercent(uint8_t percent) override {
    holdPct_ = percent;
    if (strikerUp_ && strikerHeld_) analogWrite(PIN_STRIKER_PWM, (uint16_t)holdPct_ * 255 / 100);   // applies live
  }
#else
  void setArm(bool on) override { digitalWrite(PIN_SOLENOID_ARM, on ? HIGH : LOW); }
  void service() {}
#endif
  void setLock(bool on) override { digitalWrite(PIN_SOLENOID_LOCK, on ? HIGH : LOW); }
  void setTapLed(bool on) override { digitalWrite(PIN_TAP_LED, on ? HIGH : LOW); }
  void setStatusLed(bool on) override { digitalWrite(PIN_STATUS_LED, on ? HIGH : LOW); }
  bool estopAsserted() override { return digitalRead(PIN_ESTOP) == LOW; }

  bool readTof(int sensor, uint16_t* mm) override {
#ifdef NANO_HAVE_TOF
#if NANO_TOF_VL53L0X
    if (sensor != nano::TOF_STRIKER || !tofStrikerUp) return false;   // sensor 3 does not exist here
    // Non-blocking: take a result only when the sensor says one is ready, otherwise hand back
    // the last one. No result for TOF_STALE_MS means it stopped answering (unplugged, bus fault).
    if ((tofStriker.readReg(VL53L0X::RESULT_INTERRUPT_STATUS) & 0x07) != 0) {
      uint16_t d = tofStriker.readRangeContinuousMillimeters();
      if (!tofStriker.timeoutOccurred()) {
        tofLastMm = (d > 2000) ? 0xFFFF : d;   // beyond its 2 m range: the sensor answered, nothing is there
        tofLastAt = millis();
      }
    }
    if ((uint32_t)(millis() - tofLastAt) > TOF_STALE_MS) return false;
    *mm = tofLastMm;
    return true;
#else
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
#endif
#else
    (void)sensor; (void)mm;
    return false;  // no library: "not answering" -> belt held, no strikes
#endif
  }

  void writeLine(const char* line) override { Serial.write(line); }

 private:
  bool strikerUp_ = false, strikerHeld_ = false;
  uint8_t holdPct_ = 30;
  uint32_t strikerAt_ = 0;
};

static nano::Config makeConfig() {
#if NANO_STRIKER_SPRUNG
  nano::Config c = nano::prototypeConfig();   // one sprung striker, one ToF, <= 75 mm = tile, strike 3 s later
  // c.retract_hold_ms / retract_max_ms / strike_ms: PROVISIONAL until measured on the rig
  // (how long the retraction noise rings, the coil's duty rating, how long the strike takes).
#else
  nano::Config c = nano::defaultConfig();
#endif
  return c;
}

NanoHal hal;
nano::Core core(hal, makeConfig());

void setup() {
  // Outputs first, in the safe state, before anything that can hang.
#if NANO_STRIKER_SPRUNG
  pinMode(PIN_STRIKER_PWM, OUTPUT);
  analogWrite(PIN_STRIKER_PWM, 0);        // coil off = striker DOWN
  pinMode(PIN_STRIKER_DIR, OUTPUT);
  digitalWrite(PIN_STRIKER_DIR, HIGH);    // fixed: only the PWM energises it
#if defined(__AVR__)
  // Timer1 (D9, D10) to ~31 kHz: the default ~490 Hz would hum in the coil at the hold duty,
  // and the microphone would record that as part of every tap.
  TCCR1B = (TCCR1B & 0xF8) | 0x01;
#endif
#else
  pinMode(PIN_SOLENOID_ARM, OUTPUT);
  digitalWrite(PIN_SOLENOID_ARM, LOW);
  pinMode(PIN_SOLENOID_LOCK, OUTPUT);
  digitalWrite(PIN_SOLENOID_LOCK, LOW);   // de-energized = latch CLOSED (holding)
#endif
  pinMode(PIN_BELT_PWM, OUTPUT);
  analogWrite(PIN_BELT_PWM, 0);
  pinMode(PIN_TAP_LED, OUTPUT);
  pinMode(PIN_STATUS_LED, OUTPUT);
  pinMode(PIN_ESTOP, INPUT_PULLUP);

  Serial.begin(115200);

#ifdef NANO_HAVE_TOF
  Wire.begin();
#if NANO_TOF_VL53L0X
  // One module on the I2C bus at its default 0x29 (A4 = SDA, A5 = SCL). UNVERIFIED: the Pololu
  // VL53L0X calls below are from the library's documented API; nothing has been wired yet.
  tofStriker.setTimeout(100);
  if (tofStriker.init()) {
    tofStriker.setMeasurementTimingBudget(20000);   // ~20 ms per reading: fast enough for the 25 ms poll
    tofStriker.startContinuous(25);
    tofStrikerUp = true;
    tofLastAt = millis();
  }
#else
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
#endif

  core.begin();   // sends HELLO
}

void loop() {
  while (Serial.available() > 0) {
    core.feedByte((char)Serial.read());
  }
  core.update();
  hal.service();
}
