// ============================================================================
//  Pick-and-place gantry firmware  --  Arduino UNO Q MCU (STM32U585, Zephyr)
// ============================================================================
//
//  All motion, safety and protocol logic lives in gantry_core.h (portable C++,
//  compiled and tested on a PC: tests/test_gantry_firmware_conformance.py). This
//  file is only the board glue: pins, the step-timer ISR, the serial line reader,
//  the vacuum sensor and the AS5600 encoders behind the TCA9548A mux.
//
//  Protocol: documents/programming/pick_place_control_protocol.md section 5.
//  Pins:     documents/electrical/schematics/pick_place_hardware_connections_plan.md section 3.
//
//  *** STATUS: NEVER COMPILED, NEVER RUN. ***
//  Written on a machine with no arduino-cli and no Zephyr toolchain. gantry_core.h
//  IS verified (host build); everything below the "board glue" line is from the
//  datasheets and the repo's own bench sketches. Expect compile errors; the open
//  questions are listed in README.md next to this file. Do not power motors from
//  this until the bench checklist (documents/pick_place_todo.md section 0) is done.
//
//  Safe by default: drivers are only enabled by the core once the e-stop loop is
//  closed, and STEP pulses only come from commanded, homed or homing motion.
// ============================================================================

#include <Arduino.h>
#include <Wire.h>
#include <zephyr/kernel.h>
#include <zephyr/irq.h>

#include "gantry_core.h"

// ---- switches -----------------------------------------------------------------
// Measured on the bench with io_smoke_test, 2026-10-17:
//  - Limit switches X/Y/Z (INPUT_PULLUP): LOW at rest, HIGH when pressed.
//  - E-stop A0 (INPUT_PULLUP): HIGH when not pressed, LOW when pressed.
// (The wiring doc's "LOW = triggered" was wrong for the limits, right for the e-stop.)
// A0 unplugged also reads HIGH (= not pressed), so a broken e-stop wire is NOT
// detected. Limit-switch unplugged levels are not measured yet.
#ifndef GANTRY_LIMIT_TRIGGERED_LEVEL
#define GANTRY_LIMIT_TRIGGERED_LEVEL HIGH
#endif
#ifndef GANTRY_ESTOP_ASSERTED_LEVEL
#define GANTRY_ESTOP_ASSERTED_LEVEL  LOW
#endif

// Vacuum sensor on A3. UNVERIFIED sensor type: this treats it as an analog level
// that goes ABOVE the threshold once the cup has sealed (flip VAC_CONFIRM_ABOVE for
// a sensor that falls, or read it as a switch by setting the threshold to 512).
#ifndef GANTRY_VAC_CONFIRM_ABOVE
#define GANTRY_VAC_CONFIRM_ABOVE 1
#endif
#ifndef GANTRY_VAC_THRESHOLD
#define GANTRY_VAC_THRESHOLD     600   // 10-bit counts; PLACEHOLDER
#endif

// AS5600 encoders behind the TCA9548A: 0 = off (the default until they are wired).
#ifndef GANTRY_ENCODERS_ENABLED
#define GANTRY_ENCODERS_ENABLED 0
#endif

// ---- pins (CNC Shield V3.10, GRBL pinout) ----------------------------------------------
static const int PIN_STEP[3] = {2, 3, 4};
static const int PIN_DIR[3] = {5, 6, 7};
static const int PIN_EN = 8;            // shared, ACTIVE-LOW
static const int PIN_LIM[3] = {9, 10, 11};
static const int PIN_VAC_EN = 12;       // MOSFET / relay gate
static const int PIN_ESTOP = A0;        // pressed = LOW (measured 2026-10-17)
static const int PIN_VAC_SENSE = A3;

// DIR polarity per axis: flip one if that axis runs the wrong way (the core's
// "positive" is away from the home switch for X/Y and toward it for Z).
static const bool DIR_INVERT[3] = {false, false, false};

#if GANTRY_ENCODERS_ENABLED
// TCA9548A mux channel of each encoder index (X, Y, Z, A): ch0 X, ch1 Y, ch2 Y', ch3 Z.
static const uint8_t MUX_ADDR = 0x70;
static const uint8_t AS5600_ADDR = 0x36;
static const uint8_t MUX_CHANNEL[gantry::ENCODERS] = {0, 1, 3, 2};
#endif

// ============================== board glue ===============================================

struct McuHal : gantry::Hal {
  unsigned int irqKey = 0;

  void setEnable(bool on) override { digitalWrite(PIN_EN, on ? LOW : HIGH); }
  void setDir(int a, bool positive) override { digitalWrite(PIN_DIR[a], (positive != DIR_INVERT[a]) ? HIGH : LOW); }
  void stepPulse(int a) override {
    digitalWrite(PIN_STEP[a], HIGH);
    delayMicroseconds(2);  // TMC2208 needs >= 100 ns; the call overhead alone is longer
    digitalWrite(PIN_STEP[a], LOW);
  }
  bool limitTriggered(int a) override { return digitalRead(PIN_LIM[a]) == GANTRY_LIMIT_TRIGGERED_LEVEL; }
  bool estopAsserted() override { return digitalRead(PIN_ESTOP) == GANTRY_ESTOP_ASSERTED_LEVEL; }
  void setVacuum(bool on) override { digitalWrite(PIN_VAC_EN, on ? HIGH : LOW); }
  bool vacuumConfirmed() override {
    int v = analogRead(PIN_VAC_SENSE);
    return GANTRY_VAC_CONFIRM_ABOVE ? (v > GANTRY_VAC_THRESHOLD) : (v < GANTRY_VAC_THRESHOLD);
  }

  bool readEncoder(int i, uint16_t* raw) override {
#if GANTRY_ENCODERS_ENABLED
    // Select the channel, then READ IT BACK: with four identical 0x36 devices a
    // failed select would make the wrong encoder answer and still look fine.
    Wire.beginTransmission(MUX_ADDR);
    Wire.write((uint8_t)(1u << MUX_CHANNEL[i]));
    if (Wire.endTransmission() != 0) return false;
    if (Wire.requestFrom((int)MUX_ADDR, 1) != 1) return false;
    if ((uint8_t)Wire.read() != (uint8_t)(1u << MUX_CHANNEL[i])) return false;
    // RAW ANGLE, registers 0x0C (high 4 bits) / 0x0D (low 8 bits).
    Wire.beginTransmission(AS5600_ADDR);
    Wire.write((uint8_t)0x0C);
    if (Wire.endTransmission(false) != 0) return false;
    if (Wire.requestFrom((int)AS5600_ADDR, 2) != 2) return false;
    uint16_t hi = (uint8_t)Wire.read();
    uint16_t lo = (uint8_t)Wire.read();
    *raw = (uint16_t)(((hi & 0x0F) << 8) | lo);
    return true;
#else
    (void)i; (void)raw;
    return false;
#endif
  }

  void write(const char* line) override { Serial.println(line); }
  uint32_t nowMs() override { return millis(); }
  void isrLock() override { irqKey = irq_lock(); }
  void isrUnlock() override { irq_unlock(irqKey); }
};

static McuHal hal;
static gantry::Config cfg;
static gantry::Gantry* machine = nullptr;

// ---- step generator timer -----------------------------------------------------------------
static struct k_timer stepTimer;
static void stepTimerExpiry(struct k_timer*) {
  if (machine) machine->tick();
}

// ---- serial line reader (loop context) --------------------------------------------------------
static char lineBuf[96];
static size_t lineLen = 0;
static bool lineOverflow = false;

static void pumpSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n') {
      if (lineOverflow) {
        Serial.println("err 0 BAD_CMD");
      } else if (lineLen > 0) {
        lineBuf[lineLen] = 0;
        machine->onLine(lineBuf);
      }
      lineLen = 0;
      lineOverflow = false;
    } else if (c != '\r') {
      if (lineLen < sizeof lineBuf - 1) lineBuf[lineLen++] = c;
      else lineOverflow = true;
    }
  }
}

void setup() {
  Serial.begin(115200);
  for (int a = 0; a < 3; a++) {
    pinMode(PIN_STEP[a], OUTPUT);
    pinMode(PIN_DIR[a], OUTPUT);
    pinMode(PIN_LIM[a], INPUT_PULLUP);
    digitalWrite(PIN_STEP[a], LOW);
  }
  pinMode(PIN_EN, OUTPUT);
  digitalWrite(PIN_EN, HIGH);  // drivers OFF until the core has looked at the e-stop
  pinMode(PIN_VAC_EN, OUTPUT);
  digitalWrite(PIN_VAC_EN, LOW);
  pinMode(PIN_ESTOP, INPUT_PULLUP);
  pinMode(PIN_VAC_SENSE, INPUT);
#if GANTRY_ENCODERS_ENABLED
  Wire.begin();
  Wire.setClock(400000);
#endif

  cfg = gantry::Config::defaults();
  cfg.link_timeout_ms = 500;  // the Pi's runner PINGs every 200 ms during a motion
  cfg.encoders_enabled = GANTRY_ENCODERS_ENABLED != 0;
  static gantry::Gantry instance(hal, cfg);
  machine = &instance;
  machine->begin();

  k_timer_init(&stepTimer, stepTimerExpiry, NULL);
  k_timer_start(&stepTimer, K_USEC(cfg.tick_us), K_USEC(cfg.tick_us));
}

void loop() {
  pumpSerial();
  machine->poll();
}
