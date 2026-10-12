"""Drive the compiled Nano firmware core (nano_host) from Python.

Test tooling only - nothing in the shipped node imports this.

Finding a compiler: `NANO_HOST_CXX` (a command, e.g. "g++" or
"python -m ziglang c++"), else `GANTRY_HOST_CXX` (same convention, shared with
pick_place_node/firmware_host), else c++ / g++ / clang++ on PATH. `build_host()`
returns None when there is none, and the tests skip.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "nano_host.cpp"
CORE_DIR = HERE.parent / "nano_firmware"


def find_compiler() -> Optional[list]:
    for var in ("NANO_HOST_CXX", "GANTRY_HOST_CXX"):
        env = os.environ.get(var)
        if env:
            return shlex.split(env)
    for name in ("c++", "g++", "clang++"):
        found = shutil.which(name)
        if found:
            return [found]
    return None


def build_host(out_dir: Path) -> Optional[Path]:
    cxx = find_compiler()
    if cxx is None:
        return None
    exe = Path(out_dir) / ("nano_host.exe" if sys.platform == "win32" else "nano_host")
    cmd = cxx + ["-std=c++11", "-O2", "-Wall", "-Wextra", "-I", str(CORE_DIR), "-o", str(exe), str(SOURCE)]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if done.returncode != 0:
        raise RuntimeError(f"building the Nano host failed:\n{done.stdout}\n{done.stderr}")
    return exe


class HostNano:
    """One simulated Nano. `rx()` / `advance()` return the lines the firmware
    wrote (checksum included, newline stripped); `.pins` is the latest state."""

    def __init__(self, exe, *args: str):
        self._p = subprocess.Popen([str(exe), *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, bufsize=1)
        self.pins: dict = {}
        self.boot_lines = self._read_reply()

    def _read_reply(self) -> list:
        lines = []
        while True:
            raw = self._p.stdout.readline()
            if raw == "":
                err = self._p.stderr.read()
                raise RuntimeError(f"nano_host exited: {err}")
            raw = raw.rstrip("\n")
            if raw == ".":
                return lines
            if raw.startswith("> "):
                lines.append(raw[2:])
            elif raw.startswith("# "):
                self.pins = {k: int(v) for k, v in (kv.split("=") for kv in raw[2:].split())}

    def _cmd(self, text: str) -> list:
        self._p.stdin.write(text + "\n")
        self._p.stdin.flush()
        return self._read_reply()

    # ---- the wire --------------------------------------------------------------
    def rx(self, line) -> list:
        """Deliver one line (str or bytes, trailing newline optional)."""
        if isinstance(line, bytes):
            line = line.decode("ascii", errors="replace")
        return self._cmd("rx " + line.rstrip("\r\n"))

    def rx_raw(self, data: bytes) -> list:
        return self._cmd("rawhex " + data.hex())

    # ---- time and the world -------------------------------------------------------
    def advance(self, ms: int) -> list:
        out = []
        while ms > 0:
            step = min(ms, 500)
            out += self._cmd(f"adv {step}")
            ms -= step
        return out

    def tof(self, sensor: int, mm) -> None:
        self._cmd(f"tof {sensor} {'fail' if mm is None else int(mm)}")

    def estop(self, asserted: bool) -> None:
        self._cmd(f"estop {1 if asserted else 0}")

    def close(self) -> None:
        try:
            self._p.stdin.write("quit\n")
            self._p.stdin.flush()
        except Exception:  # noqa: BLE001
            pass
        self._p.terminate()
        try:
            self._p.wait(timeout=5)
        except Exception:  # noqa: BLE001
            self._p.kill()
