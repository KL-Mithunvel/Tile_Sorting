"""Control panel: config validation, remote-status parsing, the status view and the start/stop API.

The SSH layer is not exercised here (it needs a real machine); everything it feeds is."""

from __future__ import annotations

import pytest

from master.app import create_app
from master.bus import InMemoryBus
from master.collector import MasterCollector
from master.nodes import (ConfigError, NodeManager, SimulatedBackend, action_command, load_nodes,
                          parse_status, service_view, status_command, sudoers_rule)

CFG = {"list": [
    {"id": "pi", "host": "10.0.0.1", "user": "klm", "services": [
        {"id": "broker", "unit": "mosquitto", "confirm": True, "stop_all": False},
        {"id": "station", "unit": "tile-acoustic", "station": "pi"},
        {"id": "camera", "unit": "tile-camera", "station": "camera"}]},
    {"id": "unoq", "host": "10.0.0.2", "user": "arduino", "services": [
        {"id": "gantry", "kind": "arduino_app", "app": "user:pick_place", "station": "gantry"}]},
    {"id": "nano", "ssh": False, "station": "conveyor"},
]}


def nodes():
    return load_nodes(CFG)


# ---- config ------------------------------------------------------------------

def test_load_nodes_normalises():
    n = nodes()
    assert [x["id"] for x in n] == ["pi", "unoq", "nano"]
    assert n[0]["services"][0]["confirm"] is True and n[0]["services"][0]["stop_all"] is False
    assert n[2]["ssh"] is False and n[2]["services"] == []


@pytest.mark.parametrize("bad", [
    {"id": "all", "host": "h", "user": "u"},
    {"id": "x y", "host": "h", "user": "u"},
    {"id": "a", "host": "h; rm -rf /", "user": "u"},
    {"id": "a", "host": "h", "user": "u;x"},
    {"id": "a", "host": "h", "user": "u", "services": [{"id": "s", "unit": "foo; reboot"}]},
    {"id": "a", "host": "h", "user": "u", "services": [{"id": "s", "unit": "foo $(id)"}]},
    {"id": "a", "host": "h", "user": "u", "services": [{"id": "s", "kind": "shell", "unit": "foo"}]},
    {"id": "a", "ssh": False, "services": [{"id": "s", "unit": "foo"}]},
])
def test_unsafe_or_invalid_config_refuses_to_load(bad):
    with pytest.raises(ConfigError):
        load_nodes({"list": [bad]})


def test_duplicate_ids_refused():
    with pytest.raises(ConfigError):
        load_nodes({"list": [{"id": "a", "host": "h", "user": "u"}, {"id": "a", "host": "h", "user": "u"}]})
    with pytest.raises(ConfigError):
        load_nodes({"list": [{"id": "a", "host": "h", "user": "u", "services": [
            {"id": "s", "unit": "x"}, {"id": "s", "unit": "y"}]}]})


def test_sudoers_rule_names_only_the_listed_units_and_actions():
    rule = sudoers_rule("klm", nodes()[0]["services"])
    assert rule.startswith("klm ALL=(root) NOPASSWD: ")
    assert "/usr/bin/systemctl restart tile-camera" in rule
    assert "systemctl *" not in rule and "ALL=(ALL)" not in rule
    assert rule.count("mosquitto") == 3 and "status" not in rule


# ---- remote commands -----------------------------------------------------------

def test_status_command_lists_units_and_apps():
    cmd = status_command(nodes()[0]["services"])
    assert "mosquitto tile-acoustic tile-camera" in cmd and "arduino-app-cli" not in cmd
    assert "arduino-app-cli app list" in status_command(nodes()[1]["services"])
    assert status_command([]) == "true"


def test_action_command_is_fixed_shape():
    s = nodes()[0]["services"][2]
    assert action_command(s, "restart") == "sudo -n /usr/bin/systemctl restart tile-camera"
    assert action_command(nodes()[1]["services"][0], "start") == "arduino-app-cli app start user:pick_place"
    with pytest.raises(ValueError):
        action_command(s, "disable")


def test_parse_status_systemd_and_missing_unit():
    out = ("UNIT|mosquitto|active|Thu 2026-10-15 16:00:00 IST|loaded\n"
           "UNIT|tile-acoustic|inactive||loaded\n"
           "UNIT|tile-camera|inactive||not-found\n")
    st = parse_status(out, nodes()[0]["services"])
    assert st["broker"]["state"] == "active" and st["broker"]["since"].startswith("Thu")
    assert st["station"]["state"] == "inactive"
    assert st["camera"]["state"] == "missing"      # is-active says "inactive" for a unit that does not exist


def test_parse_status_arduino_apps():
    out = ("APP|ID                NAME   ICON STATUS  EXAMPLE\n"
           "APP|user:cnc         CNC    😀 stopped false\n"
           "APP|user:pick_place  Pick and place   📟 running false\n")
    svc = nodes()[1]["services"]
    assert parse_status(out, svc)["gantry"]["state"] == "active"
    assert parse_status("APP|user:cnc CNC x stopped false\n", svc)["gantry"]["state"] == "missing"


# ---- the view -------------------------------------------------------------------

def svc(station="camera"):
    return {"id": "c", "label": "Camera", "kind": "systemd", "target": "tile-camera", "station": station,
            "confirm": False}


@pytest.mark.parametrize("state,health,status,tone", [
    ("active", {"camera": {"level": "ok", "detail": "fine"}}, "online", "ok"),
    ("active", {"camera": {"level": "warn", "detail": "replay"}}, "running, reports a problem", "warn"),
    ("active", {"camera": {"level": "fail", "detail": "silent"}}, "running, but not reporting", "fail"),
    ("active", {"camera": {"level": "offline", "detail": "no heartbeat"}}, "running, but not reporting", "fail"),
    ("active", {}, "running", "ok"),
    ("inactive", {"camera": {"level": "ok", "detail": "x"}}, "stopped", "offline"),
    ("failed", {}, "crashed (failed)", "fail"),
    ("missing", {}, "not installed on the node", "offline"),
    ("activating", {}, "starting…", "warn"),
])
def test_service_status_combines_process_and_line_views(state, health, status, tone):
    v = service_view(svc(), {"state": state}, True, health)
    assert (v["status"], v["tone"]) == (status, tone)


def test_unreachable_node_marks_service_unreachable():
    v = service_view(svc(), None, False, {"camera": {"level": "ok", "detail": ""}})
    assert v["status"] == "node unreachable" and v["tone"] == "fail"


# ---- the manager ------------------------------------------------------------------

def manager(initial=None, health=None):
    be = SimulatedBackend(initial)
    return NodeManager(nodes(), be, lambda: health or {}), be


def test_view_before_first_poll_says_checking():
    m, _ = manager()
    v = m.view()
    assert v["nodes"][0]["status"] == "checking…" and v["nodes"][2]["status"] == "not reporting"


def test_poll_and_view_with_line_health():
    m, _ = manager({("pi", "broker"): "active", ("pi", "station"): "active", ("pi", "camera"): "active",
                    ("unoq", "gantry"): "inactive"},
                   {"pi": {"level": "ok", "detail": "x"}, "camera": {"level": "fail", "detail": "silent"},
                    "conveyor": {"level": "ok", "detail": "nano up"}})
    m.poll_once()
    v = {n["id"]: n for n in m.view()["nodes"]}
    by = {s["id"]: s for s in v["pi"]["services"]}
    assert by["station"]["status"] == "online" and by["camera"]["status"] == "running, but not reporting"
    assert v["pi"]["tone"] == "fail" and v["unoq"]["services"][0]["status"] == "stopped"
    assert v["nano"]["status"] == "online" and v["nano"]["reachable"] is None
    assert m.view()["summary"]["services"] == 4


def test_unreachable_node_reported_with_error():
    m, be = manager()
    be.unreachable.add("unoq")
    m.poll_once()
    n = {x["id"]: x for x in m.view()["nodes"]}["unoq"]
    assert n["status"] == "unreachable over SSH" and "no route" in n["error"]


def test_run_starts_and_records_result():
    m, be = manager()
    m.run("pi", "camera", "start", wait=True)
    assert be.calls == [("pi", "camera", "start")]
    sv = {s["id"]: s for s in {n["id"]: n for n in m.view()["nodes"]}["pi"]["services"]}["camera"]
    assert sv["state"] == "active" and sv["last_action"]["ok"] is True


def test_dangerous_service_needs_confirm_to_stop_but_not_to_start():
    m, be = manager()
    m.run("pi", "broker", "start", wait=True)
    with pytest.raises(PermissionError):
        m.run("pi", "broker", "stop")
    with pytest.raises(PermissionError):
        m.run("pi", "broker", "restart")
    m.run("pi", "broker", "stop", confirm=True, wait=True)
    assert be.calls[-1] == ("pi", "broker", "stop")


def test_unknown_things_and_bad_actions_refused():
    m, be = manager()
    for args in (("nope", "camera", "start"), ("pi", "nope", "start")):
        with pytest.raises(KeyError):
            m.run(*args)
    with pytest.raises(ValueError):
        m.run("pi", "camera", "rm -rf /")
    assert be.calls == []


def test_busy_service_refuses_a_second_action():
    m, _ = manager()
    m._busy.add(("pi", "camera"))
    with pytest.raises(RuntimeError):
        m.run("pi", "camera", "start")


def test_start_all_in_listed_order_stop_all_reversed_and_keeps_broker():
    m, be = manager()
    m.run_all("start", wait=True)
    assert [c[1] for c in be.calls] == ["broker", "station", "camera", "gantry"]
    be.calls.clear()
    m.run_all("stop", wait=True)
    assert [c[1] for c in be.calls] == ["gantry", "camera", "station"]       # broker has stop_all: false
    with pytest.raises(ValueError):
        m.run_all("restart")


# ---- the HTTP API ------------------------------------------------------------------

def client(controls=True, with_nodes=True):
    m = NodeManager(nodes(), SimulatedBackend(), lambda: {}) if with_nodes else None
    app = create_app(MasterCollector(), InMemoryBus(), controls_enabled=controls, nodes=m)
    return app.test_client(), m


def post(c, url, body=None):
    return c.post(url, json=body if body is not None else {})


def test_page_and_status_endpoint():
    c, m = client()
    assert c.get("/nodes").status_code == 200 and b"Control panel" in c.get("/nodes").data
    m.poll_once()
    d = c.get("/api/nodes").get_json()
    assert d["configured"] is True and d["controls"] is True and len(d["nodes"]) == 3
    assert c.get("/api/nodes").get_json()["summary"]["services"] == 4


def test_status_endpoint_when_not_configured():
    c, _ = client(with_nodes=False)
    d = c.get("/api/nodes").get_json()
    assert d["configured"] is False and d["nodes"] == []
    assert post(c, "/api/nodes/pi/camera/start").status_code == 404


def test_controls_disabled_refuses_every_action():
    c, m = client(controls=False)
    assert post(c, "/api/nodes/pi/camera/start").status_code == 403
    assert post(c, "/api/nodes/all/start").status_code == 403
    assert m.backend.calls == []


def test_actions_require_json():
    c, m = client()
    assert c.post("/api/nodes/pi/camera/start", data="x", content_type="text/plain").status_code == 415
    assert m.backend.calls == []


def test_start_stop_restart_through_the_api():
    c, m = client()
    r = post(c, "/api/nodes/pi/camera/start")
    assert r.status_code == 200 and r.get_json()["started"]["action"] == "start"
    import time
    for _ in range(50):
        if m.backend.calls:
            break
        time.sleep(0.02)
    assert m.backend.calls[0] == ("pi", "camera", "start")


def test_api_errors():
    c, _ = client()
    assert post(c, "/api/nodes/zzz/camera/start").status_code == 404
    assert post(c, "/api/nodes/pi/zzz/start").status_code == 404
    assert post(c, "/api/nodes/pi/camera/format").status_code == 400
    assert post(c, "/api/nodes/pi/broker/stop").status_code == 400          # needs confirm
    assert post(c, "/api/nodes/pi/broker/stop", {"confirm": True}).status_code == 200
    assert post(c, "/api/nodes/all/restart").status_code == 400
    assert post(c, "/api/nodes/all/stop").status_code == 200


# ---- the "Open GUIs" menu -------------------------------------------------------------

def test_guis_endpoint_lists_only_http_links():
    guis = [{"label": "Camera", "url": "http://10.0.0.1:5000/", "note": "feed"},
            {"label": "Bad", "url": "javascript:alert(1)"},
            {"label": "File", "url": "file:///etc/passwd"},
            {"url": "https://10.0.0.1/"}]
    c = create_app(MasterCollector(), InMemoryBus(), guis=guis).test_client()
    got = c.get("/api/guis").get_json()["guis"]
    assert [g["url"] for g in got] == ["http://10.0.0.1:5000/", "https://10.0.0.1/"]
    assert create_app(MasterCollector(), InMemoryBus()).test_client().get("/api/guis").get_json()["guis"] == []


@pytest.mark.parametrize("page", ["/", "/analysis", "/troubleshoot", "/nodes", "/tiles", "/insights"])
def test_every_master_page_has_the_gui_menu(page):
    c = create_app(MasterCollector(), InMemoryBus()).test_client()
    r = c.get(page)
    assert r.status_code == 200 and b"Open GUIs" in r.data and b"/api/guis" in r.data
