"""Control panel back end: is each node's program running, and start / stop / restart it.

Two independent views of a node are joined here, because either alone misleads:

  * the process view  - over SSH: is the systemd unit / Arduino app active? (this module)
  * the line view     - over MQTT: is the program actually reporting to the station and is
                        it healthy? (the station health the collector already builds)

"Active" with silence on the bus means "running but not reporting" - a different fault from
"stopped", and the panel says which. Starting a node does NOT bypass the line's own safety:
the station still decides tiles only when its checks pass, and nothing here is an e-stop.

Safety shape (the page is unauthenticated, so this is the first line of defence):
  * what can be run is a fixed allow-list from config.yaml; a request names a node id, a
    service id and one of start/stop/restart - never a command or unit name;
  * unit / app names are validated against a strict pattern when the config is loaded, because
    they end up in a remote shell command;
  * no passwords: SSH uses the master PC's key (BatchMode), systemctl on the Pi runs through a
    narrow sudoers rule (see `sudoers_rule()`), the Arduino app CLI needs no sudo.

The SSH layer is a thin `Backend`; the logic is pure and tested with a fake backend.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from typing import Callable, Optional

ACTIONS = ("start", "stop", "restart")
_NAME = re.compile(r"^[A-Za-z0-9_.@:-]+$")           # unit / app ids, node ids, hosts
_HOST = re.compile(r"^[A-Za-z0-9_.-]+$")
KINDS = ("systemd", "arduino_app")
LEVEL_ORDER = {"fail": 0, "offline": 1, "warn": 2, "ok": 3}


class ConfigError(ValueError):
    pass


# --------------------------------------------------------------------------- config

def load_nodes(cfg: Optional[dict]) -> list[dict]:
    """Validate the `nodes:` block of config.yaml and return normalised node dicts.

    A bad config refuses to load instead of producing a panel that quietly cannot control
    what it shows."""
    out, seen = [], set()
    for raw in (cfg or {}).get("list") or []:
        nid = str(raw.get("id", ""))
        if not _NAME.match(nid) or nid in seen or nid == "all":
            raise ConfigError(f"node id {nid!r} must be unique, simple and not 'all'")
        seen.add(nid)
        ssh = bool(raw.get("ssh", True))
        host = str(raw.get("host") or "")
        if ssh and not _HOST.match(host):
            raise ConfigError(f"node {nid}: host {host!r} is not a plain hostname / address")
        user = str(raw.get("user") or "")
        if ssh and not _NAME.match(user):
            raise ConfigError(f"node {nid}: user {user!r} is not valid")
        services, sids = [], set()
        for s in raw.get("services") or []:
            sid = str(s.get("id", ""))
            kind = s.get("kind", "systemd")
            target = str(s.get("unit") if kind == "systemd" else s.get("app") or "")
            if not _NAME.match(sid) or sid in sids:
                raise ConfigError(f"node {nid}: service id {sid!r} must be unique and simple")
            if kind not in KINDS:
                raise ConfigError(f"node {nid}/{sid}: kind must be one of {KINDS}")
            if not _NAME.match(target):
                raise ConfigError(f"node {nid}/{sid}: unit/app {target!r} is not valid")
            if not ssh:
                raise ConfigError(f"node {nid}: services need ssh: true")
            sids.add(sid)
            services.append({
                "id": sid, "label": str(s.get("label") or sid), "kind": kind, "target": target,
                "station": s.get("station"),                 # key in the station health, or None
                "gui": s.get("gui"),                         # a link to that program's own page
                "confirm": bool(s.get("confirm", False)),    # dangerous to stop/restart: needs confirm
                "start_all": bool(s.get("start_all", True)),
                "stop_all": bool(s.get("stop_all", True)),
            })
        out.append({
            "id": nid, "label": str(raw.get("label") or nid), "ssh": ssh, "host": host, "user": user,
            "station": raw.get("station"),                   # health key when the node has no services
            "note": raw.get("note"), "services": services,
        })
    return out


def sudoers_rule(user: str, services: list[dict]) -> str:
    """The exact /etc/sudoers.d line that lets the panel manage the systemd units and nothing else."""
    units = [s["target"] for s in services if s["kind"] == "systemd"]
    cmds = [f"/usr/bin/systemctl {a} {u}" for u in units for a in ACTIONS]
    return f"{user} ALL=(root) NOPASSWD: " + ", ".join(cmds)


# --------------------------------------------------------------------------- remote commands

def status_command(services: list[dict]) -> str:
    """One remote command that reports every managed service of a node (names are pre-validated)."""
    parts = []
    units = [s["target"] for s in services if s["kind"] == "systemd"]
    if units:
        parts.append("for u in " + " ".join(units) + "; do printf 'UNIT|%s|%s|%s|%s\\n' \"$u\" "
                     "\"$(systemctl is-active $u 2>&1)\" \"$(systemctl show -p ActiveEnterTimestamp --value $u 2>&1)\" "
                     "\"$(systemctl show -p LoadState --value $u 2>&1)\"; done")
    if any(s["kind"] == "arduino_app" for s in services):
        parts.append("arduino-app-cli app list 2>&1 | sed 's/^/APP|/'")
    return "; ".join(parts) or "true"


def parse_status(output: str, services: list[dict]) -> dict[str, dict]:
    """service id -> {state, since}. state: active | inactive | failed | missing | unknown."""
    units: dict[str, tuple[str, str, str]] = {}
    apps: dict[str, str] = {}
    for line in output.splitlines():
        if line.startswith("UNIT|"):
            bits = line.split("|", 4)
            if len(bits) == 5:
                units[bits[1]] = (bits[2].strip(), bits[3].strip(), bits[4].strip())
        elif line.startswith("APP|"):
            tokens = line[4:].split()
            # "<id> <name...> <icon> <status> <example>": status is the second-to-last token.
            if len(tokens) >= 3 and tokens[0] != "ID":
                apps[tokens[0]] = tokens[-2]
    result = {}
    for s in services:
        if s["kind"] == "systemd":
            state, since, load = units.get(s["target"], ("unknown", "", ""))
            if load == "not-found":                        # is-active says "inactive" for a unit that does not exist
                state = "missing"
            elif state not in ("active", "inactive", "failed", "activating", "deactivating"):
                state = "unknown"
            result[s["id"]] = {"state": state, "since": since or None}
        else:
            raw = apps.get(s["target"])
            if raw is None:
                result[s["id"]] = {"state": "missing", "since": None}
            else:
                result[s["id"]] = {"state": "active" if raw in ("running", "started", "starting") else "inactive",
                                   "since": None, "raw": raw}
    return result


def action_command(service: dict, action: str) -> str:
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}")
    if service["kind"] == "systemd":
        return f"sudo -n /usr/bin/systemctl {action} {service['target']}"
    return f"arduino-app-cli app {action} {service['target']}"


# --------------------------------------------------------------------------- backends

class SshBackend:
    """Runs the fixed commands over the system `ssh` with the PC's key. Never prompts."""

    def __init__(self, timeout_s: float = 6.0, action_timeout_s: float = 150.0, identity_file: Optional[str] = None):
        self.timeout_s, self.action_timeout_s, self.identity_file = timeout_s, action_timeout_s, identity_file
        self._ssh = shutil.which("ssh")

    def _run(self, node: dict, command: str, timeout: float) -> tuple[int, str]:
        if not self._ssh:
            return 255, "ssh is not installed on this PC"
        argv = [self._ssh, "-o", "BatchMode=yes", "-o", "NumberOfPasswordPrompts=0",
                "-o", f"ConnectTimeout={max(2, int(self.timeout_s))}", "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ServerAliveInterval=5"]
        if self.identity_file:
            argv += ["-i", self.identity_file]
        argv += [f"{node['user']}@{node['host']}", command]
        try:
            # utf-8 explicitly: arduino-app-cli prints emoji, and Windows would decode as cp1252 and crash.
            p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return 124, "timed out"
        except OSError as exc:
            return 255, str(exc)
        return p.returncode, (p.stdout + ("\n" + p.stderr if p.stderr.strip() else "")).strip()

    def probe(self, node: dict) -> dict:
        rc, out = self._run(node, status_command(node["services"]), self.timeout_s + 4)
        if rc in (255, 124) or (rc != 0 and "UNIT|" not in out and "APP|" not in out and node["services"]):
            return {"reachable": False, "error": _short(out) or f"ssh exit {rc}", "services": {}}
        return {"reachable": True, "error": None, "services": parse_status(out, node["services"])}

    def act(self, node: dict, service: dict, action: str) -> tuple[bool, str]:
        rc, out = self._run(node, action_command(service, action), self.action_timeout_s)
        if rc != 0 and "password is required" in out:
            out = ("sudo wants a password on the node: install the sudoers rule "
                   "(master/nodes.py sudoers_rule) - " + _short(out))
        return rc == 0, _short(out)


class SimulatedBackend:
    """In-memory services for --demo / --sim and for tests. Same interface as SshBackend."""

    def __init__(self, initial: Optional[dict] = None):
        self.state: dict[tuple[str, str], str] = dict(initial or {})
        self.calls: list[tuple[str, str, str]] = []
        self.unreachable: set[str] = set()

    def probe(self, node: dict) -> dict:
        if node["id"] in self.unreachable:
            return {"reachable": False, "error": "no route to host (simulated)", "services": {}}
        return {"reachable": True, "error": None, "services": {
            s["id"]: {"state": self.state.get((node["id"], s["id"]), "inactive"), "since": None}
            for s in node["services"]}}

    def act(self, node: dict, service: dict, action: str) -> tuple[bool, str]:
        self.calls.append((node["id"], service["id"], action))
        if node["id"] in self.unreachable:
            return False, "no route to host (simulated)"
        self.state[(node["id"], service["id"])] = "inactive" if action == "stop" else "active"
        return True, ""


def _short(text: str, n: int = 240) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


# --------------------------------------------------------------------------- the view (pure)

def service_view(service: dict, remote: Optional[dict], reachable: bool, health: dict) -> dict:
    """One service: its process state, what the line says about it, and one plain-language status."""
    state = (remote or {}).get("state", "unknown") if reachable else "unreachable"
    h = health.get(service.get("station")) if service.get("station") else None
    level, detail = (h or {}).get("level"), (h or {}).get("detail")
    if state == "unreachable":
        status, tone = "node unreachable", "fail"
    elif state == "missing":
        status, tone = "not installed on the node", "offline"
    elif state == "failed":
        status, tone = "crashed (failed)", "fail"
    elif state in ("inactive", "deactivating"):
        status, tone = "stopped", "offline"
    elif state == "activating":
        status, tone = "starting…", "warn"
    elif state == "active":
        if h is None:
            status, tone = "running", "ok"                    # nothing on the bus to compare with
        elif level == "ok":
            status, tone = "online", "ok"
        elif level == "warn":
            status, tone = "running, reports a problem", "warn"
        else:
            status, tone = "running, but not reporting", "fail"
    else:
        status, tone = "unknown", "offline"
    return {"id": service["id"], "label": service["label"], "kind": service["kind"], "target": service["target"],
            "gui": service.get("gui"), "confirm": service["confirm"], "state": state,
            "since": (remote or {}).get("since"), "status": status, "tone": tone,
            "line_level": level, "line_detail": detail}


def node_view(node: dict, probe: Optional[dict], health: dict, last_actions: dict, busy: set, now: float) -> dict:
    reachable = bool(probe and probe.get("reachable"))
    services = [service_view(s, (probe or {}).get("services", {}).get(s["id"]), reachable, health)
                for s in node["services"]]
    for sv in services:
        sv["last_action"] = last_actions.get((node["id"], sv["id"]))
        sv["busy"] = (node["id"], sv["id"]) in busy
    if node["ssh"]:
        if probe is None:
            tone, status = "offline", "checking…"
        elif not reachable:
            tone, status = "fail", "unreachable over SSH"
        else:
            tone = min((sv["tone"] for sv in services), key=lambda t: LEVEL_ORDER.get(t, 1), default="ok")
            status = {"ok": "all online", "warn": "needs attention", "fail": "problem", "offline": "something stopped"}[tone]
    else:                                                     # monitored over MQTT only (e.g. the Nano)
        h = health.get(node.get("station"))
        tone = (h or {}).get("level") or "offline"
        status = {"ok": "online", "warn": "reports a problem", "fail": "not reporting", "offline": "not reporting"}.get(tone, "unknown")
    return {"id": node["id"], "label": node["label"], "host": node["host"] if node["ssh"] else None, "ssh": node["ssh"],
            "note": node.get("note"), "reachable": reachable if node["ssh"] else None,
            "error": (probe or {}).get("error"), "status": status, "tone": tone,
            "line_detail": (health.get(node.get("station")) or {}).get("detail") if node.get("station") else None,
            "services": services, "checked_at": (probe or {}).get("at")}


# --------------------------------------------------------------------------- the manager

class NodeManager:
    """Polls every node in the background and runs start/stop/restart without blocking the page."""

    def __init__(self, nodes: list[dict], backend, health: Callable[[], dict],
                 poll_interval_s: float = 5.0, clock: Callable[[], float] = time.time):
        self.nodes = {n["id"]: n for n in nodes}
        self.order = [n["id"] for n in nodes]
        self.backend, self._health, self.poll_interval_s, self._clock = backend, health, poll_interval_s, clock
        self._probes: dict[str, dict] = {}
        self._last: dict[tuple[str, str], dict] = {}
        self._busy: set[tuple[str, str]] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- polling
    def poll_once(self) -> None:
        def one(n):
            try:
                p = self.backend.probe(n)
            except Exception as exc:  # noqa: BLE001 - one node must not stop the others being checked
                p = {"reachable": False, "error": f"check failed: {exc}", "services": {}}
            p["at"] = self._clock()
            with self._lock:
                self._probes[n["id"]] = p
        threads = [threading.Thread(target=one, args=(n,), daemon=True) for n in self.nodes.values() if n["ssh"]]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

    def start(self) -> None:
        if self._thread:
            return

        def loop():
            while not self._stop.is_set():
                self.poll_once()
                self._stop.wait(self.poll_interval_s)
        self._thread = threading.Thread(target=loop, daemon=True, name="node-poller")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # -- view
    def view(self) -> dict:
        try:
            health = self._health() or {}
        except Exception:  # noqa: BLE001
            health = {}
        now = self._clock()
        with self._lock:
            probes, last, busy = dict(self._probes), dict(self._last), set(self._busy)
        nodes = [node_view(self.nodes[i], probes.get(i), health, last, busy, now) for i in self.order]
        return {"nodes": nodes, "at": now, "summary": _summary(nodes)}

    # -- actions
    def _lookup(self, node_id: str, service_id: str) -> tuple[dict, dict]:
        node = self.nodes.get(node_id)
        if node is None:
            raise KeyError(f"no node {node_id!r}")
        for s in node["services"]:
            if s["id"] == service_id:
                return node, s
        raise KeyError(f"node {node_id!r} has no service {service_id!r}")

    def run(self, node_id: str, service_id: str, action: str, confirm: bool = False, wait: bool = False) -> dict:
        """Start/stop/restart one service. Returns at once (the action runs in the background)
        unless `wait`. Raises KeyError (unknown), ValueError (bad action), PermissionError (needs confirm),
        RuntimeError (already running something there)."""
        if action not in ACTIONS:
            raise ValueError(f"action must be one of {ACTIONS}")
        node, svc = self._lookup(node_id, service_id)
        if svc["confirm"] and action in ("stop", "restart") and not confirm:
            raise PermissionError(f'{svc["label"]} carries the whole line: {action} needs "confirm": true')
        key = (node_id, service_id)
        with self._lock:
            if key in self._busy:
                raise RuntimeError(f'{svc["label"]} is already busy')
            self._busy.add(key)

        def job():
            try:
                ok, msg = self.backend.act(node, svc, action)
            except Exception as exc:  # noqa: BLE001
                ok, msg = False, f"failed: {exc}"
            with self._lock:
                self._last[key] = {"action": action, "ok": ok, "message": msg, "at": self._clock()}
                self._busy.discard(key)
            try:                                              # show the result without waiting for the next poll
                p = self.backend.probe(node)
                p["at"] = self._clock()
                with self._lock:
                    self._probes[node_id] = p
            except Exception:  # noqa: BLE001
                pass
        if wait:
            job()
        else:
            threading.Thread(target=job, daemon=True, name=f"node-{node_id}-{service_id}").start()
        return {"node": node_id, "service": service_id, "action": action}

    def run_all(self, action: str, wait: bool = False) -> list[dict]:
        """Start every `start_all` service in listed order, or stop every `stop_all` one in reverse
        order (stop the consumers first, the broker last). One background thread so the order holds."""
        if action not in ("start", "stop"):
            raise ValueError("bulk action must be start or stop")
        plan = [(n["id"], s) for n in self.nodes.values() for s in n["services"]]
        plan = [(nid, s) for nid, s in plan if s["start_all" if action == "start" else "stop_all"]]
        if action == "stop":
            plan.reverse()
        if not plan:
            return []

        def seq():
            for nid, s in plan:
                try:
                    self.run(nid, s["id"], action, confirm=True, wait=True)
                except (RuntimeError, KeyError, PermissionError, ValueError):
                    continue
        if wait:
            seq()
        else:
            threading.Thread(target=seq, daemon=True, name="node-bulk").start()
        return [{"node": nid, "service": s["id"], "action": action} for nid, s in plan]


def _summary(nodes: list[dict]) -> dict:
    svcs = [s for n in nodes for s in n["services"]]
    return {"services": len(svcs), "online": sum(1 for s in svcs if s["tone"] == "ok"),
            "problem": sum(1 for s in svcs if s["tone"] in ("fail", "warn")),
            "stopped": sum(1 for s in svcs if s["tone"] == "offline")}
