"""The station's web GUI (Flask, port 5001): what the line operator looks at.

Two pages:

- `/`        RUN view — read-only, big, glanceable from a few metres: the last
             tile's final grade and bin, per-grade counters, the health strip
             (broker / camera / mic / UNO Q), alarms. Meant to be left open in
             a fullscreen kiosk browser. If the page stops hearing from the
             station it says so in red instead of showing old data as live.
- `/service` SERVICE view — microphone device and live level, last tap
             waveform/spectrum, sound-vote and hold-all switches, simulate tap.

Everything that changes behaviour sits behind `controls_enabled` (config
station.dashboard.controls_enabled, OFF for production), is POST + JSON only
(a page on another site cannot send a JSON body here without a CORS preflight
this app never answers) and is refused with 403 otherwise. The one exception
is acknowledging alarms, which only hides the banner.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from flask import Flask, abort, jsonify, render_template, request, send_file

from acoustic.station import StationService

# acoustic/dashboard.py -> repo root. Tile pictures are only ever served from
# under <repo>/data.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def create_app(
    service: StationService,
    controls_enabled: bool = False,
    repo_root: Optional[Path] = None,
    sync_token: Optional[str] = None,
) -> Flask:
    app = Flask(__name__)
    # Read-only record API for the master PC (see acoustic/sync_api.py).
    from acoustic.sync_api import make_blueprint

    app.register_blueprint(make_blueprint(service.store, service.blobs, sync_token))
    data_root = ((repo_root or _REPO_ROOT) / "data").resolve()

    @app.route("/")
    def run_view():
        return render_template("station.html")

    @app.route("/service")
    def service_view():
        return render_template("service.html")

    @app.route("/api/station")
    def api_station():
        status = service.status()
        status["controls"] = controls_enabled
        return jsonify(status)

    @app.route("/api/tap")
    def api_tap():
        return jsonify(tap=service.last_tap(), level=service.level())

    @app.route("/api/audio/devices")
    def api_devices():
        return jsonify(devices=service.list_devices(), current=service.level()["device"])

    @app.route("/tile_image/<tile_id>")
    def tile_image(tile_id: str):
        """The camera's picture of this tile (the file the camera node saved),
        so the operator sees the tile that was actually graded, not whichever
        one the camera has seen most recently."""
        row = service.tile(tile_id)
        path = ((row or {}).get("camera") or {}).get("snapshot_path")
        if not path:
            abort(404)
        try:
            resolved = Path(path).resolve()
            resolved.relative_to(data_root)
        except (OSError, ValueError):
            abort(404)
        if resolved.suffix.lower() not in _IMAGE_SUFFIXES or not resolved.is_file():
            abort(404)
        return send_file(resolved, max_age=3600)

    # ---- controls ---------------------------------------------------------

    def _json_guard(needs_controls: bool = True):
        if needs_controls and not controls_enabled:
            return jsonify(ok=False, error="controls are disabled (station.dashboard.controls_enabled)"), 403
        if not request.is_json:
            return jsonify(ok=False, error="send a JSON body (Content-Type: application/json)"), 415
        return None

    def _body() -> dict:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    @app.route("/api/control/vote", methods=["POST"])
    def control_vote():
        denied = _json_guard()
        if denied:
            return denied
        service.coordinator.set_vote_enabled(bool(_body().get("enabled")))
        return jsonify(ok=True, vote_enabled=service.coordinator.vote_enabled)

    @app.route("/api/control/hold_all", methods=["POST"])
    def control_hold_all():
        denied = _json_guard()
        if denied:
            return denied
        service.coordinator.set_hold_all(bool(_body().get("enabled")))
        return jsonify(ok=True)

    @app.route("/api/control/simulate_tap", methods=["POST"])
    def control_simulate_tap():
        denied = _json_guard()
        if denied:
            return denied
        try:
            service.simulate_tap()
        except RuntimeError as exc:
            return jsonify(ok=False, error=str(exc)), 409
        return jsonify(ok=True)

    @app.route("/api/control/clear_alarms", methods=["POST"])
    def control_clear_alarms():
        denied = _json_guard(needs_controls=False)
        if denied:
            return denied
        service.coordinator.clear_alarms()
        return jsonify(ok=True)

    @app.route("/api/audio/device", methods=["POST"])
    def audio_device():
        denied = _json_guard()
        if denied:
            return denied
        device = _body().get("device")
        if device is None or isinstance(device, bool):
            return jsonify(ok=False, error="send a device index or name"), 400
        try:
            service.set_device(device)
        except RuntimeError as exc:
            return jsonify(ok=False, error=str(exc)), 409
        return jsonify(ok=True, device=device)

    return app
