"""Take full-page screenshots of the running GUIs in headless Edge (CDP, stdlib only).

Used to gather figures for the Review 2 report. Start the GUIs in their demo / simulated modes
first (see documents/deliverables/review_2/screenshots/README.md), then:

    python development/capture_screens.py            # every page in SHOTS
    python development/capture_screens.py master     # only names starting with "master"

Output: documents/deliverables/review_2/screenshots/<name>.png
"""
import base64, json, os, shutil, socket, struct, subprocess, sys, tempfile, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "documents", "deliverables", "review_2", "screenshots")
EDGE = next((p for p in (shutil.which("msedge"), r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                         r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") if p and os.path.exists(p)), None)
PORT = 9344
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



def shots_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("screen_list", os.path.join(ROOT, "development", "screen_list.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m.SHOTS


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    os.makedirs(OUT, exist_ok=True)
    prof = tempfile.mkdtemp()
    proc = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={PORT}", "--enable-unsafe-swiftshader",
                             "--use-angle=swiftshader", f"--user-data-dir={prof}", "--window-size=1500,950", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                page = [t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json")) if t.get("type") == "page"][0]
                break
            except Exception:
                time.sleep(0.5)
        ws = WS(page["webSocketDebuggerUrl"])
        for d in ("Runtime", "Page"):
            ws.call(d + ".enable")
        for shot in shots_module():
            name, url = shot["name"], shot["url"]
            if not name.startswith(only):
                continue
            w, h = shot.get("size", (1500, 950))
            ws.call("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
            ws.call("Page.navigate", url=url)
            ws.pump(shot.get("wait", 5))
            for step in shot.get("steps", []):
                if isinstance(step, (int, float)):
                    ws.pump(step)
                else:
                    r = ws.js(step)
                    if isinstance(r, str) and r.startswith("JS-ERROR"):
                        print("   step failed:", name, r)
            params = {"format": "png"}
            if shot.get("full", True):
                m = ws.call("Page.getLayoutMetrics")["result"]
                cs = m.get("cssContentSize") or m["contentSize"]
                params.update(captureBeyondViewport=True, clip={"x": 0, "y": 0, "width": w, "height": min(cs["height"], shot.get("max_h", 3000)), "scale": 1})
            data = ws.call("Page.captureScreenshot", **params)["result"]["data"]
            with open(os.path.join(OUT, name + ".png"), "wb") as f:
                f.write(base64.b64decode(data))
            print("saved", name)
    finally:
        proc.terminate()
        shutil.rmtree(prof, ignore_errors=True)


if __name__ == "__main__":
    main()
