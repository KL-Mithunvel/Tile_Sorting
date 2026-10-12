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
from master.plant import STATIONS, build_plant, stations_from_snapshot
from master.troubleshoot import diagnose, timeline


def create_app(collector: MasterCollector, bus, controls_enabled: bool = False,
               demo: bool = False, clock: Callable[[], float] = time.time) -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index():
        return render_template("plant.html")

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
            "now": now, "window_s": window, "demo": demo, "broker_connected": connected,
            "labels": labels,
            "findings": diagnose(stations, events, collector.alarm_rows(now - 600), now, master_connected=connected),
            "timeline": timeline(events, current, now, window),
            "changes": [e for e in events if e["ts"] >= now - window][-40:][::-1],
            "alarms": [a for a in snap.get("alarms") or []][:25],
        })

    @app.route("/classic")
    def classic():
        return render_template("master.html")

    @app.route("/api/plant")
    def api_plant():
        now = clock()
        plant = build_plant(collector.snapshot(now=now), now)
        plant["controls"] = controls_enabled
        plant["broker_connected"] = bool(bus.connected)
        plant["demo"] = demo
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

    return app
