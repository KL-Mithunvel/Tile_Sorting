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
//  *** STATUS (2026-10-18): compiled for the UNO Q; transport and boot verified over
//  the Bridge. NO MOTOR HAS BEEN DRIVEN from this. ***
//  gantry_core.h is verified on a PC (host build). The glue below follows the bench
//  sketches (stepper/io/encoder smoke tests). Open items are listed in README.md next
//  to this file. Do not power motors from this until the bench checklist
//  (documents/pick_place_todo.md section 0) is done.
//
//  Transport: on the UNO Q the Python side reaches the MCU through the App Lab Bridge
//  (arduino-router), NOT through `Serial` (that is the router's monitor). So the
//  protocol lines travel as two Bridge calls:
//    g_cmd(String line)  Pi -> MCU, one protocol line (queued, run from loop())
//    g_poll()            MCU -> Pi, returns the queued reply lines, '\n'-separated
//  The Bridge callbacks only touch the two ring buffers below; all gantry logic runs
//  in loop() or the step timer, exactly as before.
//
//  Safe by default: drivers are only enabled by the core once the e-stop loop is
//  closed, and STEP pulses only come from commanded, homed or homing motion.
// ============================================================================

#include <Arduino.h>
#include <Wire.h>
#include "Arduino_RouterBridge.h"
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

// AS5600 encoders behind the TCA9548A. On since 2026-10-18 (detect-only feedback: lost-step
// alarms, and a homing seek must make its encoder move). GANTRY_ENCODER_MASK says which are
// FITTED: bit0 X, bit1 Y, bit2 Z, bit3 A (the Y clone). 0x06 = Y and Z only (one Y motor, no clone,
// X encoder not fitted); an
// encoder in the mask that does not answer is an ENCODER_<axis> alarm, so only list fitted ones.
#ifndef GANTRY_ENCODERS_ENABLED
#define GANTRY_ENCODERS_ENABLED 1
#endif
#ifndef GANTRY_ENCODER_MASK
#define GANTRY_ENCODER_MASK 0x06   // Y, Z. X reads nothing on mux ch7 (scan 2026-10-18): not fitted
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
// TCA9548A mux channel of each encoder index (X, Y, Z, A). Bench wiring 2026-10-18:
// X on ch7, Y on ch5, Z on ch3, A (Y' clone) on ch6; ToF 1 is on ch0.
// The header pins A4/A5 are `Wire2` on the UNO Q Zephyr core, NOT `Wire` (found with
// bench_tests/i2c_scan_test: mux 0x70 answers on Wire2 only).
#define GANTRY_WIRE Wire2
static const uint8_t MUX_ADDR = 0x70;
static const uint8_t AS5600_ADDR = 0x36;
static const uint8_t MUX_CHANNEL[gantry::ENCODERS] = {7, 5, 3, 6};
#endif

// ============================== board glue ===============================================

struct McuHal : gantry::Hal {
  unsigned int irqKey = 0;
  int lockDepth = 0;

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
    GANTRY_WIRE.beginTransmission(MUX_ADDR);
    GANTRY_WIRE.write((uint8_t)(1u << MUX_CHANNEL[i]));
    if (GANTRY_WIRE.endTransmission() != 0) return false;
    if (GANTRY_WIRE.requestFrom((int)MUX_ADDR, 1) != 1) return false;
    if ((uint8_t)GANTRY_WIRE.read() != (uint8_t)(1u << MUX_CHANNEL[i])) return false;
    // RAW ANGLE, registers 0x0C (high 4 bits) / 0x0D (low 8 bits).
    GANTRY_WIRE.beginTransmission(AS5600_ADDR);
    GANTRY_WIRE.write((uint8_t)0x0C);
    if (GANTRY_WIRE.endTransmission(false) != 0) return false;
    if (GANTRY_WIRE.requestFrom((int)AS5600_ADDR, 2) != 2) return false;
    uint16_t hi = (uint8_t)GANTRY_WIRE.read();
    uint16_t lo = (uint8_t)GANTRY_WIRE.read();
    *raw = (uint16_t)(((hi & 0x0F) << 8) | lo);
    return true;
#else
    (void)i; (void)raw;
    return false;
#endif
  }

  void write(const char* line) override;  // queues for g_poll(); defined below
  uint32_t nowMs() override { return millis(); }
  // Nest-safe: only the outermost lock's key is restored, so a core that locks inside a
  // lock cannot leave interrupts masked.
  void isrLock() override {
    unsigned int k = irq_lock();
    if (lockDepth++ == 0) irqKey = k;
  }
  void isrUnlock() override {
    if (--lockDepth == 0) irq_unlock(irqKey);
  }
};

static McuHal hal;
static gantry::Config cfg;
static gantry::Gantry* machine = nullptr;

// ---- step generator timer -----------------------------------------------------------------
static struct k_timer stepTimer;
static void stepTimerExpiry(struct k_timer*) {
  if (machine) machine->tick();
}

// ---- Bridge transport ----------------------------------------------------------------------
// MCU -> Pi: reply lines (written by the core, possibly from the step timer) wait in outBuf
// until the Pi calls g_poll(). A line that does not fit is dropped whole and counted; the
// Pi sees that as silence/missing reply and its own timeouts handle it.
static const size_t OUT_CAP = 3072;
static const size_t POLL_MAX = 480;   // bytes returned per g_poll() (bridge message size)
static char outBuf[OUT_CAP];
static size_t outHead = 0, outTail = 0;   // consume at head, produce at tail
static unsigned long outDropped = 0;

static size_t outUsed() { return (outTail + OUT_CAP - outHead) % OUT_CAP; }

void McuHal::write(const char* line) {
  size_t n = strlen(line);
  unsigned int k = irq_lock();
  if (OUT_CAP - 1 - outUsed() >= n + 1) {
    for (size_t i = 0; i < n; i++) { outBuf[outTail] = line[i]; outTail = (outTail + 1) % OUT_CAP; }
    outBuf[outTail] = '\n'; outTail = (outTail + 1) % OUT_CAP;
  } else {
    outDropped++;
  }
  irq_unlock(k);
}

// Pi -> MCU: whole lines, queued by the Bridge callback, run from loop().
static const size_t IN_SLOTS = 8;
static const size_t IN_LEN = 96;
static char inBuf[IN_SLOTS][IN_LEN];
static size_t inHead = 0, inTail = 0;
static unsigned long inDropped = 0;

void bridgeCmd(String line) {
  unsigned int k = irq_lock();
  size_t next = (inTail + 1) % IN_SLOTS;
  if (next != inHead && line.length() < IN_LEN) {
    strncpy(inBuf[inTail], line.c_str(), IN_LEN - 1);
    inBuf[inTail][IN_LEN - 1] = 0;
    inTail = next;
  } else {
    inDropped++;
  }
  irq_unlock(k);
}

String bridgePoll() {
  String r;
  unsigned int k = irq_lock();
  while (outUsed() > 0) {
    // length of the next whole line
    size_t len = 0, p = outHead;
    while (outBuf[p] != '\n') { p = (p + 1) % OUT_CAP; len++; }
    if (r.length() + len + 1 > POLL_MAX) break;
    for (size_t i = 0; i < len; i++) { r += outBuf[outHead]; outHead = (outHead + 1) % OUT_CAP; }
    outHead = (outHead + 1) % OUT_CAP;  // the '\n'
    r += '\n';
  }
  irq_unlock(k);
  return r;
}

static void pumpInput() {
  while (inHead != inTail) {
    machine->onLine(inBuf[inHead]);
    inHead = (inHead + 1) % IN_SLOTS;
  }
}

void setup() {
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
  // Pins are safe (drivers off, vacuum off) BEFORE the Bridge starts, in case it blocks.
  Bridge.begin();
  Bridge.provide("g_cmd", bridgeCmd);
  Bridge.provide("g_poll", bridgePoll);
#if GANTRY_ENCODERS_ENABLED
  GANTRY_WIRE.begin();
  GANTRY_WIRE.setClock(100000);  // plan 3.2: 100 kHz first (motor noise); same as the bench test
#endif

  cfg = gantry::Config::defaults();
  cfg.link_timeout_ms = 500;  // the Pi's runner PINGs every 200 ms during a motion
  cfg.encoders_enabled = GANTRY_ENCODERS_ENABLED != 0;
  cfg.encoder_mask = GANTRY_ENCODER_MASK;
  static gantry::Gantry instance(hal, cfg);
  machine = &instance;
  machine->begin();

  k_timer_init(&stepTimer, stepTimerExpiry, NULL);
  k_timer_start(&stepTimer, K_USEC(cfg.tick_us), K_USEC(cfg.tick_us));
}

void loop() {
  pumpInput();
  machine->poll();
}
