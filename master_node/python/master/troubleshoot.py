"""Troubleshooting for the master: what is wrong right now, since when, what to
look at first, and how each station has behaved over a window.

Pure: takes plain station levels, health-change events and alarms and returns
plain data. The "what to check" lists are *suggestions written from how the
station reports the symptom*, not verified diagnoses - nothing here can tell a
dead cable from a crashed service. They are ordered cheapest-check-first.

The detail strings matched below are the ones the Pi station emits
(`fusion/coordinator.py::_health`); an unmatched detail still produces a
finding, with generic advice, rather than being dropped.
"""

from __future__ import annotations

import re
from typing import Optional

SEVERITY = {"fail": 0, "offline": 1, "warn": 2, "ok": 3}
RECENT_ALARM_S = 600.0

# station id -> ((regex on the detail text, title, meaning, [checks]), ...)
_RULES: dict[str, tuple] = {
    "pi": (
        (r"no status|not reporting", "The Pi station is not reporting",
         "The master has not heard the station's status for several seconds. The line keeps deciding on its own "
         "(it does not need this master), but you cannot see it.",
         ["Is the Pi powered and on the control LAN (can this PC ping 192.168.1.11)?",
          "Is the broker up? On the Pi: systemctl status mosquitto",
          "Is the station service up? On the Pi: systemctl status tile-station, then journalctl -u tile-station -f",
          "Is this PC on the control LAN and pointed at the right broker (--host)?"]),
        (r"fault|.", "The station reports a fault",
         "The Pi station found at least one of its own checks failing; the per-station findings below say which.",
         ["Read the individual station findings below - they are the cause, this is the summary."]),
    ),
    "camera": (
        (r"no heartbeat received yet", "The camera has never reported",
         "The Pi has not received a single camera heartbeat since it started.",
         ["Is the camera service running? journalctl -u tile-camera -f",
          "Is `mqtt.enabled` true in camera config.yaml?",
          "Is a source open (a camera, not 'no source')? Check the camera dashboard on :5000."]),
        (r"silent for", "The camera stopped sending heartbeats",
         "It was alive and then went quiet.",
         ["Is the camera service still running (systemctl status tile-camera)?",
          "Is the camera cable seated (CSI ribbon or USB)? A frozen feed usually means the device dropped.",
          "Has the Pi throttled or run hot? vcgencmd get_throttled",
          "Look at the camera dashboard's source status for 'the camera has stopped delivering frames'."]),
        (r"went offline", "The camera service reported it is going offline",
         "The camera service sent its 'offline' last-will or shut itself down.",
         ["Was it stopped on purpose (a restart, an update)?",
          "journalctl -u tile-camera -n 100 for the reason it exited."]),
        (r"results are not live", "The camera is running on a recording, not live",
         "Its source is a video file, so the grades are replayed, not from tiles on the belt now.",
         ["On the camera dashboard, switch the source back to the camera."]),
        (r"^source ", "The camera source is not running",
         "The camera service is up but its source is paused, finished or in error.",
         ["Open the camera dashboard (:5000) and read the Source bar.",
          "If it says error: re-select the camera; check the device is not used by another program."]),
    ),
    "acoustic": (
        (r"not started", "The microphone has not started",
         "The acoustic side of the station service has not opened an input.",
         ["Is the station service running (journalctl -u tile-station -f)?",
          "Is a USB microphone attached? python -m acoustic.live_monitor --list-devices on the Pi."]),
        (r"no audio blocks", "No audio is arriving from the microphone",
         "The input was opened but is delivering nothing.",
         ["Is the USB microphone still plugged in and not muted?",
          "Re-seat the USB lead; check `acoustic.device` in config matches --list-devices."]),
        (r".", "The microphone reports a problem",
         "The microphone input failed to open or read.",
         ["Read the detail text shown above; then --list-devices on the Pi and compare with config."]),
    ),
    "conveyor": (
        (r"no serial bridge heartbeat", "The Nano has never reported",
         "The Pi has not heard the belt controller (Nano) since it started.",
         ["Is the Nano powered and its USB lead connected to the Pi?",
          "Is the tap bridge enabled and pointed at the right serial port?",
          "The Nano firmware may not be flashed yet (it is not written - see nano_serial_protocol.md)."]),
        (r"silent for", "The Nano stopped answering",
         "It was reporting and then went quiet. Its watchdog should stop the belt by itself.",
         ["Is the belt actually stopped? Look at it - the Nano's own watchdog should have halted it.",
          "Re-seat the Nano's USB lead; check the e-stop is not pressed."]),
        (r"offline|not answering", "The serial link to the Nano is down",
         "The bridge on the Pi is up but the Nano is not answering on serial.",
         ["USB lead and port; the wrong port in config; another program holding the serial port."]),
    ),
    "gantry": (
        (r"no acknowledgement", "The UNO Q is not acknowledging final grades",
         "The Pi sent finals and got no acknowledgement after its retries - tiles are queuing for the gantry.",
         ["Is the UNO Q powered and on the LAN (ping its reserved address)?",
          "Is the pick-and-place app running on it (arduino-app-cli app list)?",
          "Check its subscription to tile/+/final and that it publishes tile/<id>/ack."]),
        (r"waiting for ack", "Waiting for an acknowledgement from the UNO Q",
         "A final grade is in flight; normal for a moment, a problem if it lasts.",
         ["If it stays on this for more than a few seconds, treat it as 'not acknowledging' above."]),
        (r"nothing acknowledged yet|not required", "The UNO Q has not acknowledged anything yet",
         "No final has been acknowledged since start-up (or acknowledgements are switched off for bench work).",
         ["Fine on the bench with acknowledgements off; on the real line check the UNO Q app is running."]),
    ),
    "master": (
        (r".", "This master cannot reach the broker",
         "While this link is down every station below reads offline and means nothing.",
         ["Is this PC on the control LAN? Can it ping the broker?",
          "Is the broker address in master config.yaml (or --host) right?"]),
    ),
}

# alarm kind -> (title, advice)
_ALARM_ADVICE = {
    "missed_camera": ("A tile reached the striker with no camera result",
                      "The camera missed a tile or the trigger line was crossed too fast. Each one is sent as Hold; "
                      "check lighting, belt speed and the camera trigger line."),
    "no_tap_timeout": ("Tiles timed out waiting for a tap",
                       "The strike never came for a graded tile. Check the striker, the ToF trigger and that the "
                       "belt actually carried the tile to the station; max-wait may be too short for belt speed."),
    "orphan_tap": ("A tap was heard with no tile waiting",
                   "A strike with no camera result behind it: a missed camera detection, or a stray strike."),
    "empty_strike": ("The striker fired with nothing under it",
                     "The tile sensor and the camera disagree about whether a tile was there."),
    "no_ack": ("The UNO Q is not acknowledging",
               "See the gantry finding; finals are queuing up."),
    "outbox_overflow": ("The UNO Q queue overflowed and a tile was dropped",
                        "The gantry is slower than the line. Slow the belt or find out why it is not taking tiles."),
    "unoq_rejected": ("The UNO Q rejected a final grade",
                      "It refused a message; compare the grade/destination it was sent against what it accepts."),
    "bad_message": ("Unreadable messages on the bus",
                    "Something is publishing malformed JSON or an unknown command."),
    "tap_input": ("A tap could not start a capture",
                  "The acoustic capture was not ready when the strike arrived; check the microphone health."),
    "camera_silent": ("The camera went silent", "See the camera finding."),
}


def _match(station: str, detail: str) -> tuple:
    for pattern, title, meaning, checks in _RULES.get(station, ()):
        if re.search(pattern, detail or "", re.IGNORECASE):
            return title, meaning, list(checks)
    return (f"{station} needs attention", "The station reports a problem the master has no specific advice for.",
            ["Read the detail text, then the journal of that station's service."])


def since_ts(events: list[dict], station: str, level: str) -> Optional[float]:
    """When the station entered `level`, if that is still its latest recorded
    level. Events are only logged on a change, so the last one is the entry."""
    evs = sorted((e for e in events if e["station"] == station), key=lambda e: e["ts"])
    return evs[-1]["ts"] if evs and evs[-1]["level"] == level else None


def diagnose(stations: list[dict], events: list[dict], alarms: list[dict], now: float,
             master_connected: Optional[bool] = None) -> list[dict]:
    """Active findings, most severe first. When the Pi station is silent every
    other station reads offline as a consequence, so only the Pi is reported."""
    findings: list[dict] = []

    def add(station, level, detail, source):
        title, meaning, checks = _match(station, detail)
        findings.append({"station": station, "level": level, "title": title, "meaning": meaning,
                         "checks": checks, "detail": detail, "since": since_ts(events, station, level),
                         "source": source})

    if master_connected is False:
        add("master", "fail", "not connected to the broker", "health")
        # Everything else is unknowable from here.
        return findings

    by_id = {s["id"]: s for s in stations}
    pi = by_id.get("pi")
    pi_silent = pi is not None and pi["level"] == "offline"
    if pi_silent:
        add("pi", "offline", pi["detail"], "health")
    else:
        bad = [s for s in stations if s["level"] in ("fail", "warn", "offline")]
        causes = [s for s in bad if s["id"] != "pi"]
        for s in bad:
            # The Pi's own "fault" is just the sum of its stations' faults; with a
            # specific cause on the list it adds nothing.
            if s["id"] == "pi" and s["level"] == "fail" and any(c["level"] == "fail" for c in causes):
                continue
            add(s["id"], s["level"], s["detail"], "health")

    counts: dict[str, list] = {}
    for a in alarms:
        if a.get("level", "alarm") == "alarm" and (a.get("ts") or 0) >= now - RECENT_ALARM_S:
            counts.setdefault(a.get("kind") or "unknown", []).append(a)
    for kind, items in counts.items():
        title, advice = _ALARM_ADVICE.get(kind, (f"Alarm: {kind}", "No specific advice for this alarm kind."))
        last = max(items, key=lambda a: a.get("ts") or 0)
        findings.append({"station": "alarm", "level": "warn", "title": f"{title} ({len(items)} in 10 min)",
                         "meaning": advice, "checks": [], "detail": last.get("message"),
                         "since": min(a["ts"] for a in items), "source": "alarm", "kind": kind})
    findings.sort(key=lambda f: (SEVERITY.get(f["level"], 9), f["station"]))
    return findings


def timeline(events: list[dict], current: dict[str, str], now: float, window_s: float) -> dict:
    """Per station: coloured segments across the window and the share of KNOWN
    time spent ok. Time before the history first saw a station is `unknown` and
    is excluded from uptime, so a freshly started master is not read as 100 %."""
    start = now - window_s
    stations = sorted(set(current) | {e["station"] for e in events})
    out = {}
    for st in stations:
        evs = sorted((e for e in events if e["station"] == st), key=lambda e: e["ts"])
        # level at the window start
        level, t = "unknown", start
        segs: list[dict] = []
        for e in evs:
            if e["ts"] <= start:
                level = e["level"]
                continue
            if e["ts"] > now:
                break
            if e["ts"] > t:
                segs.append({"from": t, "to": e["ts"], "level": level})
            level, t = e["level"], e["ts"]
        if now > t:
            segs.append({"from": t, "to": now, "level": level})
        known = sum(s["to"] - s["from"] for s in segs if s["level"] != "unknown")
        ok = sum(s["to"] - s["from"] for s in segs if s["level"] == "ok")
        out[st] = {"segments": [{"from": round(s["from"], 1), "to": round(s["to"], 1), "level": s["level"]} for s in segs],
                   "uptime": None if known <= 0 else round(ok / known, 4),
                   "known_s": round(known, 1),
                   "changes": sum(1 for e in evs if start < e["ts"] <= now)}
    return {"start": start, "end": now, "stations": out}
