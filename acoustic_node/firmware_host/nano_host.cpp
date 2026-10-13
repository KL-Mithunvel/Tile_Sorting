// Laptop harness for acoustic_node/nano_firmware/nano_core.h.
//
// Compiles the REAL core against a simulated Hal and exposes it over stdin/stdout
// so Python can drive it with the real protocol module. Test tooling only.
//
// Commands on stdin (one per line); every command answers with zero or more
// lines of output and then a line containing just ".":
//   rx <text>        feed <text> + '\n' as received serial bytes
//   rawhex <hex>     feed raw bytes (no newline added)
//   adv <ms>         advance simulated time 1 ms at a time, calling update()
//   tof <1|3> <mm>   the sensor now reads <mm>        (65535 = "no target")
//   tof <1|3> fail   the sensor does not answer
//   estop <0|1>      e-stop loop open (1) / closed (0)
// Output lines:
//   > <line>         a line the firmware wrote to serial (checksum included)
//   # key=value ...  after every command: the simulated pins and core state

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <sstream>
#include <string>

#include "nano_core.h"

struct SimHal : nano::Hal {
  uint32_t now = 0;
  uint8_t belt = 0;
  bool arm = false, lock = false, tap_led = false, status_led = true;
  bool estop = false;
  bool tof_ok[4] = {false, true, false, true};
  uint16_t tof_mm[4] = {0, 65535, 0, 65535};
  int arm_rises = 0, lock_rises = 0;
  std::string out;

  uint32_t nowMs() override { return now; }
  void setBelt(uint8_t p) override { belt = p; }
  void setArm(bool on) override { if (on && !arm) arm_rises++; arm = on; }
  void setLock(bool on) override { if (on && !lock) lock_rises++; lock = on; }
  void setTapLed(bool on) override { tap_led = on; }
  void setStatusLed(bool on) override { status_led = on; }
  bool estopAsserted() override { return estop; }
  bool readTof(int s, uint16_t* mm) override {
    if (s < 0 || s > 3 || !tof_ok[s]) return false;
    *mm = tof_mm[s];
    return true;
  }
  void writeLine(const char* line) override {
    // one "> " record per line, newline stripped
    std::string l(line);
    while (!l.empty() && (l.back() == '\n' || l.back() == '\r')) l.pop_back();
    out += "> " + l + "\n";
  }
};

static int hexv(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

int main(int argc, char** argv) {
  SimHal hal;
  nano::Config cfg = nano::defaultConfig();
  for (int i = 1; i < argc; i++) {  // options, for tests that need other settings
    std::string a = argv[i];
    if (a == "--no-tof-end") cfg.require_tof_end = false;
    else if (a == "--watchdog" && i + 1 < argc) cfg.watchdog_ms = (uint16_t)std::atol(argv[++i]);
  }
  nano::Core core(hal, cfg);
  core.begin();

  auto report = [&]() {
    std::fputs(hal.out.c_str(), stdout);
    hal.out.clear();
    std::printf("# belt=%u cmd=%u arm=%d lock=%d tapled=%d status=%d state=%d seq=%u strike=%d link=%d bad=%u "
                "arm_rises=%d lock_rises=%d now=%u\n",
                hal.belt, core.beltCommanded(), hal.arm, hal.lock, hal.tap_led, hal.status_led,
                (int)core.state(), core.seq(), core.strikeEnabled(), core.linkAlive(), core.badLines(),
                hal.arm_rises, hal.lock_rises, hal.now);
    std::puts(".");
    std::fflush(stdout);
  };

  report();  // what begin() printed
  std::string line;
  while (std::getline(std::cin, line)) {
    if (!line.empty() && line.back() == '\r') line.pop_back();
    std::istringstream is(line);
    std::string cmd;
    is >> cmd;
    if (cmd == "rx") {
      std::string rest;
      std::getline(is, rest);
      if (!rest.empty() && rest[0] == ' ') rest.erase(0, 1);
      for (char c : rest) core.feedByte(c);
      core.feedByte('\n');
    } else if (cmd == "rawhex") {
      std::string h;
      is >> h;
      for (size_t i = 0; i + 1 < h.size(); i += 2) {
        int a = hexv(h[i]), b = hexv(h[i + 1]);
        if (a >= 0 && b >= 0) core.feedByte((char)((a << 4) | b));
      }
    } else if (cmd == "adv") {
      long ms = 0;
      is >> ms;
      for (long i = 0; i < ms; i++) {
        hal.now++;
        core.update();
      }
    } else if (cmd == "tof") {
      int s = 0;
      std::string v;
      is >> s >> v;
      if (s == 1 || s == 3) {
        if (v == "fail") hal.tof_ok[s] = false;
        else { hal.tof_ok[s] = true; hal.tof_mm[s] = (uint16_t)std::atol(v.c_str()); }
      }
    } else if (cmd == "estop") {
      int v = 0;
      is >> v;
      hal.estop = v != 0;
    } else if (cmd == "quit") {
      break;
    }
    report();
  }
  return 0;
}
