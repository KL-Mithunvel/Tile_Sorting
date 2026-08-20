// Acoustic node MCU sketch — ToF-triggered dual-solenoid ball-drop tap sequencer.
//
// Mechanism (documents/project/Acoustic_Sorting_Subsystem.md §3.1,
// documents/electrical/schematics/acoustic_station_wiring.md — full pin table,
// BOM, and wiring diagram there; decided 2026-08-20):
//   A ToF sensor confirms a tile has arrived and settled at the acoustic
//   station. Two solenoids then run a gravity-drop impact -- neither one
//   touches the tile directly:
//     ARM solenoid  -- energized briefly, drives a lift platform that raises
//                       the striker ball up a guide tube into the LOCK
//                       latch's catch at drop height, then de-energizes and
//                       retracts out of the way.
//     LOCK solenoid -- normally de-energized = latch CLOSED (holding the
//                       ball at drop height). Energizing it briefly OPENS
//                       the latch, releasing the ball to free-fall under
//                       gravity onto the tile. This is the actual impact.
//   The Linux/Python side (python/main.py) is notified via the tapCount
//   bridge variable, incremented at the instant the ball is released, so it
//   can time its audio capture window relative to the real impact rather
//   than the earlier ToF detection.
//
// UNVERIFIED (see acoustic_node/README.md): this project has never run App
// Lab / App Bricks against real UNO Q hardware, so:
//   (a) the exact syntax for exposing `tapCount` as a bridge variable Python
//       can read is a best guess at the shape shown in Arduino's own
//       app-bricks-examples, not confirmed working here;
//   (b) the ToF sensor read below assumes a typical VL53L0X-style Arduino
//       library API -- install the actual library once the sensor is in
//       hand and replace readTofDistanceMm()'s body (marked TOF LIBRARY
//       CALL) with the real call.
//
// Pin assignments and timing constants mirror
// documents/electrical/schematics/acoustic_station_wiring.md -- keep both in
// sync by hand if either changes (no shared config format between this
// sketch and acoustic/config.yaml exists yet; see that module's comment).

#include <Wire.h>

// ---- Pin assignments (see wiring doc §3 for the full table + rationale) ----
const uint8_t PIN_TOF_XSHUT     = 7;   // ToF sensor hardware shutdown/reset
const uint8_t PIN_SOLENOID_ARM  = 5;   // MOSFET gate driving the ARM solenoid
const uint8_t PIN_SOLENOID_LOCK = 6;   // MOSFET gate driving the LOCK solenoid
const uint8_t PIN_ESTOP         = 2;   // normally-closed e-stop loop, INPUT_PULLUP
const uint8_t PIN_TAP_LED       = 4;   // lit for the duration of RELEASE (bench debug)
const uint8_t PIN_STATUS_LED    = 13;  // onboard LED: on = running, off = FAULT
// ToF SDA/SCL use the board's fixed I2C pins (A4/A5 on the Uno-shaped
// header) -- not user-assignable, so not listed as a constant here.

// ---- Tunable timing (mirrors acoustic/config.yaml's tap_mechanism section
// on the Python side -- that copy is for the dev-machine simulator only,
// this one is what actually runs). See wiring doc §5. ----
const uint16_t TOF_TRIGGER_DISTANCE_MM = 80;    // tile considered "in place" at/below this
const uint8_t  TOF_DEBOUNCE_SAMPLES    = 5;     // consecutive in-range reads required
const uint16_t SETTLE_MS      = 200;   // wait after tile confirmed, before arming
const uint16_t ARM_MS         = 300;   // ARM solenoid energized duration (lift travel time)
const uint16_t ARM_SETTLE_MS  = 150;   // wait after arm retracts, before releasing
const uint16_t LOCK_PULSE_MS  = 100;   // LOCK solenoid energized duration (latch open)
const uint16_t COOLDOWN_MS    = 1000;  // minimum time before the next tile can re-arm

enum TapState { WAIT_TILE, SETTLE, ARM, ARM_SETTLE, RELEASE, COOLDOWN, FAULT };

TapState state = WAIT_TILE;
unsigned long stateEnteredAt = 0;
uint8_t tofInRangeStreak = 0;
bool tileWasAbsent = true;

// Exposed to the Python side (acoustic_node/python/main.py). Incremented the
// instant the ball is released (entering RELEASE), not on ToF detection.
// UNVERIFIED bridge-variable syntax -- see header comment.
volatile long tapCount = 0;

void enterState(TapState s) {
  state = s;
  stateEnteredAt = millis();
}

bool elapsedAtLeast(uint16_t ms) {
  return millis() - stateEnteredAt >= ms;
}

bool estopAsserted() {
  return digitalRead(PIN_ESTOP) == LOW;  // normally-closed loop: LOW = open/tripped
}

// TOF LIBRARY CALL: replace with the real sensor library's read, e.g.
//   VL53L0X_RangingMeasurementData_t data;
//   tof.rangingTest(&data, false);
//   return data.RangeMilliMeter;
uint16_t readTofDistanceMm() {
  return 9999;  // placeholder: "nothing in range" until a real library is wired in
}

bool tileDetected() {
  uint16_t d = readTofDistanceMm();
  if (d <= TOF_TRIGGER_DISTANCE_MM) {
    if (tofInRangeStreak < 255) tofInRangeStreak++;
  } else {
    tofInRangeStreak = 0;
  }
  return tofInRangeStreak >= TOF_DEBOUNCE_SAMPLES;
}

void setup() {
  pinMode(PIN_TOF_XSHUT, OUTPUT);
  digitalWrite(PIN_TOF_XSHUT, HIGH);  // active: sensor powered/enabled

  pinMode(PIN_SOLENOID_ARM, OUTPUT);
  digitalWrite(PIN_SOLENOID_ARM, LOW);
  pinMode(PIN_SOLENOID_LOCK, OUTPUT);
  digitalWrite(PIN_SOLENOID_LOCK, LOW);  // de-energized = latch CLOSED (holding)

  pinMode(PIN_ESTOP, INPUT_PULLUP);
  pinMode(PIN_TAP_LED, OUTPUT);
  pinMode(PIN_STATUS_LED, OUTPUT);

  Wire.begin();
  // TOF LIBRARY CALL: tof.begin() / tof.init(); set continuous ranging mode.

  enterState(WAIT_TILE);
}

void loop() {
  if (estopAsserted()) {
    digitalWrite(PIN_SOLENOID_ARM, LOW);
    digitalWrite(PIN_SOLENOID_LOCK, LOW);  // fail closed: latch stays holding, no drop
    digitalWrite(PIN_STATUS_LED, LOW);
    enterState(FAULT);
    return;
  }

  digitalWrite(PIN_STATUS_LED, HIGH);
  bool present = tileDetected();

  switch (state) {
    case WAIT_TILE:
      if (!present) {
        tileWasAbsent = true;
      } else if (tileWasAbsent) {
        tileWasAbsent = false;
        enterState(SETTLE);
      }
      break;

    case SETTLE:
      if (elapsedAtLeast(SETTLE_MS)) {
        digitalWrite(PIN_SOLENOID_ARM, HIGH);
        enterState(ARM);
      }
      break;

    case ARM:
      if (elapsedAtLeast(ARM_MS)) {
        digitalWrite(PIN_SOLENOID_ARM, LOW);  // retract arm, out of the way
        enterState(ARM_SETTLE);
      }
      break;

    case ARM_SETTLE:
      if (elapsedAtLeast(ARM_SETTLE_MS)) {
        digitalWrite(PIN_SOLENOID_LOCK, HIGH);  // open latch: ball drops now
        tapCount++;
        digitalWrite(PIN_TAP_LED, HIGH);
        enterState(RELEASE);
      }
      break;

    case RELEASE:
      if (elapsedAtLeast(LOCK_PULSE_MS)) {
        digitalWrite(PIN_SOLENOID_LOCK, LOW);  // latch closes again
        digitalWrite(PIN_TAP_LED, LOW);
        enterState(COOLDOWN);
      }
      break;

    case COOLDOWN:
      if (elapsedAtLeast(COOLDOWN_MS)) {
        enterState(WAIT_TILE);
      }
      break;

    case FAULT:
      // Latched fault: requires a reset/power cycle to clear, not just the
      // e-stop loop reclosing -- matches charter §17's "lockout procedure
      // for maintenance" rather than auto-resuming after an e-stop pull.
      break;
  }
}
