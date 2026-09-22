"""Flask GUI for the io_smoke_test bench app -- live E-stop/limit-switch
readings and a vacuum-solenoid on/off toggle, served over the network (host
0.0.0.0 from config.yaml) same convention as camera_node/pick_place_node's
other dashboards.
"""

from __future__ import annotations

from flask import Flask, jsonify, render_template, request

from serial_backend import SerialIOBackend, SharedState


def create_app(state: SharedState, backend: SerialIOBackend, poll_interval_s: float) -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index():
        return render_template("io_test.html", poll_interval_ms=int(poll_interval_s * 1000))

    @app.route("/api/status")
    def api_status():
        return jsonify(state.status())

    @app.route("/api/vac", methods=["POST"])
    def api_vac():
        payload = request.get_json(silent=True) or {}
        on = bool(payload.get("on", False))
        try:
            backend.set_vacuum(on)
        except RuntimeError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 503
        return jsonify({"ok": True, "on": on})

    return app
