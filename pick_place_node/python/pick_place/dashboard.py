"""Flask dashboard: the digital twin visualization prototype
(documents/project/Pick_Place_Sorting_Subsystem.md §3) — live gantry position
+ vacuum state, rendered client-side with vendored Three.js, served over the
network (host 0.0.0.0) so it's viewable from any browser on the same WiFi as
this node, same convention as camera_node's dashboard.
"""

from __future__ import annotations

from flask import Flask, jsonify, render_template

from pick_place.worker import SharedState


def create_app(state: SharedState, workspace: dict, poll_interval_s: float) -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index():
        return render_template("twin.html", poll_interval_ms=int(poll_interval_s * 1000))

    @app.route("/api/status")
    def api_status():
        return jsonify(state.status())

    @app.route("/api/config")
    def api_config():
        return jsonify({"workspace": workspace})

    return app
