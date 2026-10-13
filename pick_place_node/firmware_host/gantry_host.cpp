// ============================================================================
//  gantry_host.cpp  --  run the gantry firmware core on a PC, against a
//                       simulated machine, over stdin / stdout
// ============================================================================
//
//  Build (any C++11 compiler):
//      c++ -std=c++11 -O2 -Wall -Wextra -I ../sketch -o gantry_host gantry_host.cpp
//  The tests (tests/test_gantry_firmware_conformance.py) build and drive it.
//
//  Protocol on stdin / stdout:
//    - a normal protocol line ("12 MOVE X10 F1000") is handed to the firmware;
//      whatever the firmware writes comes back on stdout, one line each
//    - a line starting with '@' is a harness control (below); nothing is sent to
//      the firmware
//    - after EVERY input line the harness prints "@end", so the caller can read
//      synchronously
//
//  Harness controls:
//    @tick <ms>              advance simulated time (steps + poll once per ms)
//    @vac_confirm <ms>       pump-on -> sensor-confirms delay (default 50)
//    @vac_fail <n>           the next n VAC ON never confirm
//    @vac_hold <ms>          the sensor confirms for only this long, then drops (0 = stays)
//    @switch <X|Y|Z> <mm>    move a limit switch to this real position (unreachable = homing fails)
//    @estop <0|1>            e-stop loop open (1)
//    @limit <X|Y|Z> <0|1>    force a limit switch on (1) / back to physical (0)
//    @enc_offset <X|Y|Z|A> <mm>   add a fixed error to that encoder (lost steps / skew)
//    @enc_fail <X|Y|Z|A> <0|1>    that encoder stops answering
//    @phys <X|Y|Z>           print the real (simulated) position of an axis
//    @steps <X|Y|Z>          print how many STEP pulses that axis has received
//    @dump                   print "@dump k=v ..." with what the simulation knows
//
//  Options: --fast (750 mm/s, 20 steps/mm, SETSPEED ignored, so whole pick-and-place
//  cycles take simulated seconds, not minutes) --encoders --link-timeout <ms>
//  --vac-timeout <ms> --tick-us <n>
//
//  The simulated machine: each axis is a point whose real position follows the
//  STEP pulses exactly; the limit switch sits at a fixed real position; the real
//  position at power-up is unknown to the firmware (that is what homing is for).
// ============================================================================
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <string>

#include "gantry_core.h"

using namespace gantry;

struct SimHal : Hal {
  Config cfg;
  double phys[AXES] = {100.0, 50.0, 20.0};       // real positions, mm (unknown to the firmware)
  double sw[AXES] = {0.0, 0.0, 220.0};            // real switch positions
  bool dirPos[AXES] = {true, true, true};
  long stepCount[AXES] = {0, 0, 0};
  bool forceLimit[AXES] = {false, false, false};
  bool estop = false;
  bool pump = false;
  uint32_t now = 0;
  uint32_t pumpSince = 0;
  uint32_t vacDelayMs = 50;
  int vacFail = 0;
  bool vacArmed = false;  // this pump-on will never confirm
  uint32_t vacHoldMs = 0;
  double encOff[ENCODERS] = {0, 0, 0, 0};
  bool encFail[ENCODERS] = {false, false, false, false};
  bool enabled = false;

  void setEnable(bool on) override { enabled = on; }
  void setDir(int a, bool positive) override { dirPos[a] = positive; }
  void stepPulse(int a) override {
    stepCount[a]++;
    phys[a] += (dirPos[a] ? 1.0 : -1.0) / cfg.axis[a].steps_per_mm;
  }
  bool limitTriggered(int a) override {
    if (forceLimit[a]) return true;
    return cfg.axis[a].home_dir < 0 ? phys[a] <= sw[a] : phys[a] >= sw[a];
  }
  bool estopAsserted() override { return estop; }
  void setVacuum(bool on) override {
    if (on && !pump) {
      pumpSince = now;
      vacArmed = vacFail > 0;
      if (vacArmed) vacFail--;
    }
    pump = on;
  }
  bool vacuumConfirmed() override {
    if (!pump || vacArmed) return false;
    uint32_t el = now - pumpSince;
    if (el < vacDelayMs) return false;
    return vacHoldMs == 0 || el < vacDelayMs + vacHoldMs;
  }
  bool readEncoder(int i, uint16_t* raw) override {
    if (encFail[i]) return false;
    double mm = (i == ENC_A ? phys[AX_Y] : phys[i]) + encOff[i];
    long counts = lround(mm / cfg.enc_mm_per_count[i]);
    long r = ((counts % 4096) + 4096) % 4096;
    *raw = (uint16_t)r;
    return true;
  }
  void write(const char* line) override {
    fputs(line, stdout);
    fputc('\n', stdout);
  }
  uint32_t nowMs() override { return now; }
};

static int axisIndex(const char* s) {
  return s[0] == 'X' ? 0 : s[0] == 'Y' ? 1 : s[0] == 'Z' ? 2 : s[0] == 'A' ? 3 : -1;
}

int main(int argc, char** argv) {
  SimHal hal;
  hal.cfg = Config::defaults();
  bool fast = false;
  for (int i = 1; i < argc; i++) {
    std::string a = argv[i];
    if (a == "--fast") fast = true;
    else if (a == "--encoders") hal.cfg.encoders_enabled = true;
    else if (a == "--link-timeout" && i + 1 < argc) hal.cfg.link_timeout_ms = (uint32_t)atol(argv[++i]);
    else if (a == "--vac-timeout" && i + 1 < argc) hal.cfg.vac_timeout_ms = (uint32_t)atol(argv[++i]);
    else if (a == "--tick-us" && i + 1 < argc) hal.cfg.tick_us = (uint32_t)atol(argv[++i]);
    else { fprintf(stderr, "unknown option %s\n", a.c_str()); return 2; }
  }
  if (fast) {
    for (int a = 0; a < AXES; a++) {
      hal.cfg.axis[a].steps_per_mm = 20.0f;
      hal.cfg.axis[a].max_mm_min = 45000.0f;
      hal.cfg.axis[a].accel_mm_s2 = 20000.0f;
    }
    hal.cfg.home_fast_mm_min = 30000.0f;
    hal.cfg.home_slow_mm_min = 6000.0f;
    for (int i = 0; i < ENCODERS; i++) hal.cfg.enc_mm_per_count[i] = 40.0f / 4096.0f;
  }
  // Same mm of motion per STEP in every mode is not required; encoders scale with the pulley.
  Gantry g(hal, hal.cfg);
  // --fast ignores SETSPEED by rebuilding the limits the core would otherwise be given.
  g.begin();

  char line[256];
  const uint32_t ticksPerMs = 1000u / hal.cfg.tick_us;
  while (fgets(line, sizeof line, stdin)) {
    size_t n = strlen(line);
    while (n > 0 && (line[n - 1] == '\n' || line[n - 1] == '\r')) line[--n] = 0;
    if (n == 0) { puts("@end"); fflush(stdout); continue; }
    if (line[0] != '@') {
      if (fast && strstr(line, " SETSPEED ")) {
        // acknowledge but keep the fast limits (see header)
        long seq = atol(line);
        printf("ok %ld\n", seq);
      } else {
        g.onLine(line);
      }
    } else {
      char cmd[32] = {0}, a1[32] = {0}, a2[32] = {0};
      int got = sscanf(line, "%31s %31s %31s", cmd, a1, a2);
      if (!strcmp(cmd, "@tick") && got >= 2) {
        long ms = atol(a1);
        for (long k = 0; k < ms; k++) {
          hal.now++;
          for (uint32_t t = 0; t < ticksPerMs; t++) g.tick();
          g.poll();
        }
      } else if (!strcmp(cmd, "@vac_confirm") && got >= 2) hal.vacDelayMs = (uint32_t)atol(a1);
      else if (!strcmp(cmd, "@vac_fail") && got >= 2) hal.vacFail = atoi(a1);
      else if (!strcmp(cmd, "@vac_hold") && got >= 2) hal.vacHoldMs = (uint32_t)atol(a1);
      else if (!strcmp(cmd, "@switch") && got >= 3) { int a = axisIndex(a1); if (a >= 0 && a < AXES) hal.sw[a] = atof(a2); }
      else if (!strcmp(cmd, "@estop") && got >= 2) hal.estop = atoi(a1) != 0;
      else if (!strcmp(cmd, "@limit") && got >= 3) { int a = axisIndex(a1); if (a >= 0 && a < AXES) hal.forceLimit[a] = atoi(a2) != 0; }
      else if (!strcmp(cmd, "@enc_offset") && got >= 3) { int a = axisIndex(a1); if (a >= 0) hal.encOff[a] = atof(a2); }
      else if (!strcmp(cmd, "@enc_fail") && got >= 3) { int a = axisIndex(a1); if (a >= 0) hal.encFail[a] = atoi(a2) != 0; }
      else if (!strcmp(cmd, "@phys") && got >= 2) { int a = axisIndex(a1); if (a >= 0 && a < AXES) printf("@phys %s %.4f\n", a1, hal.phys[a]); }
      else if (!strcmp(cmd, "@steps") && got >= 2) { int a = axisIndex(a1); if (a >= 0 && a < AXES) printf("@steps %s %ld\n", a1, hal.stepCount[a]); }
      else if (!strcmp(cmd, "@dump")) {
        printf("@dump enabled=%d pump=%d now=%u px=%.4f py=%.4f pz=%.4f\n", hal.enabled ? 1 : 0, hal.pump ? 1 : 0,
               (unsigned)hal.now, hal.phys[0], hal.phys[1], hal.phys[2]);
      } else if (!strcmp(cmd, "@fmt") && got >= 2) {
        char out[32];
        fmtMm((float)atof(a1), out, sizeof out);
        printf("@fmt %s\n", out);
      } else printf("@error unknown control %s\n", cmd);
    }
    puts("@end");
    fflush(stdout);
  }
  return 0;
}
