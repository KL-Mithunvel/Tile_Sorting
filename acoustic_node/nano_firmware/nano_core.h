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
  virtual void setArm(bool on) = 0;            // ARM solenoid MOSFET
  virtual void setLock(bool on) = 0;           // LOCK solenoid MOSFET (on = latch OPEN = ball drops)
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
  uint16_t settle_ms, arm_ms, arm_settle_ms, lock_pulse_ms, cooldown_ms;
  uint16_t err_repeat_ms;      // re-send a persisting ERR no more often than this
};

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
  c.settle_ms = 200;
  c.arm_ms = 300;
  c.arm_settle_ms = 150;
  c.lock_pulse_ms = 100;
  c.cooldown_ms = 1000;
  c.err_repeat_ms = 5000;
  return c;
}

enum State : uint8_t { WAIT_TILE = 0, SETTLE, ARM, ARM_SETTLE, RELEASE, COOLDOWN, FAULT };

static const char* const FW_ID = "nano-fw-0.1";

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
      sendErr("WATCHDOG", "no valid line from the Pi, belt stopped");
    }

    if (state_ != FAULT) {
      pollTof(striker_, TOF_STRIKER, c_.tof_trigger_mm, c_.tof_debounce, now, "TOF1");
      pollTof(end_, TOF_END, c_.tof_end_mm, c_.tof_end_debounce, now, "TOF3");
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

 private:
  enum { RX_MAX = 40 };

  struct Tof {
    bool ok = false;          // answered recently (false until the first good read)
    bool present = false;     // debounced "something in range"
    uint8_t fails = 0, streak = 0;
    uint16_t mm = 0xFFFF;
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
    char p[48];
    size_t n = putStr(p, 0, "HB ");
    n = putUint(p, n, now);
    p[n++] = ' ';
    n = putUint(p, n, belt_applied_);
    p[n++] = ' ';
    p[n++] = striker_.ok ? '1' : '0';
    p[n++] = ' ';
    p[n++] = end_.ok ? '1' : '0';
    send(p, n);
  }

  void sendTap(uint32_t now, bool present, uint16_t mm) {
    char p[48];
    size_t n = putStr(p, 0, "TAP ");
    n = putUint(p, n, seq_);
    p[n++] = ' ';
    p[n++] = present ? '1' : '0';
    p[n++] = ' ';
    n = putUint(p, n, mm);
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
    } else {
      badLine("unknown or malformed command");
    }
  }

  void markValid() {
    last_valid_ = h_.nowMs();
    link_alive_ = true;
  }

  // ---- ToF ------------------------------------------------------------------------
  void pollTof(Tof& t, int sensor, uint16_t near_mm, uint8_t debounce, uint32_t now, const char* name) {
    if ((int32_t)(now - t.next) < 0) return;
    t.next = now + c_.tof_poll_ms;
    uint16_t mm = 0;
    if (h_.readTof(sensor, &mm)) {
      t.fails = 0;
      t.ok = true;
      t.err_sent = false;
      t.mm = mm;
      if (mm <= near_mm) {
        if (t.streak < 255) t.streak++;
      } else {
        t.streak = 0;
      }
      t.present = t.streak >= debounce;
    } else {
      if (t.fails < 255) t.fails++;
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
        if (!striker_.present) {
          tile_was_absent_ = true;
        } else if (tile_was_absent_) {
          tile_was_absent_ = false;  // consumed even if inhibited: no strike for a tile that arrived while held
          if (strike_enabled_ && striker_.ok) enter(SETTLE, now);
        }
        break;
      case SETTLE:
        if (elapsed(now, c_.settle_ms)) {
          h_.setArm(true);
          enter(ARM, now);
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
      case FAULT:
        break;
    }
  }

  void enterFault() {
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
