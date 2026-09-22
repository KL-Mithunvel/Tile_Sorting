// ============================================================================
//  I/O smoke test  —  pick_place_node bench bring-up (checklist tasks 0.1/0.6)
// ============================================================================
//
//  Purpose: exercise 3 circuits on the CNC Shield V3.10 ahead of the real
//  firmware — E-stop sense (A0), 3 limit switches (D9/D10/D11), and the
//  vacuum-solenoid output (D12) — from a GUI instead of a serial terminal.
//  This is NOT the real gantry firmware. No stepping, no homing, no FSM.
//  Stepper drivers are held DISABLED (EN=HIGH) for the whole test since this
//  sketch never touches STEP/DIR.
//
//  The real firmware lives (as an empty stub for now) at
//  pick_place_node/sketch/sketch.ino. Design: documents/programming/
//  pick_place_control_protocol.md. Wiring / pin map: documents/electrical/
//  schematics/pick_place_hardware_connections_plan.md §3.
//
//  ---------------------------------------------------------------------------
//  KNOWN DISCREPANCY — read before trusting "triggered" in the GUI
//  ---------------------------------------------------------------------------
//  The wiring doc's pin table claims "LOW = triggered / wire broken" for the
//  limit switches and e-stop. For an NC switch wired signal->GND with
//  INPUT_PULLUP, that's backwards on paper: an actuated NC switch (or a
//  broken wire) OPENS the circuit, which the pull-up reads as HIGH, not LOW.
//  So this sketch deliberately reports the RAW pin level (0/1 = LOW/HIGH),
//  not a pre-guessed "triggered" label -- press each switch by hand and watch
//  which level the GUI shows. That's the real polarity for this wiring, not
//  whatever the doc currently says. Update the doc once confirmed.
//
//  ---------------------------------------------------------------------------
//  PROTOCOL  (bench-only subset of documents/programming/
//  pick_place_control_protocol.md §5 -- no seq numbers, no motion commands,
//  just enough to read 4 circuits and drive one output)
//  ---------------------------------------------------------------------------
//   MCU -> Linux, ~5 Hz:
//     IO LIM_X=<0|1> LIM_Y=<0|1> LIM_Z=<0|1> ESTOP=<0|1> VAC=<0|1>
//       (LIM_*/ESTOP are RAW digitalRead results: 0=LOW, 1=HIGH -- see the
//        discrepancy note above. VAC is the commanded output state.)
//   Linux -> MCU:
//     VAC ON   -> drives D12 HIGH, replies "ok VAC ON"
//     VAC OFF  -> drives D12 LOW,  replies "ok VAC OFF"
//     anything else -> "err BAD_CMD <line>"
//
//  ---------------------------------------------------------------------------
//  BEFORE RUNNING
//  ---------------------------------------------------------------------------
//   1. E-stop loop's NC contact wired A0<->GND; limit switches' NC contacts
//      wired D9/D10/D11<->GND (hardware doc §3).
//   2. Vacuum MOSFET/relay gate on D12, flyback diode across the coil.
//   3. Motors can stay connected -- EN is forced HIGH (disabled) below -- but
//      there's no reason to have the motor PSU live for this test.
//
//  ---------------------------------------------------------------------------
//  DEPLOY  (see this folder's README.md)
//  ---------------------------------------------------------------------------
//   one-time on the board:   arduino-app-cli app new io_smoke_test
//   from the laptop:         tools\uno_q\push-io-test.bat
//   on the board:            arduino-app-cli app restart ~/ArduinoApps/io_smoke_test -v
// ============================================================================

const int LIM_X = 9, LIM_Y = 10, LIM_Z = 11;
const int ESTOP = A0;
const int VAC_EN = 12;
const int EN = 8;  // shared stepper enable, active-LOW -- held HIGH (disabled) throughout

const unsigned long STATUS_INTERVAL_MS = 200;  // ~5 Hz

bool vacOn = false;
unsigned long lastStatusAt = 0;
String inputLine = "";

void setup() {
  Serial.begin(115200);

  pinMode(LIM_X, INPUT_PULLUP);
  pinMode(LIM_Y, INPUT_PULLUP);
  pinMode(LIM_Z, INPUT_PULLUP);
  pinMode(ESTOP, INPUT_PULLUP);

  pinMode(VAC_EN, OUTPUT);
  digitalWrite(VAC_EN, LOW);  // off

  pinMode(EN, OUTPUT);
  digitalWrite(EN, HIGH);  // active-low: HIGH = all 4 stepper drivers disabled

  delay(300);
  Serial.println("=== io smoke test ===");
  Serial.println("reporting raw pin levels -- see sketch header re: doc discrepancy");
  Serial.println("commands: VAC ON | VAC OFF");
}

void sendStatus() {
  Serial.print("IO LIM_X=");
  Serial.print(digitalRead(LIM_X));
  Serial.print(" LIM_Y=");
  Serial.print(digitalRead(LIM_Y));
  Serial.print(" LIM_Z=");
  Serial.print(digitalRead(LIM_Z));
  Serial.print(" ESTOP=");
  Serial.print(digitalRead(ESTOP));
  Serial.print(" VAC=");
  Serial.println(vacOn ? 1 : 0);
}

void handleLine(String line) {
  line.trim();
  line.toUpperCase();
  if (line == "VAC ON") {
    vacOn = true;
    digitalWrite(VAC_EN, HIGH);
    Serial.println("ok VAC ON");
  } else if (line == "VAC OFF") {
    vacOn = false;
    digitalWrite(VAC_EN, LOW);
    Serial.println("ok VAC OFF");
  } else if (line.length() > 0) {
    Serial.print("err BAD_CMD ");
    Serial.println(line);
  }
}

void loop() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n') {
      handleLine(inputLine);
      inputLine = "";
    } else if (c != '\r') {
      inputLine += c;
    }
  }

  unsigned long now = millis();
  if (now - lastStatusAt >= STATUS_INTERVAL_MS) {
    lastStatusAt = now;
    sendStatus();
  }
}
