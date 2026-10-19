// ============================================================================
//  nano_core.h  --  the Nano's striker / belt / serial-protocol logic
// ============================================================================
//
//  Portable C++11, header-only, NO Arduino includes, no heap, no printf family,
//  no floats. nano_firmware.ino supplies a `Hal` for the real pins; the laptop
//  harness (acoustic_node/firmware_host/) supplies a simulated one, so everything
//  below is compiled and tested on a PC against the real Python protocol module
//  (tests/test_nano_firmware_conformance.py).
//
//  Spec: documents/programming/nano_serial_protocol.md (Pi side: acoustic/
//  tap_protocol.py + tap_bridge.py). The tap sequence mirrors sketch.ino's FSM
//  and acoustic/tap_sequencer.py.
//
//  What this file guarantees (each one is a test):
//   1. Boots with the belt STOPPED and the striker INHIBITED. Only a valid line
//      from the Pi (BELT n / STRIKE 1) changes that.
//   2. WATCHDOG: no valid line from the Pi for `watchdog_ms` -> belt 0 AND striker
//      inhibited, and neither comes back by itself: a fresh BELT / STRIKE is
//      needed. (Silence is the signal; the Pi never sends BELT 0 on shutdown.)
//   3. A line with a missing/wrong checksum is discarded, never acted on.
//   4. TAP is sent once per strike, the instant LOCK opens, with `tile_present`
//      read from a fresh striker-station ToF sample taken at that instant. If the
//      ToF cannot be read there is NO strike and an ERR instead -- never a TAP
//      that claims "empty" on no evidence.
//   5. FAIL-SAFE belt end: a silent / dead / never-seen belt-end ToF, or a tile
//      sitting at the belt end, holds the belt at 0 (the commanded speed is kept
//      and resumes when the sensor sees an empty end).
//   6. E-stop latches FAULT: both solenoids off, belt off, until reset/power cycle.
//   7. (striker_type SPRUNG, the 2026-10-19 prototype) ONE solenoid above the tile:
//      energised = striker retracted, de-energised = gravity + spring STRIKE. Both
//      edges make noise, so both are reported: `RET` when it is energised, `TAP`
//      when it is released. The release is the strike. MODE 0|1 chooses who starts
//      a cycle: the ToF (auto) or the Pi (manual: RETRACT, RELEASE, TAP). A coil
//      held energised longer than `retract_max_ms`, or an inhibit / watchdog trip
//      while retracted, releases it (a strike, reported with src SAFETY): the
//      striker is never left hanging on a coil.
//
//  STATUS: logic verified on the laptop. NOT compiled for AVR, NOT run on a
//  Nano: pin numbers, the ToF library calls, PWM frequency and the flash/RAM
//  fit are all unproven (see nano_firmware.ino and README.md).
// ============================================================================
#ifndef NANO_CORE_H
#define NANO_CORE_H

#include <stdint.h>
#include <string.h>

namespace nano {

enum { TOF_STRIKER = 1, TOF_END = 3 };  // names follow the wire protocol (tof1_ok / tof3_ok)

// ---- hardware abstraction -------------------------------------------------------
struct Hal {
  virtual ~Hal() {}
  virtual uint32_t nowMs() = 0;
  virtual void setBelt(uint8_t percent) = 0;   // applied belt speed 0..100
  virtual void setArm(bool on) = 0;            // ARM solenoid MOSFET (SPRUNG: THE striker coil, on = retracted)
  virtual void setLock(bool on) = 0;           // LOCK solenoid MOSFET (on = latch OPEN = ball drops); unused when SPRUNG
  virtual void setHoldPercent(uint8_t percent) { (void)percent; }   // SPRUNG: coil duty while the striker is held up
  virtual void setTapLed(bool on) = 0;
  virtual void setStatusLed(bool on) = 0;      // on = running, off = FAULT
  virtual bool estopAsserted() = 0;            // already polarity-corrected
  // One ranging sample. false = the sensor did not answer (unplugged, bus fault,
  // timeout). A sensor that answers "no target" returns true with a large mm.
  virtual bool readTof(int sensor, uint16_t* mm) = 0;
  virtual void writeLine(const char* line) = 0;  // a COMPLETE line: payload*HH\n
};

// ---- configuration (mirrors sketch.ino / acoustic/config.yaml tap_mechanism) ------
struct Config {
  uint16_t watchdog_ms;        // no valid Pi line for this long -> stop belt + inhibit striker
  uint16_t hb_ms;              // heartbeat period
  uint16_t tof_poll_ms;        // per-sensor sample period
  uint16_t tof_trigger_mm;     // striker ToF: a tile is "in place" at/below this
  uint8_t  tof_debounce;       // consecutive in-range samples required
  uint16_t tof_end_mm;         // belt-end ToF: a tile is "at the end" at/below this
  uint8_t  tof_end_debounce;
  uint8_t  tof_fail_samples;   // consecutive failed reads before a sensor is "not ok"
  bool     require_tof_end;    // false only for a bench with no belt-end sensor fitted
  bool     tof_end_fitted;     // false: the belt-end ToF (sensor 3) does not exist; it is not polled, no ERR TOF3
  uint16_t settle_ms, arm_ms, arm_settle_ms, lock_pulse_ms, cooldown_ms;
  uint16_t err_repeat_ms;      // re-send a persisting ERR no more often than this
  // ---- single sprung striker (prototype). Ignored unless striker_type == STRIKER_SPRUNG.
  uint8_t  striker_type;       // STRIKER_DUAL (ARM+LOCK ball drop) or STRIKER_SPRUNG
  uint16_t retract_hold_ms;    // energised -> release: lets the retraction noise die away before the strike
  uint16_t retract_max_ms;     // longest the coil may stay energised (heating); then released
  uint16_t strike_ms;          // release -> back to cooldown (the strike has landed)
  // ---- parked-retracted (owner, 2026-10-19): the REST position is UP.
  uint8_t  hold_pct;           // SPRUNG: coil duty (0-100) once the striker is up; the Pi can change it (HOLD n)
  bool     park_retracted;     // AUTO: with nobody under the sensor and the striker armed, the coil is energised
                               //   and the striker waits retracted; a tile releases it, the striker re-parks once the
                               //   tile is gone. NO retract_max_ms limit while parked: the coil is on for as long as
                               //   the line idles (heat: the hold duty in nano_firmware.ino is the only mitigation).
  uint16_t release_delay_ms;   // parked: a tile must be recognised this long before the release (the strike)
  // ---- request-only (owner, 2026-10-19: the coil overheated parked all the time): the striker RESTS DOWN and is
  // raised only when the Pi says a tile is coming (UP, from the camera's detection) or when a tile is already under
  // the ToF (the camera missed it). With nothing to do it is lowered again rest_delay_ms later.
  bool     request_only;
  uint16_t up_timeout_ms;      // an UP with no tile reaching the ToF this long is forgotten (then it rests down)
  uint16_t rest_delay_ms;      // raised, nothing under it, nothing requested, this long -> lower it to rest
  // ---- stricter "a tile is really there" test for the striker ToF only (owner, 2026-10-19: it struck with no tile).
  // 0 = off. A reading counts only inside [tof_min_mm, tof_trigger_mm], and the readings of one unbroken run must
  // stay within tof_steady_mm of each other, else the run restarts. A tile reads rock steady (91 mm); a hand, a belt
  // seam or sensor noise does not. Together with tof_debounce and release_delay_ms the tile must be seen steady for the
  // whole time before the striker is released.
  uint16_t tof_min_mm;
  uint16_t tof_steady_mm;
};

enum { STRIKER_DUAL = 0, STRIKER_SPRUNG = 1 };
// Why a strike happened (the 5th field of TAP / the 2nd of RET).
enum { SRC_TOF = 0, SRC_MANUAL = 1, SRC_SAFETY = 2, SRC_TOF_BLIND = 3, SRC_REST = 4 };
// SRC_REST: the striker was lowered back to its resting position with nothing to strike (a quiet drop onto the
// empty surface), so the coil is not left energised. Not a measurement; never paired with a tile.

inline Config defaultConfig() {
  Config c;
  c.watchdog_ms = 2000;
  c.hb_ms = 500;
  c.tof_poll_ms = 25;
  c.tof_trigger_mm = 80;
  c.tof_debounce = 5;
  c.tof_end_mm = 60;       // PROVISIONAL: mount distance vs belt coast is unmeasured
  c.tof_end_debounce = 3;
  c.tof_fail_samples = 3;
  c.require_tof_end = true;
  c.tof_end_fitted = true;
  c.settle_ms = 200;
  c.arm_ms = 300;
  c.arm_settle_ms = 150;
  c.lock_pulse_ms = 100;
  c.cooldown_ms = 1000;
  c.err_repeat_ms = 5000;
  c.striker_type = STRIKER_DUAL;   // the prototype sets STRIKER_SPRUNG (nano_firmware.ino)
  c.retract_hold_ms = 1500;        // PROVISIONAL: the retraction noise decay is unmeasured
  c.retract_max_ms = 8000;         // PROVISIONAL: the coil's duty rating is unmeasured
  c.strike_ms = 300;
  c.hold_pct = 60;
  c.park_retracted = false;
  c.release_delay_ms = 2000;
  c.request_only = false;
  c.up_timeout_ms = 30000;
  c.rest_delay_ms = 3000;
  c.tof_min_mm = 0;
  c.tof_steady_mm = 0;
  return c;
}

// The 2026-10-19 prototype, in one place so the board glue and the laptop tests use the SAME numbers:
// one sprung striker, ONE ToF (the striker station's; hand-turned belt, no belt-end sensor), a tile
// is "there" at <= 100 mm (measured: tile 91, empty surface 112-142; the owner expected 75 / 150), and the strike (the release) comes
// 2 s after the tile is first recognised, from a striker that rests RETRACTED (parked up while nothing
// is under it, re-parked when the tile leaves). PROVISIONAL: the 2 s is the owner's.
inline Config prototypeConfig() {
  Config c = defaultConfig();
  c.striker_type = STRIKER_SPRUNG;
  c.require_tof_end = false;
  c.tof_end_fitted = false;
  c.tof_trigger_mm = 100;       // MEASURED 2026-10-19 on the rig: tile 91 mm (rock steady), empty surface 112-142 mm
  c.settle_ms = 1500;           // (not used while parked)
  c.retract_hold_ms = 1500;     // (not used while parked)
  c.park_retracted = true;
  c.request_only = false;       // owner 2026-10-19: a fan now cools the coil and the CNC shield, so it stays UP all the time
                                // (request_only = true, rest DOWN until the camera asks, is kept and tested: nano_host --request-only)
  c.tof_debounce = 10;          // 250 ms of unbroken "tile" readings before it counts (was 5 = 125 ms)
  c.tof_min_mm = 40;            // closer than any tile (91 mm): a hand or an object on the sensor, not a tile
  c.tof_steady_mm = 10;         // a tile reads steady; readings of one run must stay within 10 mm of each other
  c.release_delay_ms = 2000;
  c.cooldown_ms = 1200;         // strike_ms 300 + this = it lifts again 1.5 s after the strike, after the ring has died
  c.hb_ms = 250;                // the GUI shows the phase and a countdown: twice as often as the 500 ms default
  c.hold_pct = 30;              // the coil overheated parked at 60 %; 30 is a starting point to tune with HOLD n. TOO LOW DROPS THE STRIKER.
  return c;
}

enum State : uint8_t { WAIT_TILE = 0, SETTLE, ARM, ARM_SETTLE, RELEASE, COOLDOWN, FAULT,
                       RETRACTED, STRUCK };  // the last two: SPRUNG only

static const char* const FW_ID = "nano-fw-0.2";

// ---- the core -------------------------------------------------------------------
class Core {
 public:
  Core(Hal& hal, const Config& cfg) : h_(hal), c_(cfg) {}

  // Safe outputs first, then announce ourselves.
  void begin() {
    uint32_t now = h_.nowMs();
    h_.setArm(false);
    h_.setLock(false);
    h_.setTapLed(false);
    h_.setBelt(0);
    hold_pct_ = c_.hold_pct;
    h_.setHoldPercent(hold_pct_);
    h_.setStatusLed(true);
    state_ = WAIT_TILE;
    entered_ = now;
    next_hb_ = now + c_.hb_ms;
    striker_ = Tof(); end_ = Tof();
    striker_.next = now;
    end_.next = now + c_.tof_poll_ms / 2;  // interleave the two sensors on the bus
    char p[40];
    size_t n = 0;
    n = putStr(p, n, "HELLO ");
    n = putStr(p, n, FW_ID);
    send(p, n);
  }

  // One received byte from the Pi.
  void feedByte(char ch) {
    if (ch == '\r') return;
    if (ch == '\n') {
      if (overflow_) badLine("line too long");
      else if (len_ > 0) handleLine();
      len_ = 0;
      overflow_ = false;
      return;
    }
    if (len_ < RX_MAX) buf_[len_++] = ch;
    else overflow_ = true;
  }

  // Call every pass of loop(); never blocks except on the ToF read.
  void update() {
    uint32_t now = h_.nowMs();

    if (h_.estopAsserted() && state_ != FAULT) enterFault();

    if (link_alive_ && (uint32_t)(now - last_valid_) >= c_.watchdog_ms) {
      link_alive_ = false;
      belt_cmd_ = 0;            // and it stays 0 until a fresh BELT
      strike_enabled_ = false;  // and inhibited until a fresh STRIKE 1
      tripped_ = true;
      sendErr("WATCHDOG", "no valid line from the Pi, belt stopped");
    }

    if (state_ != FAULT) {
      pollTof(striker_, TOF_STRIKER, c_.tof_trigger_mm, c_.tof_debounce, now, "TOF1");
      if (c_.tof_end_fitted) pollTof(end_, TOF_END, c_.tof_end_mm, c_.tof_end_debounce, now, "TOF3");
      runSequence(now);
    }

    applyBelt();

    if ((int32_t)(now - next_hb_) >= 0) {
      next_hb_ = now + c_.hb_ms;
      sendHeartbeat(now);
    }
  }

  // ---- read-only view, for the harness and tests -----------------------------------
  State state() const { return state_; }
  uint8_t beltApplied() const { return belt_applied_; }
  uint8_t beltCommanded() const { return belt_cmd_; }
  bool strikeEnabled() const { return strike_enabled_; }
  bool linkAlive() const { return link_alive_; }
  uint16_t seq() const { return seq_; }
  uint16_t badLines() const { return bad_lines_; }
  bool manualMode() const { return manual_mode_; }
  bool retracted() const { return state_ == RETRACTED; }
  uint16_t retractSeq() const { return retract_seq_; }

 private:
  enum { RX_MAX = 40 };

  struct Tof {
    bool ok = false;          // answered recently (false until the first good read)
    bool present = false;     // debounced "something in range"
    uint8_t fails = 0, streak = 0;
    uint8_t reads = 0;        // consecutive good readings (capped): "present" means nothing until it reaches the debounce
    uint16_t mm = 0xFFFF;
    uint16_t lo = 0, hi = 0;  // spread of the current run of "near" readings (striker sensor's steadiness test)
    uint32_t next = 0;
    uint32_t last_err = 0;
    bool err_sent = false;
  };

  // ---- tiny formatting helpers (no snprintf: flash on a 328P) -------------------
  static size_t putStr(char* o, size_t n, const char* s) {
    while (*s) o[n++] = *s++;
    return n;
  }
  static size_t putUint(char* o, size_t n, uint32_t v) {
    char t[10];
    uint8_t k = 0;
    do { t[k++] = (char)('0' + v % 10); v /= 10; } while (v);
    while (k) o[n++] = t[--k];
    return n;
  }
  static uint8_t xorOf(const char* p, size_t n) {
    uint8_t x = 0;
    for (size_t i = 0; i < n; i++) x ^= (uint8_t)p[i];
    return x;
  }
  static char hexDigit(uint8_t v) { return (char)(v < 10 ? '0' + v : 'A' + (v - 10)); }
  static int hexVal(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
  }

  // payload (no checksum) -> "payload*HH\n"
  void send(const char* payload, size_t n) {
    char o[64];
    if (n > 56) n = 56;
    memcpy(o, payload, n);
    uint8_t x = xorOf(payload, n);
    o[n++] = '*';
    o[n++] = hexDigit((uint8_t)(x >> 4));
    o[n++] = hexDigit((uint8_t)(x & 0x0F));
    o[n++] = '\n';
    o[n] = 0;
    h_.writeLine(o);
  }

  void sendErr(const char* code, const char* text) {
    char p[56];
    size_t n = putStr(p, 0, "ERR ");
    n = putStr(p, n, code);
    p[n++] = ' ';
    n = putStr(p, n, text);
    send(p, n);
  }

  void sendHeartbeat(uint32_t now) {
    char p[56];
    size_t n = putStr(p, 0, "HB ");
    n = putUint(p, n, now);
    p[n++] = ' ';
    n = putUint(p, n, belt_applied_);
    p[n++] = ' ';
    p[n++] = striker_.ok ? '1' : '0';
    p[n++] = ' ';
    p[n++] = end_.ok ? '1' : '0';
    if (sprung()) {  // three more fields only the prototype striker has: mode, retracted, ToF 1 distance
      p[n++] = ' ';
      p[n++] = manual_mode_ ? '1' : '0';
      p[n++] = ' ';
      p[n++] = (state_ == RETRACTED) ? '1' : '0';
      p[n++] = ' ';
      n = putUint(p, n, striker_.ok ? striker_.mm : 0xFFFF);   // 65535 = no reading / nothing in range
      // ... and what the sequence is doing, so the Pi's GUI can show it instead of guessing:
      p[n++] = ' ';
      p[n++] = strike_enabled_ ? '1' : '0';                    // armed (STRIKE 1) -- read back, not assumed
      p[n++] = ' ';
      n = putUint(p, n, (uint32_t)state_);                     // nano::State
      p[n++] = ' ';
      uint32_t left = 0;                                       // parked, a tile being timed: ms until the strike
      if (state_ == RETRACTED && parked_ && tile_timing_ && strike_enabled_) {
        uint32_t el = (uint32_t)(now - tile_since_);
        left = el >= c_.release_delay_ms ? 0 : c_.release_delay_ms - el;
      }
      n = putUint(p, n, left);
      p[n++] = ' ';
      p[n++] = tile_must_leave_ ? '1' : '0';                   // the tile under it was struck / was there at power-up
      p[n++] = ' ';
      n = putUint(p, n, hold_pct_);                            // coil duty while held up (settable: HOLD n)
      p[n++] = ' ';
      p[n++] = up_wanted_ ? '1' : '0';                         // the Pi asked for the striker up (a tile is coming)
    }
    send(p, n);
  }

  void sendTap(uint32_t now, bool present, uint16_t mm, uint8_t src = SRC_TOF) {
    char p[48];
    size_t n = putStr(p, 0, "TAP ");
    n = putUint(p, n, seq_);
    p[n++] = ' ';
    p[n++] = present ? '1' : '0';
    p[n++] = ' ';
    n = putUint(p, n, mm);
    p[n++] = ' ';
    n = putUint(p, n, now);
    if (sprung()) {
      p[n++] = ' ';
      n = putUint(p, n, src);
    }
    send(p, n);
  }

  void sendRetract(uint32_t now, uint8_t src) {
    char p[40];
    size_t n = putStr(p, 0, "RET ");
    n = putUint(p, n, retract_seq_);
    p[n++] = ' ';
    n = putUint(p, n, src);
    p[n++] = ' ';
    n = putUint(p, n, now);
    send(p, n);
  }

  // ---- receive ------------------------------------------------------------------
  void badLine(const char* why) {
    bad_lines_++;
    uint32_t now = h_.nowMs();
    if (!bad_err_sent_ || (uint32_t)(now - bad_err_last_) >= c_.err_repeat_ms) {
      bad_err_sent_ = true;
      bad_err_last_ = now;
      sendErr("BADLINE", why);  // rate limited: motor noise must not flood the Pi's alarms
    }
  }

  static bool parseUint(const char* s, size_t n, uint32_t maxv, uint32_t* out) {
    if (n == 0 || n > 5) return false;
    uint32_t v = 0;
    for (size_t i = 0; i < n; i++) {
      if (s[i] < '0' || s[i] > '9') return false;
      v = v * 10 + (uint32_t)(s[i] - '0');
    }
    if (v > maxv) return false;
    *out = v;
    return true;
  }

  void handleLine() {
    // The last '*' separates payload from the two checksum digits.
    int star = -1;
    for (int i = (int)len_ - 1; i >= 0; i--) {
      if (buf_[i] == '*') { star = i; break; }
    }
    if (star < 0) { badLine("no checksum"); return; }
    if ((int)len_ - star - 1 != 2) { badLine("bad checksum"); return; }
    int hi = hexVal(buf_[star + 1]), lo = hexVal(buf_[star + 2]);
    if (hi < 0 || lo < 0 || (uint8_t)((hi << 4) | lo) != xorOf(buf_, (size_t)star)) {
      badLine("bad checksum");
      return;
    }

    const char* p = buf_;
    size_t n = (size_t)star;
    size_t vlen = 0;
    while (vlen < n && p[vlen] != ' ') vlen++;
    const char* arg = (vlen < n) ? p + vlen + 1 : p + n;
    size_t alen = (vlen < n) ? n - vlen - 1 : 0;
    uint32_t v = 0;

    if (vlen == 4 && memcmp(p, "PING", 4) == 0 && parseUint(arg, alen, 65535, &v)) {
      markValid();
      char o[24];
      size_t k = putStr(o, 0, "PONG ");
      k = putUint(o, k, v);
      send(o, k);
    } else if (vlen == 4 && memcmp(p, "BELT", 4) == 0 && parseUint(arg, alen, 100, &v)) {
      markValid();
      belt_cmd_ = (uint8_t)v;
    } else if (vlen == 6 && memcmp(p, "STRIKE", 6) == 0 && alen == 1 && (arg[0] == '0' || arg[0] == '1')) {
      markValid();
      strike_enabled_ = (arg[0] == '1');
    } else if (vlen == 4 && memcmp(p, "MODE", 4) == 0 && alen == 1 && (arg[0] == '0' || arg[0] == '1')) {
      markValid();
      if (!sprung()) { sendErr("UNSUPPORTED", "MODE needs the sprung striker"); return; }
      manual_mode_ = (arg[0] == '1');
      if (manual_mode_ && state_ == SETTLE) enter(WAIT_TILE, h_.nowMs());  // an auto cycle not yet started is cancelled
      if (state_ == RETRACTED) {
        entered_ = h_.nowMs();   // the hold-time limit counts from NOW: a striker parked for minutes must not be released by the mode switch itself
        // Already up when the mode changes. MANUAL: it is now held for an operator RELEASE. AUTO (parking
        // build): it becomes the parked striker and waits for a tile.
        if (manual_mode_) { hold_for_release_ = true; parked_ = false; }
        else if (c_.park_retracted) { hold_for_release_ = false; parked_ = true; tile_timing_ = false; }
      }
    } else if (vlen == 2 && memcmp(p, "UP", 2) == 0 && alen == 0) {
      markValid();
      if (!sprung() || !c_.park_retracted) { sendErr("UNSUPPORTED", "UP needs the parking striker"); return; }
      if (c_.request_only) {   // always-up build: it is already up, so the Pi's UP is accepted and has nothing to do
        up_wanted_ = true;
        up_since_ = h_.nowMs();
      }
    } else if (vlen == 4 && memcmp(p, "HOLD", 4) == 0 && parseUint(arg, alen, 100, &v)) {
      markValid();
      if (!sprung()) { sendErr("UNSUPPORTED", "HOLD needs the sprung striker"); return; }
      hold_pct_ = (uint8_t)v;
      h_.setHoldPercent(hold_pct_);
    } else if (vlen == 7 && memcmp(p, "RETRACT", 7) == 0 && alen == 0) {
      markValid();
      manualStart(true);
    } else if (vlen == 3 && memcmp(p, "TAP", 3) == 0 && alen == 0) {
      markValid();
      manualStart(false);
    } else if (vlen == 7 && memcmp(p, "RELEASE", 7) == 0 && alen == 0) {
      markValid();
      if (!sprung()) sendErr("UNSUPPORTED", "RELEASE needs the sprung striker");
      else if (state_ == RETRACTED && hold_for_release_) releaseNow(h_.nowMs(), SRC_MANUAL);
      else sendErr("NOTRETRACTED", "nothing is held retracted");
    } else {
      badLine("unknown or malformed command");
    }
  }

  // RETRACT (hold_for_release = true: wait for RELEASE) and TAP (false: release by itself after the hold).
  void manualStart(bool hold_for_release) {
    if (!sprung()) { sendErr("UNSUPPORTED", "needs the sprung striker"); return; }
    if (state_ == FAULT) { sendErr("FAULT", "e-stop latched"); return; }
    if (!manual_mode_) { sendErr("NOTMANUAL", "MODE 1 first"); return; }
    if (!strike_enabled_) { sendErr("INHIBITED", "STRIKE 1 first"); return; }
    if (state_ != WAIT_TILE) { sendErr("BUSY", "a cycle is already running"); return; }
    beginRetract(h_.nowMs(), SRC_MANUAL, hold_for_release);
  }

  void markValid() {
    last_valid_ = h_.nowMs();
    link_alive_ = true;
    if (tripped_) {
      // The Pi is back but nothing restarts by itself. The WATCHDOG error above
      // was probably never delivered (the Pi was gone), so say it again now: it
      // is what lets the bridge re-send the last BELT / STRIKE if it is set to.
      tripped_ = false;
      sendErr("WDOGRESET", "Pi is back; belt and striker need a fresh command");
    }
  }

  // ---- ToF ------------------------------------------------------------------------
  void pollTof(Tof& t, int sensor, uint16_t near_mm, uint8_t debounce, uint32_t now, const char* name) {
    if ((int32_t)(now - t.next) < 0) return;
    t.next = now + c_.tof_poll_ms;
    uint16_t mm = 0;
    if (h_.readTof(sensor, &mm)) {
      t.fails = 0;
      if (t.reads < 255) t.reads++;
      t.ok = true;
      t.err_sent = false;
      t.mm = mm;
      bool strict = (sensor == TOF_STRIKER);
      if (mm <= near_mm && !(strict && mm < c_.tof_min_mm)) {
        if (t.streak == 0) { t.lo = t.hi = mm; }
        else {
          if (mm < t.lo) t.lo = mm;
          if (mm > t.hi) t.hi = mm;
        }
        if (strict && c_.tof_steady_mm && (uint16_t)(t.hi - t.lo) > c_.tof_steady_mm) {
          t.streak = 1;           // not steady: a tile does not wobble. Start the run again from this reading.
          t.lo = t.hi = mm;
        } else if (t.streak < 255) {
          t.streak++;
        }
      } else {
        t.streak = 0;
      }
      t.present = t.streak >= debounce;
    } else {
      if (t.fails < 255) t.fails++;
      t.reads = 0;
      if (t.fails >= c_.tof_fail_samples) {
        t.ok = false;
        t.present = false;
        t.streak = 0;
        if (!t.err_sent || (uint32_t)(now - t.last_err) >= c_.err_repeat_ms) {
          t.err_sent = true;
          t.last_err = now;
          sendErr(name, "timeout");
        }
      }
    }
  }

  // ---- the tap sequence -------------------------------------------------------------
  void enter(State s, uint32_t now) {
    state_ = s;
    entered_ = now;
  }
  bool elapsed(uint32_t now, uint16_t ms) const { return (uint32_t)(now - entered_) >= ms; }

  void runSequence(uint32_t now) {
    switch (state_) {
      case WAIT_TILE:
        if (sprung() && c_.park_retracted && c_.request_only) {
          // Rests DOWN. Raised only when armed and (the Pi asked: a tile is coming) or (a tile is already under
          // the sensor: the camera missed it, or the previous tile has to be freed after its strike).
          if (up_wanted_ && (uint32_t)(now - up_since_) >= c_.up_timeout_ms) up_wanted_ = false;   // never came
          // An empty sensor seen while resting down also clears "the tile under it must leave" (power-up flag),
          // otherwise a tile the camera missed would be raised for but never struck.
          if (striker_.ok && striker_.reads >= c_.tof_debounce && !striker_.present) tile_must_leave_ = false;
          if (!manual_mode_ && strike_enabled_ && striker_.ok && striker_.reads >= c_.tof_debounce &&
              (up_wanted_ || striker_.present)) {
            beginRetract(now, SRC_TOF, false);
            parked_ = true;
            tile_timing_ = false;
            idle_timing_ = false;
          }
          break;
        }
        if (sprung() && c_.park_retracted) {
          // Rest position is UP: go up whenever it is in AUTO and the sensor answers -- armed or not, even
          // before the Pi has said a word. ARMING (STRIKE 1) only allows the release. After a strike it
          // lifts again EVEN THOUGH the tile is still under it (the tile cannot be moved out while the
          // striker is down on it); that tile is not struck again, it must leave first (tile_must_leave_).
          if (!manual_mode_ && striker_.ok && striker_.reads >= c_.tof_debounce &&
              (!striker_.present || tile_must_leave_)) {
            beginRetract(now, SRC_TOF, false);
            parked_ = true;
            tile_timing_ = false;
          }
          break;
        }
        if (!striker_.present) {
          tile_was_absent_ = true;
        } else if (tile_was_absent_) {
          tile_was_absent_ = false;  // consumed even if inhibited (or in manual mode): no strike for a tile that arrived while held
          if (strike_enabled_ && striker_.ok && !manual_mode_) enter(SETTLE, now);
        }
        break;
      case SETTLE:
        if (elapsed(now, c_.settle_ms)) {
          if (sprung()) {
            beginRetract(now, SRC_TOF, false);
          } else {
            h_.setArm(true);
            enter(ARM, now);
          }
        }
        break;
      case ARM:
        if (elapsed(now, c_.arm_ms)) {
          h_.setArm(false);
          enter(ARM_SETTLE, now);
        }
        break;
      case ARM_SETTLE:
        if (elapsed(now, c_.arm_settle_ms)) {
          // A fresh sample AT the strike: tile_present must not be a stale value.
          uint16_t mm = 0;
          if (!h_.readTof(TOF_STRIKER, &mm)) {
            // No evidence either way: do not strike and do not claim "empty".
            sendErr("TOF1", "no reading at strike, not struck");
            enter(COOLDOWN, now);
            break;
          }
          h_.setLock(true);  // latch open: the ball drops now
          h_.setTapLed(true);
          seq_++;
          sendTap(now, mm <= c_.tof_trigger_mm, mm);
          enter(RELEASE, now);
        }
        break;
      case RELEASE:
        if (elapsed(now, c_.lock_pulse_ms)) {
          h_.setLock(false);
          h_.setTapLed(false);
          enter(COOLDOWN, now);
        }
        break;
      case COOLDOWN:
        if (elapsed(now, c_.cooldown_ms)) enter(WAIT_TILE, now);
        break;
      case RETRACTED:
        if (parked_) {
          // Waiting up for a tile. It must stay recognised for release_delay_ms; if it leaves first
          // (a hand-turned belt can hesitate) the countdown is dropped and the striker stays up.
          // Not armed (or the Pi gone): a tile does NOT release it, and arming later starts a fresh 2 s.
          if (striker_.reads < c_.tof_debounce) {
            tile_timing_ = false;           // the sensor has not settled yet: "no tile" is not yet known
          } else if (!striker_.present) {
            tile_must_leave_ = false;       // the tile (if there was one) has gone: the next one counts
            tile_timing_ = false;
          } else if (tile_must_leave_ || !strike_enabled_) {
            tile_timing_ = false;           // the same tile as last time, or not armed: do not time it
          } else if (!tile_timing_) {
            tile_timing_ = true;
            tile_since_ = now;
          } else if ((uint32_t)(now - tile_since_) >= c_.release_delay_ms) {
            releaseNow(now, SRC_TOF);
          }
          if (c_.request_only && state_ == RETRACTED) {
            // Raised with nothing to do (no tile, none requested, not armed, or a dead sensor): do not stay
            // energised -- lower it again after rest_delay_ms. This is what keeps the coil cool.
            if (up_wanted_ && (uint32_t)(now - up_since_) >= c_.up_timeout_ms) up_wanted_ = false;
            bool settled = striker_.ok && striker_.reads >= c_.tof_debounce;
            // Never lowered onto a tile that is under it (that would be a strike nobody asked for): it stays up
            // until the tile has gone, unless the sensor is dead (then it lowers: cool beats blind).
            bool idle = !striker_.ok || (settled && !striker_.present && (!up_wanted_ || !strike_enabled_));
            if (idle) {
              if (!idle_timing_) { idle_timing_ = true; idle_since_ = now; }
              else if ((uint32_t)(now - idle_since_) >= c_.rest_delay_ms) releaseNow(now, SRC_REST);
            } else {
              idle_timing_ = false;
            }
          }
        } else if (!strike_enabled_) {
          sendErr("INHIBITED", "released: striker inhibited while retracted");
          releaseNow(now, SRC_SAFETY);
        } else if (elapsed(now, c_.retract_max_ms)) {
          sendErr("RETRACT", "held retracted too long, released");
          releaseNow(now, SRC_SAFETY);
        } else if (!hold_for_release_ && elapsed(now, c_.retract_hold_ms)) {
          releaseNow(now, strike_src_);
        }
        break;
      case STRUCK:
        if (elapsed(now, c_.strike_ms)) {
          h_.setTapLed(false);
          enter(COOLDOWN, now);
        }
        break;
      case FAULT:
        break;
    }
  }

  bool sprung() const { return c_.striker_type == STRIKER_SPRUNG; }

  // Energise the coil: the striker goes up. The retraction is a sound of its own, so it is announced.
  void beginRetract(uint32_t now, uint8_t src, bool hold_for_release) {
    h_.setArm(true);
    retract_seq_++;
    strike_src_ = src;
    hold_for_release_ = hold_for_release;
    parked_ = false;                 // the caller sets it for a parking retract
    sendRetract(now, src);
    enter(RETRACTED, now);
  }

  // De-energise the coil: gravity + spring strike. A fresh ToF sample goes with it so
  // tile_present is not a stale value. A ToF that cannot be read does NOT stop the
  // strike (the coil must come down) but is reported as such, never as "empty".
  void releaseNow(uint32_t now, uint8_t src) {
    uint16_t mm = 0;
    bool ok = h_.readTof(TOF_STRIKER, &mm);
    h_.setArm(false);
    h_.setTapLed(true);
    seq_++;
    if (c_.park_retracted) tile_must_leave_ = (src != SRC_REST);   // whatever is under it now must leave before it can be struck again
    if (src == SRC_TOF) up_wanted_ = false;                         // the requested tile has been dealt with
    idle_timing_ = false;
    if (ok) {
      sendTap(now, mm <= c_.tof_trigger_mm, mm, src);
    } else {
      if (src == SRC_TOF) {
        sendErr("TOF1", "no reading at strike");
        src = SRC_TOF_BLIND;
      }
      sendTap(now, false, 0xFFFF, src);
    }
    enter(STRUCK, now);
  }

  void enterFault() {
    if (state_ == RETRACTED) {  // the coil dropping IS a strike; say so before the fault
      seq_++;
      sendTap(h_.nowMs(), false, 0xFFFF, SRC_SAFETY);
    }
    h_.setArm(false);
    h_.setLock(false);   // fail closed: latch holds, no drop
    h_.setTapLed(false);
    h_.setStatusLed(false);
    state_ = FAULT;
    sendErr("ESTOP", "e-stop asserted, latched until reset");
  }

  void applyBelt() {
    uint8_t target = belt_cmd_;
    if (state_ == FAULT) target = 0;
    if (c_.require_tof_end && (!end_.ok || end_.present)) target = 0;  // fail safe: unknown = stop
    if (target != belt_applied_) {
      belt_applied_ = target;
      h_.setBelt(target);
    }
  }

  Hal& h_;
  Config c_;

  State state_ = WAIT_TILE;
  uint32_t entered_ = 0;
  bool tile_was_absent_ = true;
  uint16_t seq_ = 0;

  bool link_alive_ = false;
  uint32_t last_valid_ = 0;
  uint8_t belt_cmd_ = 0;
  uint8_t belt_applied_ = 0;
  bool strike_enabled_ = false;
  uint8_t hold_pct_ = 60;
  bool manual_mode_ = false;        // SPRUNG: the Pi, not the ToF, starts cycles
  bool hold_for_release_ = false;   // RETRACTED by RETRACT: wait for RELEASE instead of releasing by itself
  bool parked_ = false;             // RETRACTED as the rest position (AUTO, park_retracted): a tile releases it
  bool tile_timing_ = false;        // parked: a tile is being timed toward release_delay_ms
  bool up_wanted_ = false;          // request-only: the Pi said a tile is coming (UP)
  uint32_t up_since_ = 0;
  bool idle_timing_ = false;        // request-only: raised with nothing to do, counting toward rest_delay_ms
  uint32_t idle_since_ = 0;
  bool tile_must_leave_ = true;     // parked: the tile under it (struck already, or there at power-up) is not struck (again)
  uint32_t tile_since_ = 0;
  uint8_t strike_src_ = SRC_TOF;
  uint16_t retract_seq_ = 0;
  bool tripped_ = false;  // the watchdog stopped things and no valid line has arrived since

  Tof striker_, end_;
  uint32_t next_hb_ = 0;

  char buf_[RX_MAX];
  uint8_t len_ = 0;
  bool overflow_ = false;
  uint16_t bad_lines_ = 0;
  bool bad_err_sent_ = false;
  uint32_t bad_err_last_ = 0;
};

}  // namespace nano

#endif  // NANO_CORE_H
