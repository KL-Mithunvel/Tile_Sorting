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

from flask import Flask, Response, abort, jsonify, render_template, request, send_file

from master.collector import MasterCollector
from master.analysis import DEFAULT_WINDOW_S, WINDOWS_S, build_analysis
from master.insights import detect
from master.integrity import analyse as analyse_record
from master.llm import LlmError, explain
from master.plant import STATIONS, build_plant, stations_from_snapshot
from master.troubleshoot import diagnose, timeline


def create_app(collector: MasterCollector, bus, controls_enabled: bool = False,
               demo: bool = False, clock: Callable[[], float] = time.time, llm=None,
               demo_label: str = "demo — synthetic line", mirror=None, syncer=None) -> Flask:
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

    def _record_checks(window: int, now: float) -> dict:
        """Consistency checks over the Pi's own record (the master's synced copy)."""
        if mirror is None:
            return {"now": now, "tiles_checked": 0, "events_checked": 0, "findings": [], "available": False}
        since = now - window
        tiles = mirror.tiles(since)
        blobs: dict = {}
        for t in tiles:
            full = mirror.tile(t["tile_id"])
            if full and full.get("blobs"):
                blobs[t["tile_id"]] = {b["name"]: b["status"] for b in full["blobs"]}
        report = syncer.last_report if syncer is not None else None
        out = analyse_record(tiles, mirror.events(since_ts=since - 300), now, blobs=blobs, sync_report=report)
        out["available"] = True
        return out

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
        ins["record_checks"] = _record_checks(window, now)["findings"]
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
            result = explain(llm, ins, findings, clock=clock, integrity=_record_checks(window, now)["findings"])
        except LlmError as e:
            return jsonify(ok=False, error=str(e)), 502
        explain_state["last"] = result
        return jsonify(ok=True, **result)

    # ---- the Pi's own record (synced copy) ---------------------------------------

    @app.route("/tiles")
    def tiles_page():
        return render_template("tiles.html", windows=WINDOWS_S, default_window=DEFAULT_WINDOW_S)

    def _compact(t: dict) -> dict:
        cam, ac = t.get("camera") or {}, t.get("acoustic") or {}
        return {
            "tile_id": t["tile_id"], "created_ts": t["created_ts"], "state": t.get("state"),
            "camera_grade": t.get("camera_grade"), "acoustic_grade": t.get("acoustic_grade"),
            "final_grade": t.get("final_grade"), "destination": t.get("destination"),
            "delivery": t.get("delivery"), "placement": t.get("placement"), "transit_s": t.get("transit_s"),
            "peak_hz": ac.get("peak_frequency_hz"), "tap_valid": ac.get("valid"),
            "cracks": cam.get("crack_count") if isinstance(cam, dict) else None,
            "disagree": bool(t.get("camera_grade") and t.get("acoustic_grade")
                             and t["camera_grade"] != t["acoustic_grade"]),
        }

    @app.route("/api/pi/tiles")
    def api_pi_tiles():
        if mirror is None:
            return jsonify(ok=False, error="this master has no Pi record (mirror not configured)"), 409
        try:
            limit = max(1, min(int(request.args.get("limit", 200)), 2000))
        except ValueError:
            return jsonify(ok=False, error="limit must be an integer"), 400
        grade = request.args.get("grade")
        flag = request.args.get("flag")
        rows = [_compact(t) for t in mirror.tiles(newest_first=True, limit=20000)]
        if grade:
            rows = [r for r in rows if r["final_grade"] == grade]
        if flag == "disagree":
            rows = [r for r in rows if r["disagree"]]
        elif flag == "problem":
            rows = [r for r in rows if r["delivery"] in ("needs_person", "dropped", "rejected")
                    or r["placement"] in ("failed", "overdue") or r["tap_valid"] is False]
        return jsonify(ok=True, total=len(rows), tiles=rows[:limit], counts=mirror.counts())

    @app.route("/api/pi/tile/<tile_id>")
    def api_pi_tile(tile_id: str):
        if mirror is None:
            return jsonify(ok=False, error="no Pi record"), 409
        t = mirror.tile(tile_id)
        if t is None:
            return jsonify(ok=False, error="unknown tile"), 404
        return jsonify(ok=True, tile=t, events=mirror.events(tile_id=tile_id))

    @app.route("/api/pi/blob/<tile_id>/<name>")
    def api_pi_blob(tile_id: str, name: str):
        if mirror is None:
            abort(404)
        p = mirror.blob_path(tile_id, name)
        if p is None:
            abort(404)
        return send_file(p, max_age=3600)

    @app.route("/api/pi/sync", methods=["GET", "POST"])
    def api_pi_sync():
        if syncer is None or mirror is None:
            return jsonify(ok=False, configured=False,
                           error="no Pi sync is configured (sync.pi_url in config.yaml)"), 409
        if request.method == "POST":
            if not request.is_json:
                return jsonify(ok=False, error="send a JSON body (Content-Type: application/json)"), 415
            return jsonify(configured=True, **syncer.sync_once())
        out = {"ok": True, "configured": True, "last": syncer.last_report, "counts": mirror.counts(),
               "cursor": mirror.cursor()}
        if request.args.get("reconcile"):
            out["reconcile"] = syncer.reconcile()
        return jsonify(out)

    @app.route("/api/pi/integrity")
    def api_pi_integrity():
        window = _window_arg()
        if window is None:
            return jsonify(ok=False, error=f"window must be one of {list(WINDOWS_S)} seconds"), 400
        return jsonify(ok=True, **_record_checks(window, clock()))

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
