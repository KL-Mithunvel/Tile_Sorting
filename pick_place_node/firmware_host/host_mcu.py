"""Drive the compiled firmware core (gantry_host) like a serial MCU.

`HostMcu` has the same surface as `pick_place.fake_mcu.SimulatedMcu`
(`write(line)`, `read_lines()`, `tick(dt_s)`), so a `LoopbackLink(HostMcu(...))`
slots into the JobRunner / GantrySession tests in place of the Python
simulation, and the same scenarios run against the real C++ logic.

Test tooling only - nothing in the shipped node imports this.

Finding a compiler: `GANTRY_HOST_CXX` (a command, e.g. "g++" or
"python -m ziglang c++"), else c++ / g++ / clang++ on PATH. `build_host()` returns
None when there is none, and the tests skip.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from collections import deque
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "gantry_host.cpp"
SKETCH_DIR = HERE.parent / "sketch"


def find_compiler() -> Optional[list]:
    env = os.environ.get("GANTRY_HOST_CXX")
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
    exe = Path(out_dir) / ("gantry_host.exe" if sys.platform == "win32" else "gantry_host")
    cmd = cxx + ["-std=c++11", "-O2", "-Wall", "-Wextra", "-I", str(SKETCH_DIR), "-o", str(exe), str(SOURCE)]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if done.returncode != 0:
        raise RuntimeError(f"building the firmware host failed:\n{done.stdout}\n{done.stderr}")
    return exe


class HostMcu:
    def __init__(self, exe, *args: str):
        self._p = subprocess.Popen([str(exe), *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, bufsize=1)
        self._out: deque = deque()
        self._carry_ms = 0.0
        self.received: list = []
        self.closed = False
        self._out.extend(self._exchange(""))  # whatever begin() printed

    # ---- the wire (same names as SimulatedMcu) --------------------------------------

    def write(self, line: str) -> None:
        self.received.append(line.strip())
        self._out.extend(self._exchange(line.strip()))

    def read_lines(self) -> list:
        out = list(self._out)
        self._out.clear()
        return out

    def tick(self, dt_s: float) -> None:
        self._carry_ms += dt_s * 1000.0
        n = int(self._carry_ms)
        if n > 0:
            self._carry_ms -= n
            self._out.extend(self._exchange(f"@tick {n}"))

    # ---- harness controls -------------------------------------------------------------

    def control(self, text: str) -> list:
        """Send an '@...' control, return its '@...' reply lines; protocol lines the
        firmware printed meanwhile are queued for read_lines()."""
        lines = self._exchange(text, keep_controls=True)
        replies = [l for l in lines if l.startswith("@")]
        self._out.extend(l for l in lines if not l.startswith("@"))
        return replies

    def phys(self, axis: str) -> float:
        return float(self.control(f"@phys {axis}")[0].split()[2])

    def steps(self, axis: str) -> int:
        return int(self.control(f"@steps {axis}")[0].split()[2])

    def fmt(self, v: float) -> str:
        return self.control(f"@fmt {v!r}")[0].split(" ", 1)[1]

    # ---- plumbing --------------------------------------------------------------------------

    def _exchange(self, line: str, keep_controls: bool = False) -> list:
        if self.closed or self._p.poll() is not None:
            raise RuntimeError("the firmware host has exited: " + (self._p.stderr.read() if self._p.stderr else ""))
        self._p.stdin.write(line + "\n")
        self._p.stdin.flush()
        out = []
        while True:
            got = self._p.stdout.readline()
            if got == "":
                raise RuntimeError("the firmware host closed its output")
            got = got.rstrip("\r\n")
            if got == "@end":
                return out
            if got.startswith("@") and not keep_controls:
                continue
            out.append(got)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self._p.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                self._p.wait(timeout=5)
            except Exception:  # noqa: BLE001
                self._p.kill()
            for stream in (self._p.stdout, self._p.stderr):
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
