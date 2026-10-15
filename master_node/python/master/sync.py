"""Pulls the Pi's record into the master's `PiMirror`.

One pass (`sync_once`):

1. ask the Pi what it holds (`/api/sync/info`); if it is a different database
   than last time the cursor is reset (see `PiMirror.adopt_store`);
2. fetch the events after our cursor, page by page;
3. fetch the full current row of every tile those events touched;
4. only now advance the cursor - so a pass that dies in the middle re-does the
   same events next time instead of silently skipping them;
5. fetch the evidence files we do not have, verifying each against the SHA-256
   the Pi registered. A file that fails stays `missing` (or `corrupt`) and is
   retried on the next pass; it never holds the cursor back.

The HTTP function is injectable (`http(url, headers, timeout) -> (status,
headers, body)`), so the whole pass is tested against the real Pi Flask app
with no network. Never raises out of `sync_once`: an unreachable Pi is a
report, not a crash.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Optional
from urllib.parse import quote

from master.mirror import PiMirror

log = logging.getLogger(__name__)

Http = Callable[[str, dict, float], tuple]

_SUFFIX = {"audio/wav": ".wav", "application/json": ".json", "image/jpeg": ".jpg", "image/png": ".png"}


def default_http(url: str, headers: dict, timeout: float) -> tuple:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - operator-configured LAN URL
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()


class SyncError(Exception):
    pass


class PiSyncer:
    def __init__(self, mirror: PiMirror, base_url: str, token: Optional[str] = None, http: Http = default_http,
                 clock: Callable[[], float] = time.time, timeout_s: float = 10.0, page: int = 500,
                 max_pages: int = 200, fetch_blobs: bool = True, max_blobs_per_pass: int = 200):
        self._mirror = mirror
        self._base = base_url.rstrip("/")
        self._token = token
        self._http = http
        self._clock = clock
        self._timeout = timeout_s
        self._page = page
        self._max_pages = max_pages
        self._fetch_blobs = fetch_blobs
        self._max_blobs = max_blobs_per_pass
        self._lock = threading.Lock()          # one pass at a time
        self.last_report: Optional[dict] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ---- http ---------------------------------------------------------------

    def _get(self, path: str):
        headers = {"X-Sync-Token": self._token} if self._token else {}
        try:
            return self._http(self._base + path, headers, self._timeout)
        except Exception as exc:  # noqa: BLE001 - DNS, refused, timeout...
            raise SyncError(f"could not reach the Pi at {self._base}: {exc}") from exc

    def _json(self, path: str) -> dict:
        status, _h, body = self._get(path)
        if status == 401:
            raise SyncError("the Pi refused the sync token (401)")
        if status != 200:
            raise SyncError(f"the Pi answered {status} for {path.split('?')[0]}")
        try:
            data = json.loads(body)
        except ValueError as exc:
            raise SyncError(f"the Pi sent something that is not JSON for {path.split('?')[0]}") from exc
        if not isinstance(data, dict):
            raise SyncError(f"unexpected reply shape for {path.split('?')[0]}")
        return data

    # ---- one pass -----------------------------------------------------------

    def sync_once(self) -> dict:
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "error": "a sync is already running", "ts": self._clock()}
        report = {"ok": False, "ts": self._clock(), "error": None, "events": 0, "tiles": 0, "blobs_fetched": 0,
                  "blobs_failed": 0, "blobs_pruned_on_pi": 0, "store_replaced": False}
        try:
            info = self._json("/api/sync/info")
            sid = info.get("store_id")
            if not isinstance(sid, str) or not sid:
                raise SyncError("the Pi did not say which database it is (store_id)")
            report["store_replaced"] = self._mirror.adopt_store(sid)

            touched: set[str] = set()
            cursor = self._mirror.cursor()
            for _ in range(self._max_pages):
                page = self._json(f"/api/sync/events?after={cursor}&limit={self._page}")
                events = page.get("events") or []
                if not events:
                    break
                report["events"] += self._mirror.apply_events(events)
                touched.update(e["tile_id"] for e in events if e.get("tile_id"))
                cursor = int(events[-1]["seq"])
                # Tile rows for what this page touched come BEFORE the cursor moves.
                report["tiles"] += self._pull_tiles(sorted(touched))
                touched.clear()
                self._mirror.set_cursor(cursor)
            report["cursor"] = self._mirror.cursor()
            report["pi_head"] = int(info.get("head_seq", 0))
            report["behind"] = max(0, report["pi_head"] - report["cursor"])

            if self._fetch_blobs:
                self._pull_blobs(report)
            report["ok"] = True
        except SyncError as exc:
            report["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001 - a bug here must not kill the master
            log.exception("sync pass failed")
            report["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            self._lock.release()
        self.last_report = report
        return report

    def _pull_tiles(self, ids: list[str]) -> int:
        n = 0
        for i in range(0, len(ids), 200):
            chunk = ids[i:i + 200]
            data = self._json("/api/sync/tiles?ids=" + ",".join(quote(t, safe="") for t in chunk))
            n += self._mirror.apply_tiles(data.get("tiles") or [])
        return n

    def _pull_blobs(self, report: dict) -> None:
        for b in self._mirror.missing_blobs(self._max_blobs):
            tile_id, name = b["tile_id"], b["name"]
            status, headers, body = self._get(f"/api/sync/blob/{quote(tile_id, safe='')}/{quote(name, safe='')}")
            if status == 410:
                self._mirror.set_blob(tile_id, name, "pruned")
                report["blobs_pruned_on_pi"] += 1
                continue
            if status != 200:
                report["blobs_failed"] += 1
                continue
            if hashlib.sha256(body).hexdigest() != b["sha256"]:
                self._mirror.set_blob(tile_id, name, "corrupt")
                report["blobs_failed"] += 1
                continue
            mime = next((v for k, v in headers.items() if k.lower() == "content-type"), "").split(";")[0]
            dest = self._mirror.blob_destination(tile_id, name, _SUFFIX.get(mime, ".bin"))
            if dest is None:
                report["blobs_failed"] += 1
                continue
            tmp = dest.with_suffix(dest.suffix + ".part")
            tmp.write_bytes(body)
            tmp.replace(dest)
            self._mirror.set_blob(tile_id, name, "ok", str(dest))
            report["blobs_fetched"] += 1

    # ---- is the copy complete? ------------------------------------------------

    def reconcile(self) -> dict:
        """Compare this copy to what the Pi says it holds, right now."""
        try:
            info = self._json("/api/sync/info")
        except SyncError as exc:
            return {"ok": False, "error": str(exc)}
        mine = self._mirror.counts()
        blobs = mine["blobs"]
        problems = []
        if info.get("store_id") != self._mirror.store_id():
            problems.append("the Pi's database is not the one this copy follows")
        if mine["tiles"] < info.get("tiles", 0):
            problems.append(f"{info['tiles'] - mine['tiles']} tile(s) on the Pi are not here yet")
        if self._mirror.cursor() < info.get("head_seq", 0):
            problems.append(f"{info['head_seq'] - self._mirror.cursor()} event(s) not pulled yet")
        have_files = blobs.get("ok", 0)
        if have_files < info.get("blobs_live", 0):
            problems.append(f"{info['blobs_live'] - have_files} evidence file(s) not fetched yet")
        if blobs.get("corrupt", 0):
            problems.append(f"{blobs['corrupt']} evidence file(s) failed their checksum")
        return {"ok": True, "in_sync": not problems, "problems": problems,
                "pi": {k: info.get(k) for k in ("tiles", "head_seq", "blobs_live", "blobs_pruned", "blob_bytes")},
                "here": {"tiles": mine["tiles"], "cursor": self._mirror.cursor(), "blobs": blobs}}

    # ---- background ---------------------------------------------------------

    def start(self, interval_s: float = 15.0) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.is_set():
                self.sync_once()
                self._stop.wait(interval_s)

        self._thread = threading.Thread(target=loop, daemon=True, name="pi-sync")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
