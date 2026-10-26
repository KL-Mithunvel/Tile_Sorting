"""Copies recorded data-collection sessions from the Pi to this PC, verified.

    python -m master.session_pull --pi-url http://192.168.1.11:5001 --dest data/master/sessions

For every session the Pi lists as finished it fetches the manifest, then each file it names,
and checks every file against the SHA-256 in the manifest before keeping it. Re-running is cheap
and safe: a file already here with the right hash is skipped, a download that was cut off resumes
(HTTP Range) from its `.part` file, and a file that arrives wrong is thrown away and reported --
never kept. A session still being recorded is skipped; its manifest.json is written here only
once every file has been verified, so a half-copied session is recognisable (no manifest).

The HTTP opener is injectable, so this is tested against the real Pi Flask app with no network.
Standard library only. Read-only on the Pi.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

Opener = Callable[[str, dict], tuple]      # (url, headers) -> (status, headers, stream with .read(n))
CHUNK = 1 << 20


def default_opener(url: str, headers: dict) -> tuple:
    req = urllib.request.Request(url, headers=headers)
    try:
        r = urllib.request.urlopen(req, timeout=30)  # noqa: S310 - operator-configured LAN URL
        return r.status, dict(r.headers), r
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def _get_json(opener: Opener, url: str, headers: dict):
    status, _, body = opener(url, headers)
    data = body.read()
    if status != 200:
        raise RuntimeError(f"{url}: HTTP {status}")
    return json.loads(data)


def _safe_target(root: Path, rel: str) -> Path:
    target = (root / rel).resolve()
    target.relative_to(root.resolve())          # a manifest must not write outside its session
    return target


def _download(opener: Opener, url: str, headers: dict, target: Path, want_size: int, want_sha: str) -> str:
    """'ok' | 'bad' (arrived wrong, removed) | 'failed' (could not fetch; partial kept to resume)."""
    part = target.with_name(target.name + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    have = part.stat().st_size if part.exists() else 0
    if have > want_size:
        part.unlink()
        have = 0
    hdrs = dict(headers)
    if have:
        hdrs["Range"] = f"bytes={have}-"
    try:
        status, _, stream = opener(url, hdrs)
        if status == 200 and have:
            have = 0                              # the Pi ignored the Range: start again
        elif status not in (200, 206):
            return "failed"
        with open(part, "ab" if have else "wb") as f:
            while True:
                block = stream.read(CHUNK)
                if not block:
                    break
                f.write(block)
    except (OSError, urllib.error.URLError, RuntimeError):
        return "failed"
    if part.stat().st_size != want_size or _sha256(part) != want_sha:
        part.unlink(missing_ok=True)
        return "bad"
    part.replace(target)
    return "ok"


def pull_sessions(base_url: str, dest: Path, token: Optional[str] = None, opener: Opener = default_opener,
                  only: Optional[str] = None, log: Callable[[str], None] = print) -> dict:
    """Returns {session_id: {"status": ..., "files": n, "bytes": n, "problems": [...]}}."""
    base, dest = base_url.rstrip("/"), Path(dest)
    headers = {"X-Sync-Token": token} if token else {}
    report: dict = {}
    listing = _get_json(opener, f"{base}/api/sync/sessions", headers)
    if not listing.get("enabled", True):
        raise RuntimeError("the Pi is not recording sessions (station.session.enabled)")
    for s in listing["sessions"]:
        sid = s["id"]
        if only and sid != only:
            continue
        if s.get("status") == "recording":
            report[sid] = {"status": "skipped (still recording)", "files": 0, "bytes": 0, "problems": []}
            continue
        root = dest / sid
        try:
            manifest = _get_json(opener, f"{base}/api/sync/session/{quote(sid)}/manifest", headers)
        except (RuntimeError, ValueError, OSError) as exc:
            report[sid] = {"status": "failed", "files": 0, "bytes": 0, "problems": [f"manifest: {exc}"]}
            continue
        problems, got, size = [], 0, 0
        for f in manifest.get("files", []):
            try:
                target = _safe_target(root, f["path"])
            except ValueError:
                problems.append(f"refused unsafe path {f['path']!r}")
                continue
            if target.is_file() and target.stat().st_size == f["size"] and _sha256(target) == f["sha256"]:
                got += 1
                size += f["size"]
                continue
            url = f"{base}/api/sync/session/{quote(sid)}/file/{quote(f['path'])}"
            result = _download(opener, url, headers, target, f["size"], f["sha256"])
            if result == "ok":
                log(f"  {sid}/{f['path']}  {f['size'] / 1048576:.1f} MB ok")
                got += 1
                size += f["size"]
            else:
                problems.append(f"{f['path']}: {'arrived wrong, discarded' if result == 'bad' else 'could not be fetched'}")
        if not problems:
            root.mkdir(parents=True, exist_ok=True)
            (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        report[sid] = {"status": "complete" if not problems else "incomplete", "files": got, "bytes": size,
                       "problems": problems}
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Copy recorded sessions from the Pi, verifying every file.")
    ap.add_argument("--pi-url", default="http://192.168.1.11:5001", help="the Pi station GUI")
    ap.add_argument("--dest", default="data/master/sessions", help="where to put them on this PC")
    ap.add_argument("--token", default=None, help="station.sync.token, if the Pi has one")
    ap.add_argument("--only", default=None, help="just this session id")
    a = ap.parse_args(argv)
    try:
        report = pull_sessions(a.pi_url, Path(a.dest), token=a.token, only=a.only)
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"Could not read the Pi's sessions: {exc}", file=sys.stderr)
        return 2
    bad = False
    for sid, r in report.items():
        print(f"{sid}: {r['status']}  ({r['files']} files, {r['bytes'] / 1048576:.1f} MB)")
        for p in r["problems"]:
            print(f"    ! {p}")
            bad = True
    if not report:
        print("The Pi has no sessions yet.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
