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
               backend=None, jog_steps_mm=(1, 10, 50), scene=None, feed=None,
               model=None, driver=None, feed_source: str = "station") -> Flask:
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

    @app.route("/api/feed")
    def api_feed():
        """The tiles the Pi station has sent, in order (None when this node is
        not connected to the station broker)."""
        if feed is None:
            return jsonify(connected=False, queue=None, source=None)
        return jsonify(connected=True, queue=feed.queue.snapshot(), last_message_ts=feed.last_message_ts,
                       source=feed_source)

    @app.route("/api/layout")
    def api_layout():
        """Where tiles are taken from and kept: pickup, the stacks, live counts,
        tile spec (None when no coordinate model is attached)."""
        if model is None:
            return jsonify(layout=None)
        return jsonify(layout=model.describe())

    @app.route("/api/job")
    def api_job():
        """What the gantry is doing for the current tile (None without a driver)."""
        if driver is None:
            return jsonify(job=None)
        return jsonify(job=driver.status())

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

    def _runner_action(name):
        """Home / recover / acknowledge need a real job runner (not the mock driver)."""
        fn = getattr(driver, name, None)
        if fn is None:
            return jsonify({"error": f"{name} is only available with --backend sim or serial"}), 501
        if not fn():
            return jsonify({"error": f"{name} refused in the current state (busy, or an alarm "
                                     "is still latched)"}), 409
        return jsonify(driver.status())

    @app.route("/api/home", methods=["POST"])
    def api_home():
        return _runner_action("request_home")

    @app.route("/api/recover", methods=["POST"])
    def api_recover():
        return _runner_action("recover")

    @app.route("/api/acknowledge", methods=["POST"])
    def api_acknowledge():
        return _runner_action("acknowledge")

    @app.route("/api/mode", methods=["POST"])
    def api_mode():
        body = request.get_json(silent=True) or {}
        return _command(lambda: backend.set_mode(body["mode"]))

    return app
