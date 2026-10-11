#!/usr/bin/env bash
#
# One command to bring the whole line up IN ORDER, and to stop it.
#
#   ./acoustic_node/deploy/system.sh start            # run ON the Pi
#   ./acoustic_node/deploy/system.sh stop
#   ./acoustic_node/deploy/system.sh status           # = diagnose.sh
#
#   start options
#     --no-unoq        the UNO Q is not up yet (bench): do not wait for it
#     --with-nano      also require the Nano's serial link to answer
#     --belt PCT       belt speed to set once everything is verified (default: leave the belt alone)
#     --wait SEC       how long to wait for every node to answer (default 40)
#     --sudo-stdin     read the sudo password from the first line of stdin
#
# What "in sync" means here: the line is HELD (every tile goes to the manual bin,
# the belt is not started) from the moment the station comes up until EVERY
# required node has answered a diagnostic ping and says it is healthy. Only then
# is the hold released and the belt set, in one step. If anything does not
# answer, the line stays held and the script says which node and what to do:
# a half-started line never sorts tiles.
#
# Start order: broker -> station (decision) -> camera -> verify -> GO.
# The UNO Q is not started from here (it is a different machine running its own
# app); it is *verified*. Stopping sets the belt to 0, inhibits the striker and
# holds the line; --services also stops the camera and station services.
#
# NOT YET RUN on a Pi — written 2026-10-12.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ACTION="${1:-}"; shift || true

WITH_UNOQ=1; WITH_NANO=0; BELT=""; WAIT=40; SUDO_STDIN=0; STOP_SERVICES=0
while [ $# -gt 0 ]; do
    case "$1" in
        --no-unoq) WITH_UNOQ=0; shift ;;
        --with-nano) WITH_NANO=1; shift ;;
        --belt) BELT="$2"; shift 2 ;;
        --wait) WAIT="$2"; shift 2 ;;
        --sudo-stdin) SUDO_STDIN=1; shift ;;
        --services) STOP_SERVICES=1; shift ;;
        -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

PY=""
# A candidate must actually run: on Windows `python3` is often a Microsoft Store
# stub that prints an install message instead of running anything.
for candidate in "${TILE_PY:-}" python3 python py; do
    [ -n "$candidate" ] || continue
    if "$candidate" -c "import json" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
[ -n "$PY" ] || { echo "python3 is required" >&2; exit 2; }
PROBE=("$PY" "$HERE/mqtt_probe.py")

SUDO_PW=""
[ "$SUDO_STDIN" -eq 1 ] && read -r SUDO_PW
run_sudo() {
    if [ -n "$SUDO_PW" ]; then printf '%s\n' "$SUDO_PW" | sudo -S -p '' "$@"; else sudo -n "$@"; fi
}

if [ -t 1 ]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; N=$'\033[0m'; else G=; R=; Y=; N=; fi
step() { printf '\n%s== %s%s\n' "$Y" "$1" "$N"; }
ok()   { printf '  %sOK%s    %s\n' "$G" "$N" "$1"; }
no()   { printf '  %sFAIL%s  %s\n' "$R" "$N" "$1"; }

send_cmd()  { "${PROBE[@]}" pub station/cmd "$1" --host localhost >/dev/null 2>&1; }
hold_all()  { send_cmd "{\"cmd\":\"hold_all\",\"enabled\":$1}"; }
set_belt()  { "${PROBE[@]}" pub station/belt "{\"percent\":$1}" --host localhost >/dev/null 2>&1; }
set_strike(){ "${PROBE[@]}" pub station/strike "{\"enabled\":$1}" --host localhost >/dev/null 2>&1; }

wait_http() {  # wait_http URL SECONDS
    for _ in $(seq 1 "$2"); do
        curl -fsS -o /dev/null --max-time 1 "$1" && return 0
        sleep 1
    done
    return 1
}

required_nodes() {
    local n="station camera"
    [ "$WITH_UNOQ" -eq 1 ] && n="$n gantry"
    [ "$WITH_NANO" -eq 1 ] && n="$n nano"
    echo "$n"
}

# Prints "name status" for every required node, status ok | unwell | missing.
node_states() {
    local nodes; nodes="$(required_nodes)"
    local out; out="$("${PROBE[@]}" ping --host localhost --wait 2 --expect $nodes 2>/dev/null)" || out='{"nodes":{}}'
    printf '%s' "$out" | "$PY" -c '
import json, sys
nodes = json.load(sys.stdin).get("nodes", {})
for name in sys.argv[1:]:
    n = nodes.get(name)
    print(name, "missing" if n is None else ("ok" if n["ok"] else "unwell"))
' $nodes
}

case "$ACTION" in
    status) exec "$HERE/diagnose.sh" ;;

    start)
        step "1/5 broker"
        systemctl is-active --quiet mosquitto || run_sudo systemctl start mosquitto
        if (exec 3<>/dev/tcp/localhost/1883) 2>/dev/null; then ok "mosquitto is listening on :1883"
        else no "the broker is not listening — run install_pi.sh first"; exit 1; fi

        step "2/5 station (decision service) — started HELD"
        run_sudo systemctl restart tile-acoustic || { no "could not start tile-acoustic (installed?)"; exit 1; }
        wait_http http://localhost:5001/api/station 30 || { no "station GUI did not come up (journalctl -u tile-acoustic)"; exit 1; }
        # Hold before anything else can happen: a freshly started station must not
        # sort on its own until the whole system has been verified.
        held=0
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            hold_all true
            sleep 0.5
            if curl -fsS --max-time 2 http://localhost:5001/api/station | "$PY" -c 'import json,sys; sys.exit(0 if json.load(sys.stdin)["settings"]["hold_all"] else 1)'; then held=1; break; fi
        done
        [ "$held" -eq 1 ] && ok "station is up and HOLDING every tile" || { no "could not put the station on hold; not continuing"; exit 1; }
        [ "$WITH_NANO" -eq 1 ] && set_strike false || true

        step "3/5 camera"
        run_sudo systemctl restart tile-camera || { no "could not start tile-camera (installed?)"; exit 1; }
        wait_http http://localhost:5000/api/status 30 && ok "camera GUI is up" || no "camera GUI did not come up (journalctl -u tile-camera)"

        step "4/5 verify every required node answers (${WAIT} s)"
        deadline=$(( $(date +%s) + WAIT ))
        all_ok=0
        while :; do
            states="$(node_states)"
            if ! printf '%s\n' "$states" | grep -qv ' ok$'; then all_ok=1; break; fi
            [ "$(date +%s)" -ge "$deadline" ] && break
            sleep 2
        done
        printf '%s\n' "$states" | while read -r name st; do
            [ "$st" = ok ] && ok "$name" || no "$name is $st"
        done
        if [ "$all_ok" -ne 1 ]; then
            echo
            echo "The line stays HELD and the belt is not started. Nothing was sorted."
            echo "Run ./diagnose.sh for what to fix, then ./system.sh start again."
            exit 1
        fi

        step "5/5 GO"
        [ "$WITH_NANO" -eq 1 ] && set_strike true
        hold_all false
        [ -n "$BELT" ] && set_belt "$BELT" && ok "belt set to ${BELT} %"
        sleep 1
        curl -fsS --max-time 2 http://localhost:5001/api/station | "$PY" -c '
import json, sys
d = json.load(sys.stdin)
print("  station state:", d["state"], "-", d["state_detail"], "| hold_all:", d["settings"]["hold_all"], "| sound vote:", d["settings"]["vote_enabled"])
'
        printf '\n%sSYSTEM GO%s  run view: http://%s:5001/\n' "$G" "$N" "$(hostname -I 2>/dev/null | awk '{print $1}')"
        ;;

    stop)
        step "stopping the line"
        set_belt 0 && ok "belt set to 0 %"
        set_strike false && ok "striker inhibited"
        hold_all true && ok "station holding every tile"
        if [ "$STOP_SERVICES" -eq 1 ]; then
            run_sudo systemctl stop tile-camera tile-acoustic && ok "camera and station services stopped"
        fi
        echo "(The belt also stops by itself if the Pi goes silent: that is the Nano's watchdog.)"
        ;;

    *)
        echo "usage: $0 {start|stop|status} [options]   (try --help)" >&2
        exit 2
        ;;
esac
