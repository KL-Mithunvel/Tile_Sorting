"""Flask dashboard: the digital twin visualization prototype
(documents/project/Pick_Place_Sorting_Subsystem.md §3) — live gantry position
+ vacuum state, rendered client-side with vendored Three.js, served over the
network (host 0.0.0.0) so it's viewable from any browser on the same WiFi as
this node, same convention as camera_node's dashboard.
"""

from __future__ import annotations

from flask import Flask, jsonify, render_template, request

from pick_place.worker import SharedState


def create_app(state: SharedState, workspace: dict, poll_interval_s: float,
               backend=None, jog_steps_mm=(1, 10, 50), scene=None) -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index():
        return render_template(
            "twin.html",
            poll_interval_ms=int(poll_interval_s * 1000),
            jog_steps_mm=list(jog_steps_mm),
            controls_enabled=backend is not None,
        )

    @app.route("/api/status")
    def api_status():
        return jsonify(state.status())

    @app.route("/api/config")
    def api_config():
        return jsonify({"workspace": workspace, "scene": scene or {}})

    def _command(fn):
        """Run a backend command; ValueError -> 400, no backend -> 503."""
        if backend is None:
            return jsonify({"error": "no control backend"}), 503
        try:
            fn()
        except (ValueError, KeyError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(state.status())

    @app.route("/api/jog", methods=["POST"])
    def api_jog():
        body = request.get_json(silent=True) or {}
        return _command(lambda: backend.jog(body["axis"], float(body["delta_mm"])))

    @app.route("/api/vacuum", methods=["POST"])
    def api_vacuum():
        body = request.get_json(silent=True) or {}
        if not isinstance(body.get("on"), bool):
            return jsonify({"error": "'on' must be true or false"}), 400
        return _command(lambda: backend.set_vacuum(body["on"]))

    @app.route("/api/mode", methods=["POST"])
    def api_mode():
        body = request.get_json(silent=True) or {}
        return _command(lambda: backend.set_mode(body["mode"]))

    return app
