"""Flask dashboard: live MJPEG video stream + tile count + crack/corner
results, served over the network (host 0.0.0.0) so it's viewable from any
browser on the same WiFi as the camera node — this is the camera station's
"station-local monitor" (project_charter.md §14 Monitoring Architecture,
requirements.md FR-19).
"""

from __future__ import annotations

import time
from typing import Optional

from flask import Flask, Response, jsonify, render_template, request

from camera.pipeline import tile_record_to_dict
from camera.station import StationController
from camera.worker import SharedState


def _mjpeg_stream(state: SharedState):
    while True:
        frame = state.get_frame()
        if frame is None:
            time.sleep(0.05)
            continue
        yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")


def create_app(
    state: SharedState,
    controller: Optional[StationController] = None,
    controls_enabled: bool = True,
) -> Flask:
    """`controller` drives the Source panel (switch camera, open a file,
    record). Without one the page is a plain viewer. `controls_enabled=False`
    keeps the controller but refuses every control request: the dashboard is
    served on the network, so a station that must not be re-pointed by whoever
    is on the WiFi turns them off (config: dashboard.controls_enabled)."""
    app = Flask(__name__)

    @app.route("/")
    def index():
        return render_template("dashboard.html")

    @app.route("/video_feed")
    def video_feed():
        return Response(_mjpeg_stream(state), mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.route("/last_tile.png")
    def last_tile():
        png = state.get_last_tile_png()
        if png is None:
            return Response(status=204)
        return Response(png, mimetype="image/png", headers={"Cache-Control": "no-store"})

    @app.route("/api/status")
    def api_status():
        status = state.status()
        return jsonify(
            {
                "tile_count": status["tile_count"],
                "recent_tiles": [tile_record_to_dict(r) for r in status["recent_tiles"]],
                "tally": status["tally"],
                "station_state": status["station_state"],
                "info": status["info"],
                "errors": status["errors"],
                "source": status["source"],
                "recording": status["recording"],
                "last_recording": status["last_recording"],
                "perf": status["perf"],
                "controls": bool(controller is not None and controls_enabled),
            }
        )

    # ---- source / recording controls ------------------------------------
    # POST + JSON only. A web page on another site cannot send a JSON body to
    # this address without a CORS preflight, which this app never answers, so
    # requiring it stops a stray page in the operator's browser from switching
    # the camera or starting a recording.

    def _guard():
        """(response, status) to return instead of acting, or None to proceed."""
        if controller is None:
            return jsonify(ok=False, error="this dashboard has no station controller"), 503
        if not controls_enabled:
            return jsonify(ok=False, error="controls are disabled (dashboard.controls_enabled)"), 403
        if not request.is_json:
            return jsonify(ok=False, error="send a JSON body (Content-Type: application/json)"), 415
        return None

    def _run(action):
        """Run a controller action and turn its errors into JSON replies:
        ValueError (bad input, refused path) -> 400, RuntimeError (wrong state,
        camera would not open) -> 409."""
        try:
            result = action()
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 400
        except RuntimeError as exc:
            return jsonify(ok=False, error=str(exc)), 409
        return jsonify(ok=True, **(result or {}))

    def _body() -> dict:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    @app.route("/api/sources")
    def api_sources():
        if controller is None or not controls_enabled:
            return jsonify(controls=False, source=state.status()["source"])
        if not controller.sources()["scanned"]:
            controller.scan_cameras()  # first look: in the background, never blocks the page
        return jsonify(controls=True, **controller.sources())

    @app.route("/api/source/camera", methods=["POST"])
    def api_source_camera():
        denied = _guard()
        if denied:
            return denied
        body = _body()

        def go():
            try:
                index = int(body.get("index"))
            except (TypeError, ValueError):
                raise ValueError("index must be a whole number") from None
            return {"source": controller.open_camera(index, csi=bool(body.get("csi")))}

        return _run(go)

    @app.route("/api/source/file", methods=["POST"])
    def api_source_file():
        denied = _guard()
        if denied:
            return denied
        body = _body()
        loop = bool(body.get("loop"))

        def go():
            if body.get("recording"):
                return {"source": controller.open_recording(str(body["recording"]), loop=loop)}
            return {"source": controller.open_file(str(body.get("path") or ""), loop=loop)}

        return _run(go)

    @app.route("/api/source/playback", methods=["POST"])
    def api_source_playback():
        denied = _guard()
        if denied:
            return denied
        body = _body()

        def go():
            if "paused" not in body and "loop" not in body:
                raise ValueError("send paused and/or loop")
            if "paused" in body:
                controller.set_paused(bool(body["paused"]))
            if "loop" in body:
                controller.set_loop(bool(body["loop"]))
            return {}

        return _run(go)

    @app.route("/api/record/start", methods=["POST"])
    def api_record_start():
        denied = _guard()
        if denied:
            return denied
        return _run(lambda: controller.start_recording())

    @app.route("/api/record/stop", methods=["POST"])
    def api_record_stop():
        denied = _guard()
        if denied:
            return denied
        return _run(lambda: {"recording": controller.stop_recording()})

    @app.route("/api/cameras/scan", methods=["POST"])
    def api_cameras_scan():
        denied = _guard()
        if denied:
            return denied
        return _run(lambda: controller.scan_cameras() or {})

    return app
