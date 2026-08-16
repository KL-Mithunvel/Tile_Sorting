"""Flask dashboard: live MJPEG video stream + tile count + crack/corner
results, served over the network (host 0.0.0.0) so it's viewable from any
browser on the same WiFi as the camera node — this is the camera station's
"station-local monitor" (project_charter.md §14 Monitoring Architecture,
requirements.md FR-19).
"""

from __future__ import annotations

import time

from flask import Flask, Response, jsonify, render_template

from camera.pipeline import tile_record_to_dict
from camera.worker import SharedState


def _mjpeg_stream(state: SharedState):
    while True:
        frame = state.get_frame()
        if frame is None:
            time.sleep(0.05)
            continue
        yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")


def create_app(state: SharedState) -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index():
        return render_template("dashboard.html")

    @app.route("/video_feed")
    def video_feed():
        return Response(_mjpeg_stream(state), mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.route("/api/status")
    def api_status():
        status = state.status()
        return jsonify(
            {
                "tile_count": status["tile_count"],
                "recent_tiles": [tile_record_to_dict(r) for r in status["recent_tiles"]],
            }
        )

    return app
