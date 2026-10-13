"""Drive the master's pages in a real headless Edge over the DevTools protocol.

Stdlib only (no selenium/playwright). Exercises what the unit tests cannot: the
pages' JavaScript - no console/page errors on load, station selection, view
buttons, belt stop/start, hold-all, window switches, table toggle, and that
Explain is disabled with no provider. Run against a DEMO master (it sends real
commands to whatever it is pointed at):

    cd master_node/python && python -m master.live_master --demo
    python development/drive_master_pages.py [--edge PATH] [--base http://localhost:8000]

Not covered: clicking a station inside the 3D canvas (needs a projected pixel),
and the Explain flow with a real model.
"""
import base64, json, os, socket, struct, subprocess, sys, tempfile, time, urllib.request

import argparse
import shutil

_ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
_ap.add_argument("--edge", default=None, help="path to msedge.exe / chrome (default: auto-detect)")
_ap.add_argument("--base", default="http://localhost:8000")
_a = _ap.parse_args()
BASE = _a.base.rstrip("/")
EDGE = _a.edge or next((p for p in (
    shutil.which("msedge"), r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", shutil.which("chromium"),
    shutil.which("google-chrome")) if p and os.path.exists(p)), None)
if not EDGE:
    sys.exit("no Edge/Chrome found; pass --edge PATH")
PORT = 9333


class WS:
    def __init__(self, url):
        host, rest = url[5:].split("/", 1)
        h, p = host.split(":")
        self.s = socket.create_connection((h, int(p)), timeout=20)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET /{rest} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.s.recv(1)
        self.buf = b""
        self.id = 0
        self.events = []

    def _send(self, text):
        data = text.encode()
        hdr = bytearray([0x81])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126); hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127); hdr += struct.pack(">Q", n)
        mask = os.urandom(4)
        hdr += mask
        self.s.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _recv_exact(self, n):
        while len(self.buf) < n:
            chunk = self.s.recv(65536)
            if not chunk:
                raise EOFError
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _recv_frame(self):
        b1, b2 = self._recv_exact(2)
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._recv_exact(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._recv_exact(8))[0]
        return self._recv_exact(n).decode("utf-8", "replace")

    def call(self, method, **params):
        self.id += 1
        want = self.id
        self._send(json.dumps({"id": want, "method": method, "params": params}))
        while True:
            m = json.loads(self._recv_frame())
            if m.get("id") == want:
                return m
            self.events.append(m)

    def pump(self, seconds):
        self.s.settimeout(0.3)
        end = time.time() + seconds
        while time.time() < end:
            try:
                self.events.append(json.loads(self._recv_frame()))
            except (socket.timeout, TimeoutError):
                pass
        self.s.settimeout(20)

    def js(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        res = r.get("result", {})
        if "exceptionDetails" in res:
            return "JS-ERROR: " + json.dumps(res["exceptionDetails"].get("exception", {}).get("description", res["exceptionDetails"]))[:300]
        return res.get("result", {}).get("value")


def main():
    prof = tempfile.mkdtemp()
    proc = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={PORT}", "--enable-unsafe-swiftshader",
                             "--use-angle=swiftshader", f"--user-data-dir={prof}", "--window-size=1400,900", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                page = [t for t in targets if t.get("type") == "page"][0]
                break
            except Exception:
                time.sleep(0.5)
        ws = WS(page["webSocketDebuggerUrl"])
        for d in ("Runtime", "Page", "Log"):
            ws.call(d + ".enable")
        problems = []
        results = []

        def check(name, ok, detail=""):
            results.append((name, bool(ok), detail))

        def load(path):
            ws.events.clear()
            ws.call("Page.navigate", url=BASE + path)
            ws.pump(4.5)

        def errors():
            out = []
            for e in ws.events:
                if e.get("method") == "Runtime.exceptionThrown":
                    out.append(e["params"]["exceptionDetails"].get("text", "") + " " +
                               json.dumps(e["params"]["exceptionDetails"].get("exception", {}).get("description", ""))[:200])
                if e.get("method") == "Runtime.consoleAPICalled" and e["params"]["type"] == "error":
                    out.append("console.error " + json.dumps([a.get("value") for a in e["params"]["args"]])[:200])
                if e.get("method") == "Log.entryAdded" and e["params"]["entry"]["level"] == "error":
                    out.append("log " + e["params"]["entry"]["text"][:200] + " " + e["params"]["entry"].get("url", ""))
            return out

        # ---------------- Plant 3D ----------------
        load("/")
        check("plant: no page errors", not errors(), "; ".join(errors()))
        check("plant: 5 stations listed", ws.js("document.querySelectorAll('#stations .st').length") == 5)
        ws.js("document.querySelectorAll('#stations .st')[1].click()")
        ws.pump(0.5)
        sel = ws.js("document.getElementById('sel').textContent")
        check("plant: clicking a station list item selects it", "Camera station" in (sel or ""), sel)
        ws.js("document.querySelector('#views button[data-v=gantry]').click()")
        ws.pump(1.5)
        check("plant: view buttons do not throw", not errors(), "; ".join(errors()))
        ws.js("window.confirm = () => true")
        ws.js("document.getElementById('stop').click()")
        ws.pump(2.5)
        msg = ws.js("document.getElementById('cmdmsg').textContent")
        check("plant: Stop line sends the line command", (msg or "").startswith("sent: line"), msg)
        state = ws.js("document.getElementById('state').textContent")
        check("plant: line state becomes line_stopped", state == "line_stopped", state)
        belt = ws.js("document.getElementById('belttxt').textContent")
        check("plant: belt reads 0 % while stopped", "Belt 0 %" in (belt or ""), belt)
        ws.js("document.getElementById('start').click()")
        ws.pump(2.5)
        state = ws.js("document.getElementById('state').textContent")
        belt = ws.js("document.getElementById('belttxt').textContent")
        check("plant: Start line leaves the stopped state", state != "line_stopped", state)
        check("plant: belt is restored to its previous speed (60 %)", "Belt 60 %" in (belt or ""), belt)
        # --- gantry feed gate ---
        ws.js("document.getElementById('feedPause').click()")
        ws.pump(2.5)
        txt = ws.js("document.getElementById('belttxt').textContent")
        check("plant: Pause feed is shown on the status line", "GANTRY FEED PAUSED" in (txt or ""), txt)
        gdetail = ws.js("[...document.querySelectorAll('#stations .st')].find(e => e.textContent.includes('Pick & place')).textContent")
        check("plant: the gantry station says its feed is paused", "feed paused" in (gdetail or ""), gdetail)
        ws.js("document.getElementById('feedResume').click()")
        ws.pump(2.5)
        txt = ws.js("document.getElementById('belttxt').textContent")
        check("plant: Resume feed clears it", "FEED PAUSED" not in (txt or ""), txt)
        ws.js("document.getElementById('stop').click()")
        ws.pump(2.5)
        txt = ws.js("document.getElementById('belttxt').textContent")
        check("plant: Stop line also pauses the feed", "GANTRY FEED PAUSED" in (txt or ""), txt)
        ws.js("document.getElementById('start').click()")
        ws.pump(2.5)
        txt = ws.js("document.getElementById('belttxt').textContent")
        check("plant: Start line resumes the feed", "FEED PAUSED" not in (txt or ""), txt)
        ws.js("document.getElementById('holdOn').click()")
        ws.pump(2.0)
        state = ws.js("document.getElementById('state').textContent")
        check("plant: Hold all turns the line state to hold_all", state == "hold_all", state)
        ws.js("document.getElementById('holdOff').click()")
        ws.pump(1.5)
        check("plant: no errors after controls", not errors(), "; ".join(errors()))

        # ---------------- gantry / tile sync (sampled over a real run) ----------------
        ws.js("document.querySelector('#views button[data-v=gantry]').click()")
        worst, carried_samples, phases, cycles = 0.0, 0, set(), set()
        prev, fastest, belt_samples = {}, 0.0, 0                # belt-speed check: no tile may "zoom"
        for _ in range(170):                                   # ~34 s: enough for several demo tiles
            ws.pump(0.2)
            tnow = time.time()
            xs = ws.js("""(() => { const m = window.__master; if (!m) return {};
              const o = {}; for (const [id, t] of m.tiles) if (t.phase === 'belt' || t.phase === 'waiting') o[id] = t.mesh.position.x;
              return o; })()""") or {}
            for tid, x in xs.items():
                if tid in prev:
                    dt = tnow - prev[tid][1]
                    if dt > 0.05:
                        fastest = max(fastest, abs(x - prev[tid][0]) / dt)
                        belt_samples += 1
                prev[tid] = (x, tnow)
            r = ws.js("""(() => { const m = window.__master; if (!m) return null;
              const G = m.GANTRY, g = m.gantry, out = {st: m.seq.st, carried: null, placed: 0};
              for (const [id, o] of m.tiles) { if (o.phase === 'carried') {
                  out.carried = Math.hypot(o.mesh.position.x - (G.cx + g.x + G.colX), o.mesh.position.z - g.z,
                                           o.mesh.position.y - (g.cup - 0.26)); out.id = id; }
                if (o.phase === 'placed') out.placed++; }
              return out; })()""")
            if not r:
                continue
            phases.add(r["st"])
            if r["carried"] is not None:
                carried_samples += 1
                worst = max(worst, r["carried"])
            if r.get("id"):
                cycles.add(r["id"])
        check("gantry: sequencer ran through the full pick-and-place cycle",
              {"lower1", "grab", "raise1", "toBin", "lower2", "release", "raise2"} <= phases, sorted(phases))
        check("gantry: a tile was carried and placed", len(cycles) >= 1 and carried_samples > 3, f"{len(cycles)} tiles, {carried_samples} samples")
        check("gantry: a carried tile never strays from the cup (max offset < 0.05)", worst < 0.05, f"worst {worst:.3f}")
        check("belt: tiles move at belt speed - none zooms (fastest < 3.6 units/s)",
              belt_samples > 20 and fastest < 3.6, f"fastest {fastest:.2f} u/s over {belt_samples} samples")
        check("gantry: no errors during the run", not errors(), "; ".join(errors()))

        # ---------------- Analysis ----------------
        load("/analysis")
        check("analysis: no page errors", not errors(), "; ".join(errors()))
        n = ws.js("document.querySelectorAll('#kpis .kpi').length")
        check("analysis: KPI cards render", n == 6, n)
        ws.js("[...document.querySelectorAll('#windows button')].find(b => b.textContent === '5 min').click()")
        ws.pump(1.5)
        on = ws.js("document.querySelector('#windows button.on').textContent")
        check("analysis: window button switches", on == "5 min", on)
        ws.js("document.getElementById('tbl').click()")
        vis = ws.js("getComputedStyle(document.getElementById('tablewrap')).display + '/' + getComputedStyle(document.getElementById('chart')).display")
        check("analysis: table view toggles", vis == "block/none", vis)
        rows = ws.js("document.querySelectorAll('#tablewrap tr').length")
        check("analysis: table has a header + 30 buckets", rows == 31, rows)
        check("analysis: no errors after interaction", not errors(), "; ".join(errors()))

        # ---------------- Troubleshoot ----------------
        load("/troubleshoot")
        check("troubleshoot: no page errors", not errors(), "; ".join(errors()))
        lanes = ws.js("document.querySelectorAll('#tl rect').length")
        check("troubleshoot: timeline drawn", (lanes or 0) > 6, lanes)
        ws.js("[...document.querySelectorAll('#windows button')].find(b => b.textContent === '6 h').click()")
        ws.pump(1.5)
        check("troubleshoot: window switch works", ws.js("document.querySelector('#windows button.on').textContent") == "6 h")
        check("troubleshoot: findings area populated", (ws.js("document.getElementById('findings').textContent.length") or 0) > 10)

        # ---------------- Insights ----------------
        load("/insights")
        check("insights: no page errors", not errors(), "; ".join(errors()))
        ws.js("[...document.querySelectorAll('#windows button')].find(b => b.textContent === '15 min').click()")
        ws.pump(1.5)
        check("insights: window switch works", ws.js("document.querySelector('#windows button.on').textContent") == "15 min")
        dis = ws.js("document.getElementById('explain').disabled")
        check("insights: Explain disabled with no provider", dis is True, dis)
        check("insights: no errors after interaction", not errors(), "; ".join(errors()))

        bad = 0
        for name, ok, detail in results:
            print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   <- {detail}"))
            bad += 0 if ok else 1
        print(f"\n{len(results) - bad}/{len(results)} passed")
        return bad
    finally:
        proc.kill()


sys.exit(main())
