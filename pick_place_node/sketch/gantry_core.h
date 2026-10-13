// ============================================================================
//  gantry_core.h  --  the gantry MCU's motion / safety / protocol logic
// ============================================================================
//
//  Portable C++11, header-only, NO Arduino or Zephyr includes and no heap. The
//  sketch (sketch.ino) supplies a `Hal` for the real pins; the laptop test
//  harness (pick_place_node/firmware_host/) supplies a simulated one. That is the
//  point: everything below is compiled and exercised on a PC against the SAME
//  scenarios as pick_place/fake_mcu.py (tests/test_gantry_firmware_conformance.py).
//
//  Spec: documents/programming/pick_place_control_protocol.md (sections 5.2-5.7).
//  Anything marked PROVISIONAL is a number nobody has measured yet.
//
//  Two execution contexts, as on the real board:
//    tick()  - the step generator. Called at a fixed period (Config::tick_us) from
//              a timer ISR on the MCU; the host harness calls it from a loop.
//              Integer maths only, no allocation, no I/O beyond the HAL's step pins
//              and the two safety inputs.
//    poll()  - everything else (protocol, homing sequencer, vacuum, closed loop,
//              telemetry, watchdog). Called from loop().
//  onLine() is called from loop() too. State shared with tick() is `volatile` and
//  the few multi-field updates go through Hal::isrLock()/isrUnlock().
//
//  STATUS: logic verified on the laptop. NOT run on the board: pin polarity,
//  steps/mm, k_timer period, AS5600 reads and the vacuum sensor are all unproven.
// ============================================================================
#ifndef GANTRY_CORE_H
#define GANTRY_CORE_H

#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

namespace gantry {

enum Axis { AX_X = 0, AX_Y = 1, AX_Z = 2, AXES = 3 };
enum { ENC_X = 0, ENC_Y = 1, ENC_Z = 2, ENC_A = 3, ENCODERS = 4 };  // A = Y beam's second motor

static const char AXIS_LETTER[AXES] = {'X', 'Y', 'Z'};

// ---- hardware abstraction -------------------------------------------------------
struct Hal {
  virtual ~Hal() {}
  virtual void setEnable(bool on) = 0;              // all drivers; active-LOW pin on the shield
  virtual void setDir(int axis, bool positive) = 0;
  virtual void stepPulse(int axis) = 0;             // one STEP pulse (>= driver min width)
  virtual bool limitTriggered(int axis) = 0;        // already polarity-corrected
  virtual bool estopAsserted() = 0;                 // already polarity-corrected
  virtual void setVacuum(bool on) = 0;
  virtual bool vacuumConfirmed() = 0;
  virtual bool readEncoder(int encoder, uint16_t* raw12) = 0;  // 12-bit AS5600 angle; false = unreadable
  virtual void write(const char* line) = 0;         // one protocol line, no newline
  virtual uint32_t nowMs() = 0;
  virtual void isrLock() {}                         // noInterrupts() on the MCU
  virtual void isrUnlock() {}
};

// ---- configuration --------------------------------------------------------------
struct AxisConfig {
  float steps_per_mm;
  float max_mm_min;     // SETSPEED MAX
  float accel_mm_s2;    // SETSPEED ACC
  float min_mm, max_mm; // soft envelope (SETENV)
  int8_t home_dir;      // -1 / +1: which end the limit switch is at
  float home_pos_mm;    // what the axis reads once homed (at rest, `home_backoff_mm` off the switch)
};

struct Config {
  AxisConfig axis[AXES];
  uint32_t tick_us;
  float home_fast_mm_min, home_slow_mm_min, home_backoff_mm;
  uint32_t vac_timeout_ms, vac_debounce_ms;
  uint32_t link_timeout_ms;      // 0 = watchdog off
  uint32_t hb_ms, pos_ms;
  bool encoders_enabled;
  float lost_mm, skew_mm;        // SETTOL
  float enc_mm_per_count[ENCODERS];  // signed; mm of axis travel per AS5600 count
  uint8_t enc_fail_limit;        // consecutive unreadable polls before ENCODER_<axis>
  uint16_t min_speed_sps;        // generator never goes below this

  static Config defaults() {
    Config c;
    // PROVISIONAL: 1/16 microstepping, GT2 20T pulley (40 mm/rev) on X/Y, T8 lead screw
    // (8 mm/rev) on Z. Replace with measured values after the gantry is built.
    const float xy = 3200.0f / 40.0f, z = 3200.0f / 8.0f;
    c.axis[AX_X] = {xy, 8000.0f, 400.0f, 0.0f, 600.0f, -1, 0.0f};
    c.axis[AX_Y] = {xy, 8000.0f, 400.0f, 0.0f, 400.0f, -1, 0.0f};
    c.axis[AX_Z] = {z, 4000.0f, 300.0f, 0.0f, 200.0f, +1, 200.0f};
    c.tick_us = 50;
    c.home_fast_mm_min = 1500.0f;
    c.home_slow_mm_min = 300.0f;
    c.home_backoff_mm = 2.0f;
    c.vac_timeout_ms = 1500;
    c.vac_debounce_ms = 20;
    c.link_timeout_ms = 0;  // the sketch turns it on once the Pi's runner pings during motion
    c.hb_ms = 500;
    c.pos_ms = 100;
    c.encoders_enabled = false;
    c.lost_mm = 1.0f;
    c.skew_mm = 0.5f;
    for (int i = 0; i < ENCODERS; i++) c.enc_mm_per_count[i] = (i == ENC_Z ? 8.0f : 40.0f) / 4096.0f;
    c.enc_fail_limit = 3;
    c.min_speed_sps = 100;
    return c;
  }
};

// ---- small pure helpers (also used by the tests through the host harness) ----------

// "500.0", "12.5", "0.125": up to 3 decimals, trailing zeros trimmed, one kept.
inline int fmtMm(float v, char* out, int cap) {
  if (!(v == v) || v > 1.0e9f || v < -1.0e9f) v = 0.0f;
  long long milli = (long long)llroundf(v * 1000.0f);
  bool neg = milli < 0;
  if (neg) milli = -milli;
  long long ip = milli / 1000;
  int fp = (int)(milli % 1000);
  char buf[32];
  int n = 0;
  if (neg && milli != 0) buf[n++] = '-';
  char digits[24];
  int dn = 0;
  if (ip == 0) digits[dn++] = '0';
  while (ip > 0 && dn < 20) { digits[dn++] = (char)('0' + (int)(ip % 10)); ip /= 10; }
  while (dn > 0) buf[n++] = digits[--dn];
  buf[n++] = '.';
  char f[3] = {(char)('0' + fp / 100), (char)('0' + (fp / 10) % 10), (char)('0' + fp % 10)};
  int flen = 3;
  while (flen > 1 && f[flen - 1] == '0') flen--;
  for (int i = 0; i < flen; i++) buf[n++] = f[i];
  buf[n] = 0;
  int len = n < cap - 1 ? n : cap - 1;
  memcpy(out, buf, (size_t)len);
  out[len] = 0;
  return len;
}

// Whole token must be a finite number.
inline bool parseNum(const char* s, float* out) {
  if (!s || !*s) return false;
  char* end = 0;
  double v = strtod(s, &end);
  if (end == s || *end != 0) return false;
  if (!(v == v) || v > 1.0e12 || v < -1.0e12) return false;
  *out = (float)v;
  return true;
}

// ---- the machine ---------------------------------------------------------------------

enum Fsm { FSM_NEEDS_HOMING, FSM_HOMING, FSM_IDLE, FSM_MOVING, FSM_ALARM };

class Gantry {
 public:
  Gantry(Hal& hal, const Config& cfg) : h_(hal), c_(cfg) { reset(); }

  void begin() {
    reset();
    h_.setEnable(!h_.estopAsserted());
    if (h_.estopAsserted()) raiseAlarm("ESTOP");
    emitState(true);
  }

  // ---- step generator: fixed period, ISR context on the MCU --------------------------
  void tick() {
    if (h_.estopAsserted()) {
      if (!estop_seen_) {
        estop_seen_ = true;
        for (int a = 0; a < AXES; a++) g_[a].active = false;
      }
      return;
    }
    for (int a = 0; a < AXES; a++) {
      Gen& g = g_[a];
      if (!g.active) continue;
      // A limit switch ahead of a moving axis stops it at once (homing seeks are the
      // one case where hitting it is the point).
      if (g.dir == c_.axis[a].home_dir && h_.limitTriggered(a)) {
        g.active = false;
        if (g.stop_on_limit) g.hit_limit = true;
        else limit_hit_axis_ = a;
        continue;
      }
      int64_t remaining = (int64_t)g.total - (int64_t)g.done;
      if (remaining <= 0) { g.active = false; continue; }
      // decelerate once the distance left is what is needed to stop from here
      int64_t stop_steps_q16 = (((int64_t)g.v_q16 * g.v_q16) >> 16) / (2 * (int64_t)g.accel_sps2);
      if ((remaining << 16) <= stop_steps_q16 + 65536) g.v_q16 -= g.dv_q16;
      else if (g.v_q16 < g.vmax_q16) { g.v_q16 += g.dv_q16; if (g.v_q16 > g.vmax_q16) g.v_q16 = g.vmax_q16; }
      if (g.v_q16 < g.vmin_q16) g.v_q16 = g.vmin_q16;
      g.phase_q16 += (int32_t)(((int64_t)g.v_q16 * (int64_t)c_.tick_us) / 1000000);
      if (g.phase_q16 >= 65536) {
        h_.stepPulse(a);
        g.phase_q16 -= 65536;
        g.done++;
        pos_steps_[a] += g.dir;
        if (g.done >= g.total) g.active = false;
      }
    }
  }

  // ---- everything else: loop() ---------------------------------------------------------
  void poll() {
    uint32_t now = h_.nowMs();
    now_ = now;
    if (!seen_poll_) { seen_poll_ = true; next_hb_ = now + c_.hb_ms; next_pos_ = now + c_.pos_ms; last_rx_ = now; }

    if (estop_seen_ || h_.estopAsserted()) {
      estop_seen_ = true;
      if (alarm_[0] == 0) raiseAlarm("ESTOP");
    }
    if (limit_hit_axis_ >= 0) {
      int a = limit_hit_axis_;
      limit_hit_axis_ = -1;
      char r[24];
      limitAlarmName(r, sizeof r, a);
      raiseAlarm(r);
    }
    if (alarm_[0] == 0) {
      serviceMotion(now);
      serviceVacuum(now);
      serviceClosedLoop(now);
      serviceWatchdog(now);
    } else {
      serviceVacuum(now);  // a pending VAC ON is still answered
    }
    if (now >= next_hb_) {
      next_hb_ = now + c_.hb_ms;
      char b[40];
      int n = 0;
      b[n++] = 'h'; b[n++] = 'b'; b[n++] = ' ';
      n += utoa_(now, b + n);
      b[n] = 0;
      h_.write(b);
    }
    if (motionActive() && now >= next_pos_) {
      next_pos_ = now + c_.pos_ms;
      emitPos();
    }
    emitState(false);
  }

  // ---- protocol -----------------------------------------------------------------------------
  void onLine(const char* line) {
    last_rx_ = now_;
    char buf[96];
    size_t n = strlen(line);
    if (n >= sizeof buf) { badCmd(0); return; }
    memcpy(buf, line, n + 1);
    while (n > 0 && (buf[n - 1] == '\r' || buf[n - 1] == '\n' || buf[n - 1] == ' ')) buf[--n] = 0;

    char* tok[10];
    int nt = 0;
    for (char* p = strtok_(buf); p && nt < 10; p = strtok_(0)) tok[nt++] = p;
    if (nt < 2 || !isDigits(tok[0])) { badCmd(nt >= 1 && isDigits(tok[0]) ? atol(tok[0]) : 0); return; }
    long seq = atol(tok[0]);
    char* verb = tok[1];
    for (char* p = verb; *p; p++) if (*p >= 'a' && *p <= 'z') *p = (char)(*p - 32);
    char** args = tok + 2;
    int na = nt - 2;

    bool always = !strcmp(verb, "PING") || !strcmp(verb, "STOP") || !strcmp(verb, "GETPOS") ||
                  !strcmp(verb, "GETSTATE") || !strcmp(verb, "CLEARALARM") || !strcmp(verb, "VAC");
    if (alarm_[0] != 0 && !always) { err(seq, "ALARM_ACTIVE"); return; }

    if (!strcmp(verb, "PING")) ok(seq);
    else if (!strcmp(verb, "GETPOS")) { ok(seq); emitPos(); }
    else if (!strcmp(verb, "GETSTATE")) { ok(seq); emitState(true); }
    else if (!strcmp(verb, "STOP")) cmdStop(seq);
    else if (!strcmp(verb, "CLEARALARM")) cmdClearAlarm(seq);
    else if (!strcmp(verb, "VAC")) cmdVac(seq, args, na);
    else if (!strcmp(verb, "SETENV")) cmdSetEnv(seq, args, na);
    else if (!strcmp(verb, "SETSPEED")) cmdSetSpeed(seq, args, na);
    else if (!strcmp(verb, "SETTOL")) cmdSetTol(seq, args, na);
    else if (!strcmp(verb, "HOME")) cmdHome(seq, args, na);
    else if (!strcmp(verb, "MOVE")) cmdMove(seq, args, na, false);
    else if (!strcmp(verb, "JOG")) cmdMove(seq, args, na, true);
    else err(seq, "BAD_CMD");
  }

  // ---- introspection for tests / the sketch ----------------------------------------------------
  Fsm fsm() const { return fsm_; }
  bool homed() const { return homedAll(); }
  bool vacuumOn() const { return vac_on_; }
  const char* alarm() const { return alarm_; }
  float posMm(int a) const { return (float)pos_steps_[a] / c_.axis[a].steps_per_mm; }
  int32_t posSteps(int a) const { return pos_steps_[a]; }
  bool motionActive() const { return motion_seq_ >= 0; }
  const Config& config() const { return c_; }

 private:
  // ---- types ---------------------------------------------------------------------------------
  struct Gen {
    volatile bool active;
    volatile bool hit_limit;
    bool stop_on_limit;
    int8_t dir;
    volatile int32_t done;
    int32_t total;
    int32_t v_q16, vmin_q16, vmax_q16, dv_q16, phase_q16;
    int32_t accel_sps2;
  };
  enum HomePhase { HP_NONE, HP_SEEK, HP_BACKOFF, HP_RETOUCH, HP_RETURN };

  Hal& h_;
  Config c_;
  Gen g_[AXES];
  volatile int32_t pos_steps_[AXES];
  bool homed_axis_[AXES];
  Fsm fsm_;
  char alarm_[24];
  long motion_seq_;          // seq of the command whose `done` is owed; -1 none
  bool motion_is_home_;
  volatile bool estop_seen_;
  volatile int limit_hit_axis_;
  bool seen_poll_;
  uint32_t now_, next_hb_, next_pos_, last_rx_;
  // homing
  int home_axis_list_[AXES];
  int home_n_, home_i_;
  HomePhase home_phase_;
  // vacuum
  bool vac_on_, vac_wait_;
  long vac_seq_;
  uint32_t vac_start_, vac_conf_since_;
  bool vac_conf_;
  // state emission
  bool last_state_valid_;
  Fsm last_fsm_;
  bool last_vac_, last_homed_;
  // closed loop
  uint16_t enc_last_raw_[ENCODERS];
  int32_t enc_acc_[ENCODERS], enc_zero_[ENCODERS];
  bool enc_have_[ENCODERS], enc_zeroed_;
  uint8_t enc_fail_[ENCODERS];
  float enc_mm_[ENCODERS];
  float enc_dev_[ENCODERS];  // measured minus commanded, both taken at the instant of that read
  bool enc_ok_[ENCODERS];
  int enc_rr_;
  uint32_t next_enc_;
  char* strtok_state_;

  // ---- plumbing ------------------------------------------------------------------------------------
  void reset() {
    memset(g_, 0, sizeof g_);
    for (int a = 0; a < AXES; a++) { pos_steps_[a] = 0; homed_axis_[a] = false; }
    fsm_ = FSM_NEEDS_HOMING;
    alarm_[0] = 0;
    motion_seq_ = -1;
    motion_is_home_ = false;
    estop_seen_ = false;
    limit_hit_axis_ = -1;
    seen_poll_ = false;
    now_ = next_hb_ = next_pos_ = last_rx_ = 0;
    home_n_ = home_i_ = 0;
    home_phase_ = HP_NONE;
    vac_on_ = vac_wait_ = false;
    vac_seq_ = -1;
    vac_start_ = vac_conf_since_ = 0;
    vac_conf_ = false;
    last_state_valid_ = false;
    last_fsm_ = FSM_NEEDS_HOMING;
    last_vac_ = last_homed_ = false;
    memset(enc_last_raw_, 0, sizeof enc_last_raw_);
    memset(enc_acc_, 0, sizeof enc_acc_);
    memset(enc_zero_, 0, sizeof enc_zero_);
    memset(enc_have_, 0, sizeof enc_have_);
    memset(enc_fail_, 0, sizeof enc_fail_);
    memset(enc_mm_, 0, sizeof enc_mm_);
    memset(enc_dev_, 0, sizeof enc_dev_);
    memset(enc_ok_, 0, sizeof enc_ok_);
    enc_zeroed_ = false;
    enc_rr_ = 0;
    next_enc_ = 0;
    strtok_state_ = 0;
  }

  bool homedAll() const { return homed_axis_[0] && homed_axis_[1] && homed_axis_[2]; }

  static bool isDigits(const char* s) {
    if (!*s) return false;
    for (; *s; s++) if (*s < '0' || *s > '9') return false;
    return true;
  }
  char* strtok_(char* s) {
    if (s) strtok_state_ = s;
    if (!strtok_state_) return 0;
    while (*strtok_state_ == ' ' || *strtok_state_ == '\t') strtok_state_++;
    if (!*strtok_state_) { strtok_state_ = 0; return 0; }
    char* start = strtok_state_;
    while (*strtok_state_ && *strtok_state_ != ' ' && *strtok_state_ != '\t') strtok_state_++;
    if (*strtok_state_) *strtok_state_++ = 0; else strtok_state_ = 0;
    return start;
  }
  static int utoa_(uint32_t v, char* out) {
    char t[12];
    int n = 0;
    if (v == 0) t[n++] = '0';
    while (v > 0) { t[n++] = (char)('0' + v % 10); v /= 10; }
    for (int i = 0; i < n; i++) out[i] = t[n - 1 - i];
    return n;
  }
  static void catNum(char* dst, size_t cap, long v) {
    char t[16];
    int n = 0;
    bool neg = v < 0;
    unsigned long u = neg ? (unsigned long)(-v) : (unsigned long)v;
    if (u == 0) t[n++] = '0';
    while (u > 0) { t[n++] = (char)('0' + u % 10); u /= 10; }
    size_t len = strlen(dst);
    if (neg && len + 1 < cap) dst[len++] = '-';
    for (int i = n - 1; i >= 0 && len + 1 < cap; i--) dst[len++] = t[i];
    dst[len] = 0;
  }
  static void catStr(char* dst, size_t cap, const char* s) {
    size_t len = strlen(dst);
    while (*s && len + 1 < cap) dst[len++] = *s++;
    dst[len] = 0;
  }
  static void catMm(char* dst, size_t cap, float v) {
    char t[24];
    fmtMm(v, t, sizeof t);
    catStr(dst, cap, t);
  }
  void limitAlarmName(char* out, size_t cap, int axis) {
    out[0] = 0;
    catStr(out, cap, "LIMIT_");
    char l[2] = {AXIS_LETTER[axis], 0};
    catStr(out, cap, l);
    catStr(out, cap, c_.axis[axis].home_dir < 0 ? "_MIN" : "_MAX");
  }

  void ok(long seq) { char b[24] = "ok "; catNum(b, sizeof b, seq); h_.write(b); }
  void err(long seq, const char* why) {
    char b[48] = "err ";
    catNum(b, sizeof b, seq);
    catStr(b, sizeof b, " ");
    catStr(b, sizeof b, why);
    h_.write(b);
  }
  void badCmd(long seq) { err(seq, "BAD_CMD"); }

  const char* fsmName(Fsm f) const {
    switch (f) {
      case FSM_NEEDS_HOMING: return "NEEDS_HOMING";
      case FSM_HOMING: return "HOMING";
      case FSM_IDLE: return "IDLE";
      case FSM_MOVING: return "MOVING";
      default: return "ALARM";
    }
  }
  void emitState(bool force) {
    bool hm = homedAll();
    if (!force && last_state_valid_ && last_fsm_ == fsm_ && last_vac_ == vac_on_ && last_homed_ == hm) return;
    last_state_valid_ = true;
    last_fsm_ = fsm_;
    last_vac_ = vac_on_;
    last_homed_ = hm;
    char b[64] = "state ";
    catStr(b, sizeof b, fsmName(fsm_));
    catStr(b, sizeof b, vac_on_ ? " VAC=1" : " VAC=0");
    catStr(b, sizeof b, hm ? " HOMED=1" : " HOMED=0");
    h_.write(b);
  }
  void emitPos() {
    char b[160] = "pos";
    for (int a = 0; a < AXES; a++) {
      char l[3] = {' ', AXIS_LETTER[a], 0};
      catStr(b, sizeof b, l);
      catMm(b, sizeof b, posMm(a));
    }
    if (c_.encoders_enabled && enc_zeroed_) {
      const char* names[ENCODERS] = {" EX", " EY", " EZ", " EA"};
      for (int i = 0; i < ENCODERS; i++) {
        if (!enc_ok_[i]) continue;
        catStr(b, sizeof b, names[i]);
        // Measured position NOW = commanded now + the deviation seen at that encoder's
        // last read (a raw reading is a few ms old and the axis may have moved).
        catMm(b, sizeof b, posMm(i == ENC_A ? AX_Y : i) + enc_dev_[i]);
      }
    }
    h_.write(b);
  }
  void emitDone(long seq) {
    char b[96] = "done ";
    catNum(b, sizeof b, seq);
    for (int a = 0; a < AXES; a++) {
      char l[3] = {' ', AXIS_LETTER[a], 0};
      catStr(b, sizeof b, l);
      catMm(b, sizeof b, posMm(a));
    }
    h_.write(b);
  }

  // ---- alarms ---------------------------------------------------------------------------------------------
  void stopGenerators() {
    h_.isrLock();
    for (int a = 0; a < AXES; a++) g_[a].active = false;
    h_.isrUnlock();
  }
  void cancelMotion(const char* why) {
    stopGenerators();
    if (motion_seq_ >= 0) {
      err(motion_seq_, why);
      motion_seq_ = -1;
    }
    motion_is_home_ = false;
    home_phase_ = HP_NONE;
  }
  void raiseAlarm(const char* reason) {
    if (alarm_[0] != 0) return;
    size_t n = strlen(reason);
    if (n >= sizeof alarm_) n = sizeof alarm_ - 1;
    memcpy(alarm_, reason, n);
    alarm_[n] = 0;
    for (int a = 0; a < AXES; a++) homed_axis_[a] = false;
    cancelMotion("STOPPED");
    h_.setEnable(false);  // any alarm drops the drivers (the hardware e-stop also cuts the PSU)
    fsm_ = FSM_ALARM;
    char b[40] = "alarm ";
    catStr(b, sizeof b, alarm_);
    h_.write(b);
    emitState(false);
  }

  // ---- motion --------------------------------------------------------------------------------------------------
  // Start axis `a` moving to absolute step position `target`. feed in mm/min (<=0: axis max).
  void startAxis(int a, int32_t target, float feed_mm_min, bool stop_on_limit) {
    const AxisConfig& ac = c_.axis[a];
    int32_t delta = target - pos_steps_[a];
    if (delta == 0) return;
    float feed = (feed_mm_min > 0.0f && feed_mm_min < ac.max_mm_min) ? feed_mm_min : ac.max_mm_min;
    float sps = feed / 60.0f * ac.steps_per_mm;
    float cap = 0.8f * 1.0e6f / (float)c_.tick_us;  // at most 0.8 step per tick
    if (sps > cap) sps = cap;
    if (sps < (float)c_.min_speed_sps) sps = (float)c_.min_speed_sps;
    float acc = ac.accel_mm_s2 * ac.steps_per_mm;
    if (acc < 1.0f) acc = 1.0f;
    Gen& g = g_[a];
    h_.isrLock();
    g.dir = delta > 0 ? 1 : -1;
    g.total = delta > 0 ? delta : -delta;
    g.done = 0;
    g.accel_sps2 = (int32_t)acc;
    g.vmin_q16 = (int32_t)c_.min_speed_sps << 16;
    g.vmax_q16 = (int32_t)(sps * 65536.0f);
    if (g.vmax_q16 < g.vmin_q16) g.vmax_q16 = g.vmin_q16;
    g.v_q16 = g.vmin_q16;
    g.dv_q16 = (int32_t)(((int64_t)g.accel_sps2 * (int64_t)c_.tick_us << 16) / 1000000);
    if (g.dv_q16 < 1) g.dv_q16 = 1;
    g.phase_q16 = 0;
    g.stop_on_limit = stop_on_limit;
    g.hit_limit = false;
    h_.setDir(a, g.dir > 0);
    g.active = true;  // last: the ISR only looks at the rest once this is set
    h_.isrUnlock();
  }

  bool anyGenActive() const {
    for (int a = 0; a < AXES; a++) if (g_[a].active) return true;
    return false;
  }

  void finishMotion() {
    long seq = motion_seq_;
    motion_seq_ = -1;
    motion_is_home_ = false;
    fsm_ = homedAll() ? FSM_IDLE : FSM_NEEDS_HOMING;
    emitDone(seq);
    emitState(false);
  }

  void serviceMotion(uint32_t now) {
    (void)now;
    if (motion_seq_ < 0 || anyGenActive()) return;
    if (!motion_is_home_) { finishMotion(); return; }
    advanceHoming();
  }

  // ---- homing --------------------------------------------------------------------------------------------------------
  void beginHomeAxis() {
    int a = home_axis_list_[home_i_];
    home_phase_ = HP_SEEK;
    float far_mm = (c_.axis[a].max_mm - c_.axis[a].min_mm) * 1.5f + 50.0f;
    int32_t span = (int32_t)(far_mm * c_.axis[a].steps_per_mm);
    startAxis(a, pos_steps_[a] + c_.axis[a].home_dir * span, c_.home_fast_mm_min, true);
  }

  void advanceHoming() {
    int a = home_axis_list_[home_i_];
    const AxisConfig& ac = c_.axis[a];
    Gen& g = g_[a];
    int32_t back = (int32_t)(c_.home_backoff_mm * ac.steps_per_mm);
    switch (home_phase_) {
      case HP_SEEK:
        if (!g.hit_limit) { failHoming("HOME_FAIL"); return; }
        home_phase_ = HP_BACKOFF;
        startAxis(a, pos_steps_[a] - ac.home_dir * back, c_.home_fast_mm_min, false);
        return;
      case HP_BACKOFF:
        if (h_.limitTriggered(a)) { failHoming("HOME_FAIL"); return; }
        home_phase_ = HP_RETOUCH;
        startAxis(a, pos_steps_[a] + ac.home_dir * back * 2, c_.home_slow_mm_min, true);
        return;
      case HP_RETOUCH: {
        if (!g.hit_limit) { failHoming("HOME_FAIL"); return; }
        // The switch fires here: this IS the reference. The axis rests `backoff` off it.
        h_.isrLock();
        pos_steps_[a] = (int32_t)lroundf((ac.home_pos_mm + ac.home_dir * c_.home_backoff_mm) * ac.steps_per_mm);
        h_.isrUnlock();
        home_phase_ = HP_RETURN;
        startAxis(a, (int32_t)lroundf(ac.home_pos_mm * ac.steps_per_mm), c_.home_slow_mm_min * 2.0f, false);
        if (!g.active) { /* backoff rounded to zero steps */ }
        return;
      }
      case HP_RETURN:
        homed_axis_[a] = true;
        home_i_++;
        if (home_i_ >= home_n_) {
          home_phase_ = HP_NONE;
          zeroEncoders();
          finishMotion();
        } else {
          beginHomeAxis();
        }
        return;
      default:
        return;
    }
  }

  void failHoming(const char* why) { raiseAlarm(why); }  // raiseAlarm cancels the homing motion itself

  // ---- commands ------------------------------------------------------------------------------------------------------------
  bool parseAxisArgs(char** args, int na, float vals[AXES], bool have[AXES], float* feed, bool* has_feed) {
    for (int i = 0; i < AXES; i++) { have[i] = false; vals[i] = 0.0f; }
    *has_feed = false;
    for (int i = 0; i < na; i++) {
      const char* t = args[i];
      float v;
      if (t[0] == 'F' && parseNum(t + 1, &v)) { *feed = v; *has_feed = true; continue; }
      int a = t[0] == 'X' ? AX_X : t[0] == 'Y' ? AX_Y : t[0] == 'Z' ? AX_Z : -1;
      if (a < 0 || !parseNum(t + 1, &v)) return false;
      vals[a] = v;
      have[a] = true;
    }
    return true;
  }

  bool motionAllowed(long seq, bool needs_home) {
    if (motion_seq_ >= 0) { err(seq, "BUSY"); return false; }
    if (needs_home && !homedAll()) { err(seq, "NOT_HOMED"); return false; }
    return true;
  }

  void cmdMove(long seq, char** args, int na, bool jog) {
    float vals[AXES];
    bool have[AXES];
    float feed = 0.0f;
    bool has_feed;
    if (!parseAxisArgs(args, na, vals, have, &feed, &has_feed)) { badCmd(seq); return; }
    int count = have[0] + have[1] + have[2];
    if (count == 0 || (jog && count != 1) || (has_feed && !(feed > 0.0f))) { badCmd(seq); return; }
    if (!motionAllowed(seq, true)) return;
    float target[AXES];
    for (int a = 0; a < AXES; a++) {
      float cur = posMm(a);
      target[a] = !have[a] ? cur : (jog ? cur + vals[a] : vals[a]);
      if (target[a] < c_.axis[a].min_mm - 1e-3f || target[a] > c_.axis[a].max_mm + 1e-3f) {
        err(seq, "OUT_OF_BOUNDS");
        return;
      }
    }
    ok(seq);
    motion_seq_ = seq;
    motion_is_home_ = false;
    fsm_ = FSM_MOVING;
    next_pos_ = now_ + c_.pos_ms;
    for (int a = 0; a < AXES; a++) {
      if (!have[a]) continue;
      startAxis(a, (int32_t)lroundf(target[a] * c_.axis[a].steps_per_mm), has_feed ? feed : 0.0f, false);
    }
    emitState(false);
    if (!anyGenActive()) finishMotion();  // a move to where it already is
  }

  void cmdHome(long seq, char** args, int na) {
    if (na != 1) { badCmd(seq); return; }
    int only = -1;
    if (!strcmp(args[0], "ALL")) only = -1;
    else if (args[0][1] == 0 && (args[0][0] == 'X' || args[0][0] == 'Y' || args[0][0] == 'Z'))
      only = args[0][0] == 'X' ? AX_X : args[0][0] == 'Y' ? AX_Y : AX_Z;
    else { badCmd(seq); return; }
    if (!motionAllowed(seq, false)) return;
    // Z first (up, clear of everything), then X, then Y.
    home_n_ = 0;
    const int order[AXES] = {AX_Z, AX_X, AX_Y};
    for (int i = 0; i < AXES; i++) if (only < 0 || order[i] == only) home_axis_list_[home_n_++] = order[i];
    home_i_ = 0;
    for (int i = 0; i < home_n_; i++) homed_axis_[home_axis_list_[i]] = false;
    ok(seq);
    motion_seq_ = seq;
    motion_is_home_ = true;
    fsm_ = FSM_HOMING;
    enc_zeroed_ = false;
    emitState(false);
    beginHomeAxis();
  }

  void cmdStop(long seq) {
    ok(seq);
    cancelMotion("STOPPED");
    if (alarm_[0] == 0 && fsm_ != FSM_ALARM) fsm_ = homedAll() ? FSM_IDLE : FSM_NEEDS_HOMING;
    emitState(false);
  }

  void cmdClearAlarm(long seq) {
    if (h_.estopAsserted()) { err(seq, "ALARM_ACTIVE"); return; }  // the loop must be reclosed first
    alarm_[0] = 0;
    estop_seen_ = false;
    limit_hit_axis_ = -1;
    for (int a = 0; a < AXES; a++) homed_axis_[a] = false;
    enc_zeroed_ = false;
    h_.setEnable(true);
    fsm_ = FSM_NEEDS_HOMING;
    ok(seq);
    emitState(false);
  }

  void cmdVac(long seq, char** args, int na) {
    if (na != 1) { badCmd(seq); return; }
    if (!strcmp(args[0], "OFF")) {
      vac_wait_ = false;
      vac_on_ = false;
      h_.setVacuum(false);
      ok(seq);
      emitState(false);
    } else if (!strcmp(args[0], "ON")) {
      if (alarm_[0] != 0) { err(seq, "ALARM_ACTIVE"); return; }
      if (vac_wait_) { err(seq, "BUSY"); return; }
      vac_on_ = true;  // pump running; the grip is not confirmed yet
      h_.setVacuum(true);
      vac_wait_ = true;
      vac_seq_ = seq;
      vac_start_ = now_;
      vac_conf_ = false;
      emitState(false);
    } else {
      badCmd(seq);
    }
  }

  void cmdSetEnv(long seq, char** args, int na) {
    float lo[AXES], hi[AXES];
    bool glo[AXES] = {false, false, false}, ghi[AXES] = {false, false, false};
    for (int i = 0; i < na; i++) {
      const char* t = args[i];
      int a = t[0] == 'X' ? AX_X : t[0] == 'Y' ? AX_Y : t[0] == 'Z' ? AX_Z : -1;
      float v;
      if (a < 0 || !parseNum(t + 1, &v)) { badCmd(seq); return; }
      if (!glo[a]) { lo[a] = v; glo[a] = true; }
      else if (!ghi[a]) { hi[a] = v; ghi[a] = true; }
      else { badCmd(seq); return; }
    }
    for (int a = 0; a < AXES; a++) if (!glo[a] || !ghi[a] || lo[a] >= hi[a]) { badCmd(seq); return; }
    for (int a = 0; a < AXES; a++) { c_.axis[a].min_mm = lo[a]; c_.axis[a].max_mm = hi[a]; }
    ok(seq);
  }

  void cmdSetSpeed(long seq, char** args, int na) {
    if (na != 3 || args[0][1] != 0) { badCmd(seq); return; }
    int a = args[0][0] == 'X' ? AX_X : args[0][0] == 'Y' ? AX_Y : args[0][0] == 'Z' ? AX_Z : -1;
    float mx, ac;
    if (a < 0 || strncmp(args[1], "MAX", 3) || strncmp(args[2], "ACC", 3) || !parseNum(args[1] + 3, &mx) ||
        !parseNum(args[2] + 3, &ac) || !(mx > 0.0f) || !(ac > 0.0f)) { badCmd(seq); return; }
    c_.axis[a].max_mm_min = mx;
    c_.axis[a].accel_mm_s2 = ac;
    ok(seq);
  }

  void cmdSetTol(long seq, char** args, int na) {
    float lost = 0, skew = 0;
    bool gl = false, gs = false;
    for (int i = 0; i < na; i++) {
      float v;
      if (!strncmp(args[i], "LOST", 4) && parseNum(args[i] + 4, &v)) { lost = v; gl = true; }
      else if (!strncmp(args[i], "SKEW", 4) && parseNum(args[i] + 4, &v)) { skew = v; gs = true; }
    }
    if (!gl || !gs || !(lost > 0.0f) || !(skew > 0.0f)) { badCmd(seq); return; }
    c_.lost_mm = lost;
    c_.skew_mm = skew;
    ok(seq);
  }

  // ---- vacuum ---------------------------------------------------------------------------------------------------------------------
  void serviceVacuum(uint32_t now) {
    if (!vac_wait_) return;
    bool conf = h_.vacuumConfirmed();
    if (conf) {
      if (!vac_conf_) { vac_conf_ = true; vac_conf_since_ = now; }
      if (now - vac_conf_since_ >= c_.vac_debounce_ms) {
        vac_wait_ = false;
        ok(vac_seq_);
        return;
      }
    } else {
      vac_conf_ = false;
    }
    if (now - vac_start_ >= c_.vac_timeout_ms) {
      vac_wait_ = false;
      vac_on_ = false;
      h_.setVacuum(false);
      err(vac_seq_, "VACUUM_TIMEOUT");
      emitState(false);
    }
  }

  // ---- closed loop ----------------------------------------------------------------------------------------------------------------
  void zeroEncoders() {
    if (!c_.encoders_enabled) return;
    for (int i = 0; i < ENCODERS; i++) enc_zero_[i] = enc_acc_[i];
    enc_zeroed_ = true;
  }

  void serviceClosedLoop(uint32_t now) {
    if (!c_.encoders_enabled || now < next_enc_) return;
    next_enc_ = now + 2;  // round-robin: one encoder per 2 ms, never a long blocking sweep
    int i = enc_rr_;
    enc_rr_ = (enc_rr_ + 1) % ENCODERS;
    uint16_t raw;
    if (!h_.readEncoder(i, &raw)) {
      enc_ok_[i] = false;
      if (enc_fail_[i] < 255) enc_fail_[i]++;
      // An unreadable encoder is a fault, never a pass.
      if (enc_zeroed_ && enc_fail_[i] >= c_.enc_fail_limit) {
        char r[24] = "ENCODER_";
        char l[2] = {i == ENC_A ? 'A' : AXIS_LETTER[i], 0};
        catStr(r, sizeof r, l);
        raiseAlarm(r);
      }
      return;
    }
    enc_fail_[i] = 0;
    if (!enc_have_[i]) { enc_have_[i] = true; enc_last_raw_[i] = raw; }
    int32_t d = (int32_t)raw - (int32_t)enc_last_raw_[i];
    if (d > 2048) d -= 4096;
    else if (d < -2048) d += 4096;
    enc_last_raw_[i] = raw;
    enc_acc_[i] += d;
    if (!enc_zeroed_) { enc_ok_[i] = false; return; }
    int refAxis = (i == ENC_A) ? AX_Y : i;
    float ref = c_.axis[refAxis].home_pos_mm;
    enc_mm_[i] = ref + (float)(enc_acc_[i] - enc_zero_[i]) * c_.enc_mm_per_count[i];
    // The encoders are read one at a time, a few ms apart, while the axis may be
    // moving: raw positions from different instants must never be compared. Each
    // is judged against the commanded position at ITS OWN read.
    enc_dev_[i] = enc_mm_[i] - posMm(refAxis);
    enc_ok_[i] = true;
    if (!homedAll() || fsm_ == FSM_HOMING) return;
    if (i != ENC_A && fabsf(enc_dev_[i]) > c_.lost_mm) {
      char r[24] = "LOST_STEPS_";
      char l[2] = {AXIS_LETTER[i], 0};
      catStr(r, sizeof r, l);
      raiseAlarm(r);
      return;
    }
    if (i == ENC_A && fabsf(enc_dev_[ENC_A]) > c_.lost_mm) {
      raiseAlarm("LOST_STEPS_Y");  // the second Y motor lost steps on its own
      return;
    }
    // Racking: the two Y motors disagree about how far they are from where they were told to be.
    if ((i == ENC_Y || i == ENC_A) && enc_ok_[ENC_Y] && enc_ok_[ENC_A] &&
        fabsf(enc_dev_[ENC_A] - enc_dev_[ENC_Y]) > c_.skew_mm)
      raiseAlarm("SKEW_Y");
  }

  // ---- link watchdog ------------------------------------------------------------------------------------------------------------------
  void serviceWatchdog(uint32_t now) {
    if (c_.link_timeout_ms == 0 || motion_seq_ < 0) return;
    if (now - last_rx_ > c_.link_timeout_ms) raiseAlarm("LINK_TIMEOUT");
  }
};

}  // namespace gantry

#endif  // GANTRY_CORE_H
