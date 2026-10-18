// ============================================================================
//  Encoder smoke test  —  pick_place_node bench bring-up
// ============================================================================
//
//  Purpose: confirm that each of the 4 AS5600 encoders (X, Y, Y-clone, Z)
//  tracks its stepper motor. Reads the encoders through the TCA9548A I2C mux,
//  drives the same STEP/DIR pins as stepper_smoke_test, and reports the
//  measured encoder movement for a 1-revolution move out and back. Pass/fail
//  is decided on the Python side (config.yaml tolerance), not here.
//  This is NOT the real gantry firmware: no acceleration, no homing, no limit
//  switches, no e-stop handling.
//
//  WRITTEN FROM DOCUMENTATION. NEVER COMPILED OR RUN ON THE BOARD. Unverified:
//   - (resolved 2026-10-18) A4/A5 are NOT `Wire` on the UNO Q Zephyr core: they are
//     i2c3 = `Wire2` (mux found at 0x70 on Wire2 by bench_tests/i2c_scan_test).
//     Wire = i2c2 (D20/D21), Wire1 = i2c4. All I2C below uses Wire2.
//   - Wire read timeout behaviour (a stuck bus may block a read)
//   - steps per motor rev (3200 assumed: 200 full steps x 16 microsteps)
//
//  Wiring (documents/electrical/i2c_bus_plan.md):
//   TCA9548A 0x70 on the UNO Q I2C bus; AS5600 (0x36) on mux ch0..3:
//     ch0 = X motor, ch1 = Y motor, ch2 = Y-clone motor, ch3 = Z motor.
//   Mux RESET (active-low) on A1.
//   Steppers: CNC Shield V3 pins, as in stepper_smoke_test.
//
//  Y and Y-clone are driven by ONE STEP/DIR pair (clone jumpers), so the "Y"
//  move turns both Y motors and is checked against encoders 1 and 2.
//
//  The AS5600 reports one revolution of absolute angle (0..4095). This sketch
//  unwraps it into a running position, so it must be sampled faster than half
//  a revolution per sample; it samples every 64 steps (~11 deg of motor).
//
//  Bridge functions (Python side: Bridge.call(name, ...)):
//    enc_status()   -> one "ENC ..." status line (see statusLine())
//    enc_move(axis) -> request a 1-rev out-and-back move (0=X, 1=Y+A, 2=Z)
//    enc_stop()     -> stop a move, drivers off
//    enc_scan()     -> request an I2C scan of the 8 mux channels
//    enc_mux_reset()-> pulse the mux RESET pin
//    enc_zero()     -> zero the running positions
//  The callbacks only set request flags; all I2C and stepping happens in loop()
//  so the bus is never used from two threads.
//
//  DEPLOY  (see this folder's README.md)
//   one-time on the board:   arduino-app-cli app new encoder_smoke_test
//   from the laptop:         tools\uno_q\push-encoder-test.bat
//   on the board:            arduino-app-cli app restart ~/ArduinoApps/encoder_smoke_test -v
// ============================================================================

#include <Wire.h>
#include "Arduino_RouterBridge.h"

// --- Stepper pins (CNC Shield V3) -------------------------------------------
const int STEP_PIN[3] = { 2, 3, 4 };   // X, Y(+A), Z
const int DIR_PIN[3]  = { 5, 6, 7 };
const int EN = 8;                      // shared, ACTIVE-LOW (LOW = drivers enabled)

// Which encoder channels each axis move is checked against (bit n = channel n).
const uint8_t NUM_AXES = 3;
const uint8_t NUM_ENC = 4;

// Mux channel each encoder index (X, Y, Y-clone, Z) is actually wired to.
// Bench wiring 2026-10-18: X on ch7, Y on ch5, Y-clone on ch6, Z on ch3. Only the two
// Y encoders were fitted and read when this was set; X and Z read as failed until fitted.
// Plan default (i2c_bus_plan.md 1.1): {0, 1, 2, 3}.
const uint8_t ENC_MUX_CH[NUM_ENC] = { 7, 5, 6, 3 };

// --- I2C ---------------------------------------------------------------------
const int MUX_RESET = A1;
const uint8_t MUX_ADDR = 0x70;
const uint8_t AS5600_ADDR = 0x36;
const uint8_t TOF_ADDR = 0x29;
const uint8_t REG_STATUS = 0x0B;       // bit5 MD magnet detected, bit4 ML too weak, bit3 MH too strong
const uint8_t REG_RAW_ANGLE = 0x0C;    // 12-bit, unfiltered
const uint32_t I2C_CLOCK_HZ = 100000;  // plan 3.2: start at 100 kHz, motor noise

// --- Motion ------------------------------------------------------------------
const long STEPS_PER_REV = 3200;
const int PULSE_HALF_US = 600;
const int DIR_SETTLE_MS = 3;
const int CHUNK_STEPS = 64;            // steps between encoder samples
const unsigned long SETTLE_MS = 500;   // let the rotor stop before reading a leg
const unsigned long IDLE_POLL_MS = 20;
const unsigned long SERIAL_STATUS_MS = 500;

enum Phase { PH_IDLE, PH_FWD, PH_SETTLE1, PH_REV, PH_SETTLE2 };
enum MoveState { ST_IDLE = 0, ST_MOVING = 1, ST_DONE = 2, ST_STOPPED = 3 };

// --- State -------------------------------------------------------------------
Phase phase = PH_IDLE;
MoveState moveState = ST_IDLE;
int activeAxis = -1;
long stepsLeft = 0;
unsigned long phaseStart = 0;
unsigned long seq = 0;                 // completed-move counter

int rawAngle[NUM_ENC] = { -1, -1, -1, -1 };
int statusReg[NUM_ENC] = { -1, -1, -1, -1 };
int prevRaw[NUM_ENC] = { 0, 0, 0, 0 };
bool havePrev[NUM_ENC] = { false, false, false, false };
long posCounts[NUM_ENC] = { 0, 0, 0, 0 };
unsigned long errCount[NUM_ENC] = { 0, 0, 0, 0 };
unsigned long moveFails[NUM_ENC] = { 0, 0, 0, 0 };
long startCounts[NUM_ENC] = { 0, 0, 0, 0 };
long fwdCounts[NUM_ENC] = { 0, 0, 0, 0 };
long netCounts[NUM_ENC] = { 0, 0, 0, 0 };
bool muxOk = false;
bool driversOn = false;
int scanResult[8] = { -2, -2, -2, -2, -2, -2, -2, -2 };  // bit0 AS5600, bit1 0x29; -1 select failed; -2 not scanned

// Requests set from Bridge callbacks, served from loop().
volatile int pendingAxis = -1;
volatile bool pendingStop = false;
volatile bool pendingScan = false;
volatile bool pendingMuxReset = false;
volatile bool pendingZero = false;

unsigned long lastIdlePoll = 0;
unsigned long lastSerialStatus = 0;

// --- I2C helpers ---------------------------------------------------------------
bool selectChannel(uint8_t ch) {
  uint8_t mask = (ch < 8) ? (uint8_t)(1 << ch) : 0;
  Wire2.beginTransmission(MUX_ADDR);
  Wire2.write(mask);
  if (Wire2.endTransmission() != 0) { muxOk = false; return false; }
  // Read the channel register back: with four identical 0x36 devices a failed
  // select would still "succeed" by reading the wrong encoder.
  if (Wire2.requestFrom((int)MUX_ADDR, 1) != 1) { muxOk = false; return false; }
  muxOk = (Wire2.read() == mask);
  return muxOk;
}

bool readReg(uint8_t reg, uint8_t *buf, uint8_t n) {
  Wire2.beginTransmission(AS5600_ADDR);
  Wire2.write(reg);
  if (Wire2.endTransmission(false) != 0) return false;
  if (Wire2.requestFrom((int)AS5600_ADDR, (int)n) != n) return false;
  for (uint8_t i = 0; i < n; i++) buf[i] = Wire2.read();
  return true;
}

void failRead(uint8_t ch) {
  errCount[ch]++;
  moveFails[ch]++;
  rawAngle[ch] = -1;
  statusReg[ch] = -1;
}

void sampleEncoder(uint8_t ch) {
  uint8_t b[2];
  if (!selectChannel(ENC_MUX_CH[ch]) || !readReg(REG_STATUS, b, 1)) { failRead(ch); return; }
  statusReg[ch] = b[0];
  if (!readReg(REG_RAW_ANGLE, b, 2)) { failRead(ch); return; }
  int raw = ((b[0] & 0x0F) << 8) | b[1];
  rawAngle[ch] = raw;
  if (havePrev[ch]) {
    int d = raw - prevRaw[ch];
    if (d > 2048) d -= 4096;
    if (d < -2048) d += 4096;
    posCounts[ch] += d;
  }
  prevRaw[ch] = raw;
  havePrev[ch] = true;
}

void sampleAll() {
  for (uint8_t ch = 0; ch < NUM_ENC; ch++) sampleEncoder(ch);
}

void doScan() {
  for (uint8_t ch = 0; ch < 8; ch++) {
    int flags = 0;
    if (selectChannel(ch)) {
      Wire2.beginTransmission(AS5600_ADDR);
      if (Wire2.endTransmission() == 0) flags |= 1;
      Wire2.beginTransmission(TOF_ADDR);
      if (Wire2.endTransmission() == 0) flags |= 2;
    } else {
      flags = -1;
    }
    scanResult[ch] = flags;
  }
  selectChannel(8);  // all channels off
}

void doMuxReset() {
  digitalWrite(MUX_RESET, LOW);
  delay(2);
  digitalWrite(MUX_RESET, HIGH);
  delay(2);
}

// --- Motion ----------------------------------------------------------------------
void driversEnable(bool on) {
  driversOn = on;
  digitalWrite(EN, on ? LOW : HIGH);
}

void pulseSteps(int stepPin, int n) {
  for (int i = 0; i < n; i++) {
    digitalWrite(stepPin, HIGH);
    delayMicroseconds(PULSE_HALF_US);
    digitalWrite(stepPin, LOW);
    delayMicroseconds(PULSE_HALF_US);
  }
}

void startMove(int axis) {
  if (axis < 0 || axis >= NUM_AXES || phase != PH_IDLE) return;
  sampleAll();
  for (uint8_t ch = 0; ch < NUM_ENC; ch++) {
    startCounts[ch] = posCounts[ch];
    moveFails[ch] = 0;
    fwdCounts[ch] = 0;
    netCounts[ch] = 0;
  }
  activeAxis = axis;
  digitalWrite(DIR_PIN[axis], LOW);
  driversEnable(true);
  delay(DIR_SETTLE_MS);
  stepsLeft = STEPS_PER_REV;
  moveState = ST_MOVING;
  phase = PH_FWD;
}

void abortMove() {
  driversEnable(false);
  phase = PH_IDLE;
  moveState = ST_STOPPED;
}

void runMove() {
  switch (phase) {
    case PH_FWD:
    case PH_REV: {
      int n = (stepsLeft < CHUNK_STEPS) ? (int)stepsLeft : CHUNK_STEPS;
      pulseSteps(STEP_PIN[activeAxis], n);
      stepsLeft -= n;
      sampleAll();
      if (stepsLeft <= 0) {
        phase = (phase == PH_FWD) ? PH_SETTLE1 : PH_SETTLE2;
        phaseStart = millis();
      }
      break;
    }
    case PH_SETTLE1:
    case PH_SETTLE2: {
      sampleAll();
      if (millis() - phaseStart < SETTLE_MS) break;
      if (phase == PH_SETTLE1) {
        for (uint8_t ch = 0; ch < NUM_ENC; ch++) fwdCounts[ch] = posCounts[ch] - startCounts[ch];
        digitalWrite(DIR_PIN[activeAxis], HIGH);
        delay(DIR_SETTLE_MS);
        stepsLeft = STEPS_PER_REV;
        phase = PH_REV;
      } else {
        for (uint8_t ch = 0; ch < NUM_ENC; ch++) netCounts[ch] = posCounts[ch] - startCounts[ch];
        driversEnable(false);
        phase = PH_IDLE;
        moveState = ST_DONE;
        seq++;
      }
      break;
    }
    default:
      break;
  }
}

void serviceRequests() {
  if (pendingStop) {
    pendingStop = false;
    if (phase != PH_IDLE) abortMove(); else driversEnable(false);
  }
  if (pendingMuxReset) { pendingMuxReset = false; if (phase == PH_IDLE) doMuxReset(); }
  if (pendingScan) { pendingScan = false; if (phase == PH_IDLE) doScan(); }
  if (pendingZero) {
    pendingZero = false;
    for (uint8_t ch = 0; ch < NUM_ENC; ch++) posCounts[ch] = 0;
  }
  if (pendingAxis >= 0) {
    int axis = pendingAxis;
    pendingAxis = -1;
    startMove(axis);
  }
}

// --- Status ------------------------------------------------------------------------
long deci(long counts) { return (long)(counts * (3600.0f / 4096.0f)); }

// ENC ST=<0 idle|1 moving|2 done|3 stopped> AX=<axis|-1> SEQ=<n> MUX=<0|1> DRV=<0|1>
//     then per channel n=0..3:
//       A<n> raw 0..4095 (-1 read failed)   P<n> position, 0.1 deg   S<n> AS5600 status byte (-1 failed)
//       E<n> failed reads, total            F<n> last move out-leg, 0.1 deg
//       N<n> last move net (after return)   M<n> failed reads during last move
//     SC=<8 comma-separated channel flags, bit0 0x36 / bit1 0x29, -1 select failed> or SC=- (not scanned)
String statusLine() {
  String s = "ENC ST=";
  s += (int)moveState;
  s += " AX=";
  s += activeAxis;
  s += " SEQ=";
  s += seq;
  s += " MUX=";
  s += (muxOk ? 1 : 0);
  s += " DRV=";
  s += (driversOn ? 1 : 0);
  for (uint8_t n = 0; n < NUM_ENC; n++) {
    s += " A"; s += n; s += "="; s += rawAngle[n];
    s += " P"; s += n; s += "="; s += deci(posCounts[n]);
    s += " S"; s += n; s += "="; s += statusReg[n];
    s += " E"; s += n; s += "="; s += errCount[n];
    s += " F"; s += n; s += "="; s += deci(fwdCounts[n]);
    s += " N"; s += n; s += "="; s += deci(netCounts[n]);
    s += " M"; s += n; s += "="; s += moveFails[n];
  }
  s += " SC=";
  if (scanResult[0] == -2) {
    s += "-";
  } else {
    for (uint8_t c = 0; c < 8; c++) {
      if (c) s += ",";
      s += scanResult[c];
    }
  }
  return s;
}

// --- Bridge callbacks (set flags only; no I2C here) -----------------------------------
String bridgeStatus() { return statusLine(); }
void bridgeMove(int axis) { pendingAxis = axis; }
void bridgeStop() { pendingStop = true; }
void bridgeScan() { pendingScan = true; }
void bridgeMuxReset() { pendingMuxReset = true; }
void bridgeZero() { pendingZero = true; }

void setup() {
  Serial.begin(115200);

  for (int a = 0; a < NUM_AXES; a++) {
    pinMode(STEP_PIN[a], OUTPUT);
    digitalWrite(STEP_PIN[a], LOW);
    pinMode(DIR_PIN[a], OUTPUT);
    digitalWrite(DIR_PIN[a], LOW);
  }
  pinMode(EN, OUTPUT);
  driversEnable(false);  // drivers off until a move is requested

  // Bridge first (same order as the i2c_scan_test app, which stayed responsive
  // on this bus): a stuck I2C call must never stop the GUI from connecting.
  Bridge.begin();
  Bridge.provide("enc_status", bridgeStatus);
  Bridge.provide("enc_move", bridgeMove);
  Bridge.provide("enc_stop", bridgeStop);
  Bridge.provide("enc_scan", bridgeScan);
  Bridge.provide("enc_mux_reset", bridgeMuxReset);
  Bridge.provide("enc_zero", bridgeZero);

  pinMode(MUX_RESET, OUTPUT);
  digitalWrite(MUX_RESET, HIGH);
  Wire2.begin();
  Wire2.setClock(I2C_CLOCK_HZ);
  doMuxReset();

  delay(300);
  Serial.println("=== encoder smoke test ===");
  Serial.println("drivers stay disabled until a move is requested from the GUI");
}

void loop() {
  serviceRequests();

  if (phase == PH_IDLE) {
    unsigned long now = millis();
    if (now - lastIdlePoll >= IDLE_POLL_MS) {
      lastIdlePoll = now;
      sampleAll();
    }
  } else {
    runMove();
  }

  unsigned long now = millis();
  if (now - lastSerialStatus >= SERIAL_STATUS_MS) {
    lastSerialStatus = now;
    Serial.println(statusLine());
  }
}
