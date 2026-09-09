// ============================================================================
//  Stepper smoke test  —  pick_place_node bench bring-up (checklist task 0.5)
// ============================================================================
//
//  Purpose: confirm that ALL FOUR stepper motors on the CNC Shield V3.10 move.
//  This is NOT the real gantry firmware. No acceleration, no homing, no limit
//  switches, no e-stop handling, no serial protocol. Bench use only, motors
//  clamped down / off the gantry.
//
//  The real firmware lives (as an empty stub for now) at
//  pick_place_node/sketch/sketch.ino. Design: documents/programming/
//  pick_place_control_protocol.md. Wiring / pin map: documents/electrical/
//  schematics/pick_place_hardware_connections_plan.md §3.
//
//  ---------------------------------------------------------------------------
//  BEFORE RUNNING  (documents/pick_place_todo.md §0.1-0.4, golden rules)
//  ---------------------------------------------------------------------------
//   1. Vref set on ALL 4 TMC2208 (~1.6 V, motor disconnected, VMOT present).
//      The A driver's Vref MUST equal the Y driver's.
//   2. MS1 AND MS2 jumpers CLOSED on all 4 drivers  (= 1/16 microstep).
//   3. A-socket clone jumpers fitted to clone Y. Without them the 4th motor
//      is never driven and will not move — that is expected, not a fault.
//   4. 12 V LRS-150-12 wired to the shield VMOT terminals, single common
//      ground to the UNO Q, inline ~8-10 A fuse on V+.
//   5. NEVER seat/unseat a driver or a motor connector with power on.
//
//  ---------------------------------------------------------------------------
//  WHAT IT DOES
//  ---------------------------------------------------------------------------
//   Firmware drives only 3 STEP/DIR pairs. The 4th motor (shield "A" socket)
//   is cloned from Y in hardware, so the "Y" phase below turns BOTH Y-beam
//   motors at once — watch both during that phase to confirm the 4th one.
//
//     phase 1:  X             -> 1 rev forward, 1 rev back
//     phase 2:  Y + A (clone) -> 1 rev forward, 1 rev back   (two motors)
//     phase 3:  Z             -> 1 rev forward, 1 rev back
//     pause 3 s, repeat.
//
//   Send any character over the serial monitor to STOP (drivers disabled).
//   Reset the board to start again.
//
//   To test the A motor in isolation from Y (e.g. to find a bad A driver):
//   pull the Y driver, leave A seated, run again — only A should turn in
//   the "Y" phase.
//
//  ---------------------------------------------------------------------------
//  DEPLOY  (see this folder's README.md)
//  ---------------------------------------------------------------------------
//   one-time on the board:   arduino-app-cli app new stepper_smoke_test
//   from the laptop:         tools\uno_q\push-stepper-test.bat
//   on the board:            arduino-app-cli app restart ~/ArduinoApps/stepper_smoke_test -v
//                            arduino-app-cli monitor
// ============================================================================

// --- CNC Shield V3 fixed pin map (Uno header) ------------------------------
const int X_STEP = 2, X_DIR = 5;
const int Y_STEP = 3, Y_DIR = 6;   // also feeds the A socket via the clone jumper
const int Z_STEP = 4, Z_DIR = 7;
const int EN     = 8;              // shared, ACTIVE-LOW (LOW = all drivers enabled)

// --- Motion parameters ----------------------------------------------------
const long STEPS_PER_REV = 3200;   // 200 full steps * 16 microsteps
const int  PULSE_HALF_US = 600;    // half-period. 600 us ~= 0.26 rev/s.
                                   // Lower for faster, but a stopped motor may
                                   // stall below ~250 us with no ramp.
const int  DIR_SETTLE_MS  = 3;     // let DIR settle before stepping
const int  PHASE_PAUSE_MS = 400;   // between forward and reverse
const int  CYCLE_PAUSE_MS = 3000;  // between full X/Y/Z cycles

bool stopped = false;

void disableDrivers() {
  digitalWrite(EN, HIGH);          // active-low: HIGH = disabled
}

void checkStop() {
  if (!stopped && Serial.available() > 0) {
    while (Serial.available() > 0) Serial.read();
    disableDrivers();
    stopped = true;
    Serial.println("STOP requested -- drivers disabled. Reset the board to restart.");
  }
}

void stepAxis(int stepPin, int dirPin, const char *name) {
  if (stopped) return;
  Serial.print("  moving ");
  Serial.println(name);

  for (int dir = 0; dir <= 1; dir++) {
    digitalWrite(dirPin, dir == 0 ? LOW : HIGH);
    delay(DIR_SETTLE_MS);

    for (long i = 0; i < STEPS_PER_REV; i++) {
      digitalWrite(stepPin, HIGH);
      delayMicroseconds(PULSE_HALF_US);
      digitalWrite(stepPin, LOW);
      delayMicroseconds(PULSE_HALF_US);

      if ((i & 0x7F) == 0) {       // ~every 128 steps
        checkStop();
        if (stopped) return;
      }
    }
    delay(PHASE_PAUSE_MS);
  }
}

void setup() {
  Serial.begin(115200);

  const int outs[] = { X_STEP, X_DIR, Y_STEP, Y_DIR, Z_STEP, Z_DIR, EN };
  for (unsigned i = 0; i < sizeof(outs) / sizeof(outs[0]); i++) {
    pinMode(outs[i], OUTPUT);
    digitalWrite(outs[i], LOW);
  }
  digitalWrite(EN, LOW);           // enable all 4 drivers

  delay(500);
  Serial.println("=== stepper smoke test ===");
  Serial.println("X, then Y+A (clone), then Z. Send any char to stop.");
}

void loop() {
  checkStop();
  if (stopped) { delay(200); return; }

  Serial.println("cycle:");
  stepAxis(X_STEP, X_DIR, "X");
  stepAxis(Y_STEP, Y_DIR, "Y + A (cloned in hardware -- two motors)");
  stepAxis(Z_STEP, Z_DIR, "Z");

  Serial.print("cycle done, pausing ");
  Serial.print(CYCLE_PAUSE_MS / 1000);
  Serial.println(" s");

  for (int t = 0; t < CYCLE_PAUSE_MS && !stopped; t += 100) {
    checkStop();
    delay(100);
  }
}
