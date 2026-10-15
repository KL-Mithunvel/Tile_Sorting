"""Read-only API the master PC pulls the Pi's record through.

Mounted on the station GUI's Flask app (port 5001). Four GET endpoints:

- /api/sync/info                          what the Pi holds (counts, head seq, store id)
- /api/sync/events?after=N&limit=M        history after cursor N, oldest first
- /api/sync/tiles?ids=a,b,c               full current rows for those tile ids
- /api/sync/blob/<tile_id>/<name>         one evidence file (picture, WAV, preview)

It never writes anything and cannot change how the line behaves. The master
keeps its own cursor, so a master that was off for a day just asks again from
where it stopped. If `station.sync.token` is set, every request must carry it
in the `X-Sync-Token` header (the GUI is served on 0.0.0.0, so anyone on the
LAN could otherwise read the record).
"""

from __future__ import annotations

import hmac
from typing import Optional

from flask import Blueprint, abort, jsonify, request, send_file

from fusion.blobs import BlobStore
from fusion.store import TileStore

MAX_EVENTS = 2000
MAX_IDS = 500


def make_blueprint(store: TileStore, blobs: Optional[BlobStore], token: Optional[str] = None) -> Blueprint:
    bp = Blueprint("sync", __name__, url_prefix="/api/sync")

    @bp.before_request
    def _auth():
        if token:
            given = request.headers.get("X-Sync-Token", "")
            if not hmac.compare_digest(given.encode(), str(token).encode()):
                abort(401)

    @bp.route("/info")
    def info():
        m = store.manifest()
        m["blob_budget_bytes"] = blobs.budget_bytes if blobs else 0
        m["has_blobs"] = blobs is not None
        return jsonify(m)

    @bp.route("/events")
    def events():
        try:
            after = int(request.args.get("after", 0))
            limit = min(int(request.args.get("limit", 500)), MAX_EVENTS)
        except ValueError:
            return jsonify(ok=False, error="after and limit must be integers"), 400
        if after < 0 or limit < 1:
            return jsonify(ok=False, error="after must be >= 0 and limit >= 1"), 400
        evs = store.events_after(after, limit)
        return jsonify(store_id=store.store_id, head_seq=store.head_seq(), events=evs,
                       next_after=evs[-1]["seq"] if evs else after)

    @bp.route("/tiles")
    def tiles():
        ids = [i for i in (request.args.get("ids") or "").split(",") if i]
        if len(ids) > MAX_IDS:
            return jsonify(ok=False, error=f"at most {MAX_IDS} ids per call"), 400
        rows = store.rows_by_ids(ids)
        for r in rows:
            r["blobs"] = [{k: b[k] for k in ("name", "sha256", "size", "mime", "pruned")}
                          for b in store.blobs_for(r["tile_id"])]
        return jsonify(store_id=store.store_id, tiles=rows)

    @bp.route("/blob/<tile_id>/<name>")
    def blob(tile_id: str, name: str):
        if blobs is None:
            abort(404)
        row = store.get_blob(tile_id, name)
        if row is None:
            abort(404)
        if row["pruned"]:
            return jsonify(ok=False, error="pruned", detail="the Pi deleted this file to stay under its disk budget"), 410
        path = blobs.path_for(tile_id, name)
        if path is None:
            abort(404)
        resp = send_file(path, mimetype=row.get("mime") or "application/octet-stream", max_age=0)
        resp.headers["X-Blob-Sha256"] = row["sha256"]
        return resp

    return bp
