#!/usr/bin/env bash
#
# Is everything on the network there, and answering the way it should?
# Read-only: it changes nothing and cannot cause a pick (it uses a dedicated
# diagnostic ping, never a tile).
#
#   ./acoustic_node/deploy/diagnose.sh                 # run ON the Pi
#   ./acoustic_node/deploy/diagnose.sh --pi 192.168.1.11     # from a laptop (Git Bash / WSL)
#
# Options
#   --pi HOST      the Pi / broker host            (default localhost)
#   --unoq HOST    the UNO Q                       (default 192.168.1.15)
#   --master HOST  the master PC                   (default 192.168.1.10)
#   --router HOST  the control-LAN router          (default 192.168.1.1)
#   --require LIST nodes that MUST answer, comma separated
#                  (default station,camera,gantry; add nano / master when they exist)
#   --wait SEC     how long to wait for answers    (default 4)
#
# Layers, so a failure says WHERE it stopped:
#   1. network   can the device be pinged?
#   2. ports     is the service's port open?
#   3. answers   did the RUNNING PROGRAM reply (station/diag/ping -> pong)?
#   4. stations  what do the station and camera GUIs say about themselves?
#
# Exit status: 0 = every required node answered ok, 1 = something is wrong.
#
# NOT YET RUN against the real Pi / UNO Q — written 2026-10-12.

set -u

PI=localhost
UNOQ=192.168.1.15
MASTER=192.168.1.10
ROUTER=192.168.1.1
REQUIRE="station,camera,gantry"
WAIT=4

while [ $# -gt 0 ]; do
    case "$1" in
        --pi) PI="$2"; shift 2 ;;
        --unoq) UNOQ="$2"; shift 2 ;;
        --master) MASTER="$2"; shift 2 ;;
        --router) ROUTER="$2"; shift 2 ;;
        --require) REQUIRE="$2"; shift 2 ;;
        --wait) WAIT="$2"; shift 2 ;;
        -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
        *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
    esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY=""
# A candidate must actually run: on Windows `python3` is often a Microsoft Store
# stub that prints an install message instead of running anything.
for candidate in "${TILE_PY:-}" python3 python py; do
    [ -n "$candidate" ] || continue
    if "$candidate" -c "import json" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
[ -n "$PY" ] || { echo "python3 is required" >&2; exit 2; }

if [ -t 1 ]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; D=$'\033[2m'; N=$'\033[0m'; else G=; R=; Y=; D=; N=; fi
FAILS=0
HINTS=()
pass() { printf '  %sPASS%s  %s\n' "$G" "$N" "$1"; }
warn() { printf '  %sWARN%s  %s\n' "$Y" "$N" "$1"; }
fail() { printf '  %sFAIL%s  %s\n' "$R" "$N" "$1"; FAILS=$((FAILS + 1)); [ -n "${2:-}" ] && HINTS+=("$2"); }
is_required() { case ",$REQUIRE," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }

up() {  # up HOST -> 0 if it answers a ping (Linux and Windows ping flags)
    if [ "$1" = localhost ] || [ "$1" = 127.0.0.1 ]; then return 0; fi
    ping -c 1 -W 2 "$1" >/dev/null 2>&1 || ping -n 1 -w 2000 "$1" >/dev/null 2>&1
}
port_open() {  # port_open HOST PORT
    (exec 3<>"/dev/tcp/$1/$2") >/dev/null 2>&1 && return 0
    timeout 3 bash -c "exec 3<>/dev/tcp/$1/$2" >/dev/null 2>&1
}

echo "=== 1. network"
for pair in "router:$ROUTER" "pi:$PI" "unoq:$UNOQ" "master:$MASTER"; do
    name="${pair%%:*}"; host="${pair#*:}"
    label="$name ($host)"
    if up "$host"; then
        pass "$label answers ping"
    else
        case "$name" in
            pi)    fail "$label does not answer" "Pi unreachable: is the Ethernet cable in, is the Pi powered, is this machine on 192.168.1.x?" ;;
            unoq)  is_required gantry && fail "$label does not answer" "UNO Q unreachable: power, USB-C Ethernet hub, DHCP reservation .15" || warn "$label does not answer (not required)" ;;
            master) is_required master && fail "$label does not answer" "master PC unreachable" || warn "$label does not answer (optional, the line runs without it)" ;;
            *)     warn "$label does not answer ping" ;;
        esac
    fi
done

echo
echo "=== 2. ports"
check_port() {  # check_port HOST PORT LABEL REQUIRED(0/1) HINT
    if port_open "$1" "$2"; then
        pass "$3 ($1:$2) is open"
    elif [ "$4" = 1 ]; then
        fail "$3 ($1:$2) is closed" "$5"
    else
        warn "$3 ($1:$2) is closed (optional)"
    fi
}
check_port "$PI" 1883 "MQTT broker" 1 "broker down: sudo systemctl start mosquitto (acoustic_node/deploy/install_pi.sh installs it)"
check_port "$PI" 5000 "camera GUI" 1 "camera service down: sudo systemctl start tile-camera, then journalctl -u tile-camera"
check_port "$PI" 5001 "station GUI" 1 "station service down: sudo systemctl start tile-acoustic, then journalctl -u tile-acoustic"
check_port "$UNOQ" 22 "UNO Q ssh" 0 ""
check_port "$MASTER" 8000 "master GUI" 0 ""

echo
echo "=== 3. answers (the running programs, through the broker)"
EXPECT_ARGS=$(echo "$REQUIRE" | tr ',' ' ')
OUT="$("$PY" "$HERE/mqtt_probe.py" ping --host "$PI" --wait "$WAIT" --expect $EXPECT_ARGS 2>/dev/null)"
RC=$?
if [ "$RC" -ne 0 ] || [ -z "$OUT" ]; then
    fail "could not talk to the broker at $PI:1883 (paho-mqtt installed? broker up?)" "check the broker first; nothing below can be trusted"
    NODES_JSON='{"broker": false, "nodes": {}}'
else
    NODES_JSON="$OUT"
fi
ANSWERS="$(printf '%s' "$NODES_JSON" | "$PY" -c '
import json, sys
d = json.load(sys.stdin)
nodes = d.get("nodes", {})
for name in ("station", "camera", "gantry", "nano", "master"):
    n = nodes.get(name)
    if n is None:
        print("%s|missing||" % name)
    else:
        det = n.get("detail") or {}
        print("%s|%s|%s|%s" % (name, "ok" if n["ok"] else "unwell", n["latency_ms"], json.dumps(det, separators=(",", ":"))[:170]))
')"
while IFS='|' read -r name status lat detail; do
    [ -z "$name" ] && continue
    case "$status" in
        ok)      pass "$name answered in ${lat} ms  ${D}${detail}${N}" ;;
        unwell)  if is_required "$name"; then fail "$name answered but reports a problem  ${detail}" "$name is up but unhealthy — see its detail above"
                 else warn "$name answered but reports a problem  ${detail}"; fi ;;
        missing) if is_required "$name"; then
                     case "$name" in
                         station) fail "station did not answer" "tile-acoustic is not running or not connected to the broker" ;;
                         camera)  fail "camera did not answer" "tile-camera is not running, or mqtt.enabled is off (install_pi.sh adds a --mqtt drop-in)" ;;
                         gantry)  fail "gantry (UNO Q) did not answer" "on the UNO Q: python -m pick_place.tile_feed --host <Pi>  (the feed consumer is not running)" ;;
                         nano)    fail "nano did not answer" "the serial bridge is off or the Nano is not connected (station.nano.enabled)" ;;
                         *)       fail "$name did not answer" "start it" ;;
                     esac
                 else warn "$name did not answer (not required)"; fi ;;
    esac
done <<< "$ANSWERS"

echo
echo "=== 4. stations' own view"
if body="$(curl -fsS --max-time 3 "http://$PI:5001/api/station" 2>/dev/null)"; then
    printf '%s' "$body" | "$PY" -c '
import json, sys
d = json.load(sys.stdin)
print("  station : %s - %s" % (d["state"], d["state_detail"]))
print("  tiles   : %s   finals by grade: %s" % (d["counters"]["tiles"], d["tally"]))
bad = [a for a in d["alarms"] if a["level"] == "alarm"]
for a in bad[:4]:
    print("  alarm   : [%s] %s" % (a["kind"], a["message"]))
t = d["transit"]
if t["n"]:
    print("  transit : n=%s median=%ss p95=%ss -> suggested max_wait_s %s" % (t["n"], t["median_s"], t["p95_s"], t["suggested_max_wait_s"]))
'
else
    warn "station GUI not readable"
fi
if body="$(curl -fsS --max-time 3 "http://$PI:5000/api/status" 2>/dev/null)"; then
    printf '%s' "$body" | "$PY" -c '
import json, sys
d = json.load(sys.stdin)
s = d["source"]
print("  camera  : source=%s (%s) state=%s tiles=%s errors=%s" % (s.get("label"), s.get("status"), d["station_state"], d["tile_count"], d["errors"]["count"]))
'
else
    warn "camera GUI not readable"
fi

echo
if [ "$FAILS" -eq 0 ]; then
    printf '%sDIAGNOSIS: all required nodes are up and answering.%s\n' "$G" "$N"
    exit 0
fi
printf '%sDIAGNOSIS: %d problem(s).%s What to do:\n' "$R" "$FAILS" "$N"
i=1
for h in "${HINTS[@]}"; do printf '  %d. %s\n' "$i" "$h"; i=$((i + 1)); done
exit 1
