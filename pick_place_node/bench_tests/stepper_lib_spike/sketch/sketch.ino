// ============================================================================
//  Stepper-library spike  --  pick_place_node bench bring-up (checklist 0.6)
// ============================================================================
//
//  QUESTION THIS ANSWERS (documents/pick_place_todo.md §0.6, the "single biggest
//  firmware risk"): on the UNO Q's Zephyr core, can we generate accelerated STEP
//  pulses WITHOUT blocking loop() -- so the real firmware can keep reading limit
//  switches, the e-stop and the serial protocol while an axis moves?
//
//  It runs the SAME move through each candidate and prints one comparison table:
//
//    baseline      what the core can do at all: micros() resolution, cost of a
//                  digitalWrite pair (=> maximum bit-bang step rate), accuracy of
//                  delayMicroseconds(), cost of a Serial.println(), and what
//                  tick rate a Zephyr k_timer actually delivers.
//    AccelStepper  the library, polled from loop() (only if it is installed).
//    k_timer ISR   a hand-written trapezoid generator driven by a Zephyr kernel
//                  timer callback (only if <zephyr/kernel.h> compiles).
//
//  For each stage it reports: steps produced, time taken vs the ideal trapezoid,
//  and the WORST gap between two loop() iterations while a 100 ms "limit-switch
//  print" runs in loop() -- i.e. whether loop() stayed responsive.
//
//  *** STATUS: written from documentation, NEVER COMPILED OR RUN. ***
//  It was written on a machine with no arduino-cli and no Zephyr toolchain. The
//  step-generator maths was checked off-target (see README); the C++ was not.
//  Compile errors are expected data, not failures -- see the README's table.
//
//  ---------------------------------------------------------------------------
//  SAFE BY DEFAULT
//  ---------------------------------------------------------------------------
//  DRIVERS_ENABLED is 0: EN (D8) is held HIGH (all 4 drivers OFF), so STEP pulses
//  go nowhere and NOTHING MOVES even with 12 V on the shield. That is enough to
//  answer the timing question. Set it to 1 only with the motor off the gantry
//  (README), to also see the motion physically.
//
//  Pins (shield-fixed, documents/electrical/schematics/
//  pick_place_hardware_connections_plan.md §3): X STEP D2, X DIR D5, EN D8,
//  X limit D9 (INPUT_PULLUP, raw level printed - polarity is not confirmed).
//
//  Any character sent over serial aborts: drivers disabled, no further motion.
// ============================================================================

// ---- switches you may need to flip ------------------------------------------
#define DRIVERS_ENABLED 0       // 0 = EN held HIGH, nothing moves. 1 = motor really turns.

// Set to 0 if the compiler rejects the Zephyr header / k_timer calls.
#ifndef SPIKE_ZEPHYR_TIMER
#define SPIKE_ZEPHYR_TIMER 1
#endif

// AccelStepper is used only if the library is installed (sketch.yaml / lib install).
#if __has_include(<AccelStepper.h>)
  #include <AccelStepper.h>
  #define HAVE_ACCELSTEPPER 1
#else
  #define HAVE_ACCELSTEPPER 0
#endif

#if SPIKE_ZEPHYR_TIMER
  #include <zephyr/kernel.h>
#endif

// ---- pins ---------------------------------------------------------------------
const int X_STEP = 2;
const int X_DIR  = 5;
const int EN     = 8;     // shared, ACTIVE-LOW
const int LIM_X  = 9;     // INPUT_PULLUP

// ---- the move every stage performs ------------------------------------------------
// 1/16 microstepping: 3200 steps per revolution. 6400 = 2 rev, then 2 rev back.
const long  MOVE_STEPS    = 6400;
const long  MAX_SPEED_SPS = 4000;    // steps/s  (1.25 rev/s)
const long  ACCEL_SPS2    = 8000;    // steps/s^2
const long  MIN_SPEED_SPS = 100;     // generator never goes below this (needs a nonzero start)
const float MAX_SPEED     = (float)MAX_SPEED_SPS;   // for AccelStepper and printing
const float ACCEL         = (float)ACCEL_SPS2;

// k_timer generator base tick. Must be small against the step interval at MAX_SPEED
// (250 us here). If the kernel cannot deliver it, the baseline stage will say so.
const unsigned long GEN_TICK_US = 50;

const unsigned long LIM_PRINT_US = 100000UL;   // the "loop() still works" probe

// ---- stage bookkeeping -----------------------------------------------------------------
enum Stage { ST_START, ST_ACCEL_FWD, ST_ACCEL_BACK, ST_TIMER_BEGIN, ST_TIMER_RUN, ST_REPORT, ST_DONE };
Stage stage = ST_START;
int timerLeg = 0;            // 0 = forward, 1 = back

struct StageResult {
  bool ran;
  long steps;
  unsigned long time_us;
  unsigned long ideal_us;
  unsigned long max_gap_us;
  unsigned long prints;
  unsigned long tick_min_us;   // timer stages only
  unsigned long tick_max_us;
};
StageResult resAccel, resTimer;

unsigned long stageStartUs = 0;
unsigned long lastLoopUs = 0, maxGapUs = 0, lastPrintUs = 0, printCount = 0;
int lastLim = -1;
bool aborted = false;

// baseline results
unsigned long bMicrosRes = 0;       // smallest nonzero micros() step seen
float bTogglePairUs = 0;            // one HIGH+LOW digitalWrite pair
float bDelay10Us = 0;               // measured delayMicroseconds(10)
float bPrintUs = 0;                 // one 40-char Serial.println
struct TimerProbe { unsigned long asked_us; unsigned long count; };
TimerProbe tprobe[4];
int tprobeN = 0;

// ---- helpers --------------------------------------------------------------------------------
unsigned long idealTrapezoidUs(long n, float vmax, float a) {
  float ramp = vmax * vmax / (2.0f * a);                    // steps to reach vmax
  float t;
  if (2.0f * ramp >= (float)n) t = 2.0f * sqrtf((float)n / a);   // triangular: never reaches vmax
  else t = 2.0f * vmax / a + ((float)n - 2.0f * ramp) / vmax;
  return (unsigned long)(t * 1e6f);
}

void driversEnable(bool on) {
  digitalWrite(EN, on ? LOW : HIGH);   // active-low
}

void setDir(bool forward) {
  digitalWrite(X_DIR, forward ? HIGH : LOW);
  delay(5);                            // DIR settle before the first STEP
}

void checkAbort() {
  if (!aborted && Serial.available() > 0) {
    while (Serial.available() > 0) Serial.read();
    driversEnable(false);
    aborted = true;
    Serial.println();
    Serial.println("ABORT requested -- drivers disabled. Reset the board to restart.");
  }
}

// Called every loop(): measures the worst gap between iterations and runs the
// 100 ms print that stands in for "limit switches + serial protocol + telemetry".
void loopProbe() {
  unsigned long now = micros();
  if (lastLoopUs != 0) {
    unsigned long gap = now - lastLoopUs;
    if (gap > maxGapUs) maxGapUs = gap;
  }
  lastLoopUs = now;

  if (now - lastPrintUs >= LIM_PRINT_US) {
    lastPrintUs = now;
    int lim = digitalRead(LIM_X);
    if (lim != lastLim) {                       // only a level change prints text...
      lastLim = lim;
      Serial.print(lim ? " [LIM_X=1] " : " [LIM_X=0] ");
    } else {
      Serial.print('.');                        // ...otherwise a dot, still a Serial write
    }
    printCount++;
  }
}

void probeReset() {
  stageStartUs = micros();
  lastLoopUs = 0;
  maxGapUs = 0;
  lastPrintUs = stageStartUs;
  printCount = 0;
}

// ---- baseline ------------------------------------------------------------------------------------
#if SPIKE_ZEPHYR_TIMER
static struct k_timer probeTimer;
static volatile unsigned long probeCount = 0;
static void probeExpiry(struct k_timer *) { probeCount++; }
#endif

void runBaseline() {
  Serial.println("--- baseline (drivers held disabled) ---");
  digitalWrite(EN, HIGH);                       // pulses below must not move anything

  // micros() resolution
  bMicrosRes = 0xFFFFFFFFUL;
  for (int i = 0; i < 2000; i++) {
    unsigned long a = micros(), b = a;
    unsigned long guard = 0;
    while ((b = micros()) == a && ++guard < 2000000UL) {}
    unsigned long d = b - a;
    if (d > 0 && d < bMicrosRes) bMicrosRes = d;
  }

  // cost of a STEP pulse pair
  const long N = 20000;
  unsigned long t0 = micros();
  for (long i = 0; i < N; i++) {
    digitalWrite(X_STEP, HIGH);
    digitalWrite(X_STEP, LOW);
  }
  bTogglePairUs = (float)(micros() - t0) / (float)N;

  // delayMicroseconds accuracy
  t0 = micros();
  for (int i = 0; i < 1000; i++) delayMicroseconds(10);
  bDelay10Us = (float)(micros() - t0) / 1000.0f;

  // cost of a Serial.println (does it block?)
  t0 = micros();
  for (int i = 0; i < 20; i++) Serial.println("0123456789012345678901234567890123456789");
  bPrintUs = (float)(micros() - t0) / 20.0f;

#if SPIKE_ZEPHYR_TIMER
  // What does a kernel timer actually deliver? Ask for several periods, count 100 ms.
  const unsigned long asks[4] = {200, 100, 50, 25};
  k_timer_init(&probeTimer, probeExpiry, NULL);
  for (int i = 0; i < 4; i++) {
    probeCount = 0;
    k_timer_start(&probeTimer, K_USEC(asks[i]), K_USEC(asks[i]));
    unsigned long s = millis();
    while (millis() - s < 100) {}
    k_timer_stop(&probeTimer);
    tprobe[i].asked_us = asks[i];
    tprobe[i].count = probeCount;
    tprobeN = i + 1;
  }
#endif

  Serial.print("micros() resolution : "); Serial.print(bMicrosRes); Serial.println(" us");
  Serial.print("STEP pulse pair cost: "); Serial.print(bTogglePairUs, 3); Serial.print(" us  (max bit-bang ~");
  Serial.print(1e6f / (bTogglePairUs + 2.0f), 0); Serial.println(" steps/s with a 2 us pulse)");
  Serial.print("delayMicroseconds(10): "); Serial.print(bDelay10Us, 2); Serial.println(" us measured");
  Serial.print("Serial.println(40ch): "); Serial.print(bPrintUs, 1); Serial.println(" us each");
#if SPIKE_ZEPHYR_TIMER
  Serial.println("k_timer: asked period -> callbacks in 100 ms (ideal = 100000/period)");
  for (int i = 0; i < tprobeN; i++) {
    Serial.print("   "); Serial.print(tprobe[i].asked_us); Serial.print(" us -> ");
    Serial.print(tprobe[i].count); Serial.print("  (ideal ");
    Serial.print(100000UL / tprobe[i].asked_us); Serial.println(")");
  }
#else
  Serial.println("k_timer: disabled at compile time (SPIKE_ZEPHYR_TIMER 0)");
#endif
  Serial.print("AccelStepper library: "); Serial.println(HAVE_ACCELSTEPPER ? "found" : "NOT installed");
  Serial.println();
}

// ---- stage: AccelStepper (polled from loop) -----------------------------------------------------------
#if HAVE_ACCELSTEPPER
AccelStepper stepper(AccelStepper::DRIVER, X_STEP, X_DIR);

void accelBegin(bool forward) {
  stepper.setMaxSpeed(MAX_SPEED);
  stepper.setAcceleration(ACCEL);
  stepper.setMinPulseWidth(3);
  stepper.setCurrentPosition(0);
  stepper.moveTo(forward ? MOVE_STEPS : -MOVE_STEPS);
  probeReset();
}

bool accelPoll() {            // true while still moving
  stepper.run();
  return stepper.distanceToGo() != 0;
}
#endif

// ---- stage: k_timer-driven trapezoid generator -----------------------------------------------------------
// INTEGER fixed-point on purpose: the callback runs in interrupt context, and a
// Cortex-M33 thread using the FPU can be corrupted by an ISR that also uses it
// unless the core enables FPU sharing -- which we cannot verify from here.
// Q16 = value * 65536.
#if SPIKE_ZEPHYR_TIMER
struct Gen {
  volatile bool active;
  volatile long done;
  long total;
  int32_t v_q16;              // steps/s
  int32_t phase_q16;          // fractional step; a step is emitted each time it reaches 1.0
  int32_t dv_q16;             // speed change per tick (steps/s)
  int32_t vmax_q16, vmin_q16;
  volatile unsigned long ticks;
  volatile unsigned long lastTickUs;
  volatile unsigned long tickMin, tickMax;
};
static Gen gen;
static struct k_timer genTimer;

static void genTick(struct k_timer *) {
  unsigned long now = micros();
  if (gen.lastTickUs != 0) {
    unsigned long d = now - gen.lastTickUs;
    if (d < gen.tickMin) gen.tickMin = d;
    if (d > gen.tickMax) gen.tickMax = d;
  }
  gen.lastTickUs = now;
  gen.ticks++;
  if (!gen.active) return;

  // Distance still to go vs distance needed to stop from the current speed, both Q16 steps.
  int64_t remaining_q16 = (int64_t)(gen.total - gen.done) << 16;
  int64_t stop_q16 = (((int64_t)gen.v_q16 * gen.v_q16) >> 16) / (2 * ACCEL_SPS2);
  if (remaining_q16 <= stop_q16 + 65536) {
    gen.v_q16 -= gen.dv_q16;                    // decelerate so the last step lands at v ~ vmin
  } else if (gen.v_q16 < gen.vmax_q16) {
    gen.v_q16 += gen.dv_q16;
    if (gen.v_q16 > gen.vmax_q16) gen.v_q16 = gen.vmax_q16;
  }
  if (gen.v_q16 < gen.vmin_q16) gen.v_q16 = gen.vmin_q16;

  // steps this tick (Q16) = v[steps/s] * tick[us] / 1e6
  gen.phase_q16 += (int32_t)(((int64_t)gen.v_q16 * (int64_t)GEN_TICK_US) / 1000000);
  while (gen.phase_q16 >= 65536 && gen.done < gen.total) {
    digitalWrite(X_STEP, HIGH);
    delayMicroseconds(2);                       // >= driver minimum STEP pulse width
    digitalWrite(X_STEP, LOW);
    gen.done++;
    gen.phase_q16 -= 65536;
  }
  if (gen.done >= gen.total) gen.active = false;
}

void timerBegin() {
  gen.done = 0;
  gen.total = MOVE_STEPS;
  gen.vmin_q16 = (int32_t)(MIN_SPEED_SPS << 16);
  gen.vmax_q16 = (int32_t)(MAX_SPEED_SPS << 16);
  gen.v_q16 = gen.vmin_q16;
  gen.phase_q16 = 0;
  gen.dv_q16 = (int32_t)(((int64_t)ACCEL_SPS2 * (int64_t)GEN_TICK_US << 16) / 1000000);
  gen.ticks = 0;
  gen.lastTickUs = 0;
  gen.tickMin = 0xFFFFFFFFUL;
  gen.tickMax = 0;
  gen.active = true;
  k_timer_init(&genTimer, genTick, NULL);
  probeReset();
  k_timer_start(&genTimer, K_USEC(GEN_TICK_US), K_USEC(GEN_TICK_US));
}

bool timerPoll() { return gen.active; }
void timerEnd() { k_timer_stop(&genTimer); }
#endif

// ---- the state machine, called from loop() --------------------------------------------------------------------
void finishStage(StageResult &r, long steps, bool addTimerStats) {
  r.ran = true;
  r.steps += steps;
  r.time_us += micros() - stageStartUs;
  r.ideal_us += idealTrapezoidUs(MOVE_STEPS, MAX_SPEED, ACCEL);
  if (maxGapUs > r.max_gap_us) r.max_gap_us = maxGapUs;
  r.prints += printCount;
#if SPIKE_ZEPHYR_TIMER
  if (addTimerStats) {
    if (gen.tickMin < r.tick_min_us || r.tick_min_us == 0) r.tick_min_us = gen.tickMin;
    if (gen.tickMax > r.tick_max_us) r.tick_max_us = gen.tickMax;
  }
#else
  (void)addTimerStats;
#endif
}

void printRow(const char *name, const StageResult &r) {
  Serial.print(name);
  if (!r.ran) { Serial.println("  (not run)"); return; }
  float err = r.ideal_us ? 100.0f * ((float)r.time_us - (float)r.ideal_us) / (float)r.ideal_us : 0.0f;
  Serial.print("  steps=");       Serial.print(r.steps);
  Serial.print("  time_ms=");     Serial.print(r.time_us / 1000UL);
  Serial.print("  ideal_ms=");    Serial.print(r.ideal_us / 1000UL);
  Serial.print("  err%=");        Serial.print(err, 1);
  Serial.print("  max_loop_gap_us="); Serial.print(r.max_gap_us);
  Serial.print("  prints=");      Serial.print(r.prints);
  if (r.tick_max_us) {
    Serial.print("  tick_us min/max="); Serial.print(r.tick_min_us);
    Serial.print('/'); Serial.print(r.tick_max_us);
  }
  Serial.println();
}

void printReport() {
  Serial.println();
  Serial.println("=================== SPIKE RESULT ===================");
  Serial.print("DRIVERS_ENABLED="); Serial.print(DRIVERS_ENABLED);
  Serial.print("  move="); Serial.print(MOVE_STEPS); Serial.print(" steps fwd + back, vmax=");
  Serial.print(MAX_SPEED, 0); Serial.print(" sps, accel="); Serial.print(ACCEL, 0); Serial.println(" sps^2");
  Serial.print("baseline: micros_res_us="); Serial.print(bMicrosRes);
  Serial.print("  step_pair_us="); Serial.print(bTogglePairUs, 3);
  Serial.print("  delay10_us="); Serial.print(bDelay10Us, 2);
  Serial.print("  println_us="); Serial.println(bPrintUs, 1);
  for (int i = 0; i < tprobeN; i++) {
    Serial.print("k_timer ask "); Serial.print(tprobe[i].asked_us); Serial.print("us -> ");
    Serial.print(tprobe[i].count); Serial.print("/"); Serial.print(100000UL / tprobe[i].asked_us);
    Serial.println(" callbacks per 100 ms");
  }
  printRow("AccelStepper (polled): ", resAccel);
  printRow("k_timer ISR generator: ", resTimer);
  Serial.println("Pass if: |err%| within ~5 (a small NEGATIVE err is normal: the generator starts at");
  Serial.println("100 steps/s, not 0), max_loop_gap_us well under 5000 while prints run,");
  Serial.println("steps == 2 x move, and (DRIVERS_ENABLED=1) the motor ran smoothly both ways.");
  Serial.println("=====================================================");
}

void startStage(bool forward) {
  setDir(forward);
}

void setup() {
  Serial.begin(115200);
  pinMode(X_STEP, OUTPUT);
  pinMode(X_DIR, OUTPUT);
  pinMode(EN, OUTPUT);
  pinMode(LIM_X, INPUT_PULLUP);
  digitalWrite(X_STEP, LOW);
  digitalWrite(X_DIR, LOW);
  digitalWrite(EN, HIGH);                // disabled until the baseline is done
  unsigned long t = millis();
  while (!Serial && millis() - t < 3000) {}
  delay(300);

  Serial.println();
  Serial.println("=== stepper-library spike (checklist 0.6) ===");
  Serial.print("build: AccelStepper="); Serial.print(HAVE_ACCELSTEPPER);
  Serial.print("  zephyr_timer="); Serial.print(SPIKE_ZEPHYR_TIMER);
  Serial.print("  drivers_enabled="); Serial.println(DRIVERS_ENABLED);
  Serial.println("Send any character to abort.");
  Serial.println();

  runBaseline();
  driversEnable(DRIVERS_ENABLED != 0);   // still HIGH (off) unless you enabled it
  stage = ST_START;
}

void loop() {
  checkAbort();
  if (aborted) { delay(200); return; }

  switch (stage) {
    case ST_START:
#if HAVE_ACCELSTEPPER
      Serial.print("AccelStepper forward ");
      startStage(true);
      accelBegin(true);
      stage = ST_ACCEL_FWD;
#else
      stage = ST_TIMER_BEGIN;
#endif
      break;

    case ST_ACCEL_FWD:
#if HAVE_ACCELSTEPPER
      loopProbe();
      if (!accelPoll()) {
        finishStage(resAccel, MOVE_STEPS, false);
        Serial.println();
        Serial.print("AccelStepper back ");
        startStage(false);
        accelBegin(false);
        stage = ST_ACCEL_BACK;
      }
#endif
      break;

    case ST_ACCEL_BACK:
#if HAVE_ACCELSTEPPER
      loopProbe();
      if (!accelPoll()) {
        finishStage(resAccel, MOVE_STEPS, false);
        Serial.println();
        delay(500);
        stage = ST_TIMER_BEGIN;
      }
#endif
      break;

    case ST_TIMER_BEGIN:
#if SPIKE_ZEPHYR_TIMER
      Serial.print(timerLeg == 0 ? "k_timer forward " : "k_timer back ");
      startStage(timerLeg == 0);
      timerBegin();
      stage = ST_TIMER_RUN;
#else
      stage = ST_REPORT;
#endif
      break;

    case ST_TIMER_RUN:
#if SPIKE_ZEPHYR_TIMER
      loopProbe();
      if (!timerPoll()) {
        timerEnd();
        finishStage(resTimer, MOVE_STEPS, true);
        Serial.println();
        if (timerLeg == 0) {
          timerLeg = 1;
          delay(300);
          stage = ST_TIMER_BEGIN;
        } else {
          stage = ST_REPORT;
        }
      }
#else
      stage = ST_REPORT;
#endif
      break;

    case ST_REPORT:
      driversEnable(false);
      printReport();
      stage = ST_DONE;
      break;

    case ST_DONE:
      delay(5000);
      printReport();               // repeat so you can attach the monitor late
      break;
  }
}
