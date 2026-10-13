"""The master's web page (Flask, :8000): inspect, collect, command.

Read-mostly. The commands (sound vote, hold-all, belt speed, strike inhibit)
change how the LINE behaves, so they sit behind `controls_enabled` (off by
default), are POST + JSON only, and every value is validated here before it is
put on the bus — the station and the Nano validate again, this is not the only
line of defence but it is the first.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from flask import Flask, Response, jsonify, render_template, request

from master.collector import MasterCollector
from master.analysis import DEFAULT_WINDOW_S, WINDOWS_S, build_analysis
from master.insights import detect
from master.llm import LlmError, explain
from master.plant import STATIONS, build_plant, stations_from_snapshot
from master.troubleshoot import diagnose, timeline


def create_app(collector: MasterCollector, bus, controls_enabled: bool = False,
               demo: bool = False, clock: Callable[[], float] = time.time, llm=None,
               demo_label: str = "demo — synthetic line") -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index():
        return render_template("plant.html")

    @app.route("/favicon.ico")
    def favicon():
        return ("", 204)          # no icon; answer quietly instead of a 404 in every page's console

    @app.route("/analysis")
    def analysis_page():
        return render_template("analysis.html", windows=WINDOWS_S, default_window=DEFAULT_WINDOW_S)

    @app.route("/api/analysis")
    def api_analysis():
        try:
            window = int(request.args.get("window", DEFAULT_WINDOW_S))
        except ValueError:
            window = -1
        if window not in WINDOWS_S:
            return jsonify(ok=False, error=f"window must be one of {list(WINDOWS_S)} seconds"), 400
        now = clock()
        since = now - window
        out = build_analysis(collector.analysis_rows(since), collector.alarm_rows(since), now, window)
        out["demo"] = demo
        out["demo_label"] = demo_label
        return jsonify(out)

    @app.route("/troubleshoot")
    def troubleshoot_page():
        return render_template("troubleshoot.html", windows=WINDOWS_S, default_window=DEFAULT_WINDOW_S)

    @app.route("/api/troubleshoot")
    def api_troubleshoot():
        try:
            window = int(request.args.get("window", DEFAULT_WINDOW_S))
        except ValueError:
            window = -1
        if window not in WINDOWS_S:
            return jsonify(ok=False, error=f"window must be one of {list(WINDOWS_S)} seconds"), 400
        now = clock()
        connected = bool(bus.connected)
        collector.tick(now, broker_connected=connected)
        snap = collector.snapshot(now=now, recent=0)
        stations = stations_from_snapshot(snap)
        events = collector.health_events(now - window)
        current = {s["id"]: s["level"] for s in stations}
        if connected is not None:
            current["master"] = "ok" if connected else "fail"
        labels = {sid: label for sid, label, _ in STATIONS}
        labels["master"] = "This master (broker link)"
        return jsonify({
            "now": now, "window_s": window, "demo": demo, "demo_label": demo_label, "broker_connected": connected,
            "labels": labels,
            "findings": diagnose(stations, events, collector.alarm_rows(now - 600), now, master_connected=connected),
            "timeline": timeline(events, current, now, window),
            "changes": [e for e in events if e["ts"] >= now - window][-40:][::-1],
            "alarms": [a for a in snap.get("alarms") or []][:25],
        })

    # ---- AI insights ----------------------------------------------------------
    # The rule-based detection is always on; the language model only explains it,
    # only when asked, and nothing it says reaches the line.
    explain_state = {"last": None, "at": 0.0}
    EXPLAIN_COOLDOWN_S = 10.0

    def _window_arg():
        try:
            w = int(request.args.get("window", DEFAULT_WINDOW_S))
        except ValueError:
            w = -1
        return w if w in WINDOWS_S else None

    def _insights_now(window: int):
        now = clock()
        since = now - window
        ins = detect(collector.analysis_rows(since), collector.alarm_rows(since), now, window)
        snap = collector.snapshot(now=now, recent=0)
        connected = bool(bus.connected)
        findings = diagnose(stations_from_snapshot(snap), collector.health_events(now - 600),
                            collector.alarm_rows(now - 600), now, master_connected=connected)
        return now, ins, findings

    @app.route("/insights")
    def insights_page():
        return render_template("insights.html", windows=WINDOWS_S, default_window=DEFAULT_WINDOW_S)

    @app.route("/api/insights")
    def api_insights():
        window = _window_arg()
        if window is None:
            return jsonify(ok=False, error=f"window must be one of {list(WINDOWS_S)} seconds"), 400
        now, ins, findings = _insights_now(window)
        ins["demo"] = demo
        ins["demo_label"] = demo_label
        ins["findings"] = [{k: f.get(k) for k in ("station", "level", "title", "detail")} for f in findings]
        ins["provider"] = None if llm is None else {
            "name": llm.name, "model": getattr(llm, "model", None), "destination": llm.destination,
            "leaves_the_network": llm.leaves_the_network}
        ins["last_explanation"] = explain_state["last"]
        return jsonify(ins)

    @app.route("/api/insights/provider")
    def api_insights_provider():
        if llm is None:
            return jsonify(ok=False, configured=False,
                           detail="No language model is configured (insights.provider is none).")
        return jsonify(configured=True, **llm.check())

    @app.route("/api/insights/explain", methods=["POST"])
    def api_insights_explain():
        if not request.is_json:
            return jsonify(ok=False, error="send a JSON body (Content-Type: application/json)"), 415
        if llm is None:
            return jsonify(ok=False, error="no language model is configured (insights.provider is none)"), 409
        body = request.get_json(silent=True)
        body = body if isinstance(body, dict) else {}
        window = body.get("window", DEFAULT_WINDOW_S)
        if isinstance(window, bool) or window not in WINDOWS_S:
            return jsonify(ok=False, error=f"window must be one of {list(WINDOWS_S)} seconds"), 400
        t = time.monotonic()
        if t - explain_state["at"] < EXPLAIN_COOLDOWN_S:
            return jsonify(ok=False, error="asked again too soon; wait a few seconds"), 429
        explain_state["at"] = t
        now, ins, findings = _insights_now(window)
        try:
            result = explain(llm, ins, findings, clock=clock)
        except LlmError as e:
            return jsonify(ok=False, error=str(e)), 502
        explain_state["last"] = result
        return jsonify(ok=True, **result)

    @app.route("/classic")
    def classic():
        return render_template("master.html")

    @app.route("/api/plant")
    def api_plant():
        now = clock()
        plant = build_plant(collector.snapshot(now=now), now)
        plant["controls"] = controls_enabled
        plant["broker_connected"] = bool(bus.connected)
        # This PC and the person at it, so the 3D view can show them. They are not part of the
        # line's health (Troubleshoot never sees them): the PC's level is its own broker link, the
        # operator's is whether they may command anything.
        link = "connected to the broker" if bus.connected else "NOT connected to the broker"
        mode = demo_label if demo else "live"
        plant["stations"] = list(plant["stations"]) + [
            {"id": "master", "label": "Master PC (this GUI)", "u": None,
             "level": "ok" if bus.connected else "fail",
             "detail": f"{link}; controls {'ON' if controls_enabled else 'read-only'}; {mode}"},
            {"id": "user", "label": "Operator (you)", "u": None,
             "level": "ok" if controls_enabled else "offline",
             "detail": ("you can command the line from this page" if controls_enabled
                        else "view only - start the master with --controls to command the line")},
        ]
        plant["demo"] = demo
        plant["demo_label"] = demo_label
        return jsonify(plant)

    @app.route("/api/overview")
    def api_overview():
        snap = collector.snapshot()
        snap["controls"] = controls_enabled
        snap["broker_connected"] = bool(bus.connected)
        return jsonify(snap)

    @app.route("/api/export.csv")
    def api_export():
        return Response(
            collector.export_csv(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=tiles.csv"},
        )

    # ---- commands -----------------------------------------------------------

    def _guard():
        if not controls_enabled:
            return jsonify(ok=False, error="controls are disabled (dashboard.controls_enabled)"), 403
        if not request.is_json:
            return jsonify(ok=False, error="send a JSON body (Content-Type: application/json)"), 415
        return None

    def _body() -> dict:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def _send(topic: str, payload: dict):
        if not bus.publish(topic, payload, qos=1):
            return jsonify(ok=False, error="not connected to the station broker"), 503
        return jsonify(ok=True, sent={"topic": topic, "payload": payload})

    def _bool(body: dict, key: str) -> Optional[bool]:
        v = body.get(key)
        return v if isinstance(v, bool) else None

    @app.route("/api/command/vote", methods=["POST"])
    def cmd_vote():
        denied = _guard()
        if denied:
            return denied
        enabled = _bool(_body(), "enabled")
        if enabled is None:
            return jsonify(ok=False, error="enabled must be true or false"), 400
        return _send("station/cmd", {"cmd": "vote", "enabled": enabled})

    @app.route("/api/command/hold_all", methods=["POST"])
    def cmd_hold_all():
        denied = _guard()
        if denied:
            return denied
        enabled = _bool(_body(), "enabled")
        if enabled is None:
            return jsonify(ok=False, error="enabled must be true or false"), 400
        return _send("station/cmd", {"cmd": "hold_all", "enabled": enabled})

    @app.route("/api/command/clear_alarms", methods=["POST"])
    def cmd_clear_alarms():
        denied = _guard()
        if denied:
            return denied
        return _send("station/cmd", {"cmd": "clear_alarms"})

    @app.route("/api/command/line", methods=["POST"])
    def cmd_line():
        denied = _guard()
        if denied:
            return denied
        body = _body()
        state = body.get("state")
        if state not in ("start", "stop"):
            return jsonify(ok=False, error='state must be "start" or "stop"'), 400
        payload = {"cmd": "line", "state": state}
        if "belt_percent" in body and body["belt_percent"] is not None:
            pct = body["belt_percent"]
            if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 0 < pct <= 100:
                return jsonify(ok=False, error="belt_percent must be a number above 0 and up to 100"), 400
            if state == "stop":
                return jsonify(ok=False, error="belt_percent only applies to start"), 400
            payload["belt_percent"] = pct
        return _send("station/cmd", payload)

    @app.route("/api/command/feed", methods=["POST"])
    def cmd_feed():
        """Stop / resume handing NEW tiles to the gantry. Not a gantry pause: the
        machine finishes what it is on and idles with an empty cup."""
        denied = _guard()
        if denied:
            return denied
        state = _body().get("state")
        if state not in ("pause", "resume"):
            return jsonify(ok=False, error='state must be "pause" or "resume"'), 400
        return _send("station/cmd", {"cmd": "feed", "state": state})

    @app.route("/api/command/belt", methods=["POST"])
    def cmd_belt():
        denied = _guard()
        if denied:
            return denied
        pct = _body().get("percent")
        if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 0 <= pct <= 100:
            return jsonify(ok=False, error="percent must be a number from 0 to 100"), 400
        return _send("station/belt", {"percent": pct})

    @app.route("/api/command/strike", methods=["POST"])
    def cmd_strike():
        denied = _guard()
        if denied:
            return denied
        enabled = _bool(_body(), "enabled")
        if enabled is None:
            return jsonify(ok=False, error="enabled must be true or false"), 400
        return _send("station/strike", {"enabled": enabled})

    GANTRY_CMDS = ("pause", "resume", "acknowledge", "reset_stack", "home", "recover")

    @app.route("/api/command/gantry", methods=["POST"])
    def cmd_gantry():
        """pause / resume / acknowledge / reset_stack are harmless; home / recover
        MOVE the gantry, so they need `"confirm": true` here and
        `gantry.station.motion_commands_enabled` on the UNO Q (checked there)."""
        denied = _guard()
        if denied:
            return denied
        body = _body()
        name = body.get("cmd")
        if name not in GANTRY_CMDS:
            return jsonify(ok=False, error=f"cmd must be one of {list(GANTRY_CMDS)}"), 400
        payload = {"id": f"m-{int(clock() * 1000)}", "cmd": name}
        if name == "reset_stack":
            stack, count = body.get("stack"), body.get("count", 0)
            if not isinstance(stack, str) or not stack or isinstance(count, bool) or not isinstance(count, int) \
                    or count < 0:
                return jsonify(ok=False, error="reset_stack needs a stack name and a non-negative integer count"), 400
            payload.update(stack=stack, count=count)
        if name in ("home", "recover") and body.get("confirm") is not True:
            return jsonify(ok=False, error=f"{name} moves the machine: send \"confirm\": true"), 400
        return _send("station/gantry/cmd", payload)

    return app
