"""Flask GUI for the encoder_smoke_test bench app: live encoder dials, per-axis
move tests with pass/fail, I2C scan and mux reset. Served on 0.0.0.0 (config)
like the other bench dashboards."""

from __future__ import annotations

from flask import Flask, jsonify, render_template, request

from bridge_backend import BridgeEncBackend, SharedState


def create_app(state: SharedState, backend: BridgeEncBackend, config: dict) -> Flask:
    app = Flask(__name__)
    page_cfg = {
        "channels": config["encoder"]["channel_names"],
        "axes": config["axes"],
        "tolerance_deg": config["encoder"]["tolerance_deg"],
        "expected_deg": config["encoder"]["deg_per_motor_rev"],
        "poll_ms": int(config["dashboard"]["poll_interval_s"] * 1000),
    }

    def command(fn, *args):
        try:
            fn(*args)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:  # bridge down / timeout
            return jsonify({"ok": False, "error": str(exc)}), 503
        return jsonify({"ok": True})

    @app.route("/")
    def index():
        return render_template("encoder_test.html", cfg=page_cfg)

    @app.route("/api/status")
    def api_status():
        return jsonify(state.snapshot())

    @app.route("/api/move", methods=["POST"])
    def api_move():
        payload = request.get_json(silent=True) or {}
        try:
            axis = int(payload.get("axis", -1))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "axis must be an integer"}), 400
        return command(backend.move, axis)

    @app.route("/api/jog", methods=["POST"])
    def api_jog():
        payload = request.get_json(silent=True) or {}
        try:
            axis, steps = int(payload.get("axis", -1)), int(payload.get("steps", 0))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "axis and steps must be integers"}), 400
        return command(backend.jog, axis, steps)

    @app.route("/api/stop", methods=["POST"])
    def api_stop():
        return command(backend.halt)

    @app.route("/api/scan", methods=["POST"])
    def api_scan():
        return command(backend.scan)

    @app.route("/api/mux_reset", methods=["POST"])
    def api_mux_reset():
        return command(backend.mux_reset)

    @app.route("/api/zero", methods=["POST"])
    def api_zero():
        return command(backend.zero)

    @app.route("/api/clear_history", methods=["POST"])
    def api_clear_history():
        state.clear_history()
        return jsonify({"ok": True})

    return app
