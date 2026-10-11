#!/usr/bin/env bash
#
# Station software self-test, on the Pi, against the REAL broker.
#
#   ./acoustic_node/deploy/selftest.sh [seconds]                 # default 30
#   echo PASSWORD | ./acoustic_node/deploy/selftest.sh --sudo-stdin 30
#
# What it proves: broker + paho + the station service + the tile ledger + the
# final grade + the SQLite record + acknowledgements, end to end, with NO
# microphone, camera or UNO Q — a synthetic microphone and fake
# camera/striker/UNO Q stand in (development/fake_station_peers.py).
# What it does NOT prove: that the real microphone hears a strike, that the
# camera grades real tiles, that the UNO Q acknowledges, or the Nano.
#
# It stops the real tile-acoustic service for the duration (the fake peers
# share its topics, so two stations would both answer) and restarts it on exit.
# Results go to data/station/selftest.db, never to the line's tiles.db.
#
# NOT YET RUN on a Pi — written 2026-10-12 from documentation.

set -u

SUDO_STDIN=0
SECS=30
for a in "$@"; do
    case "$a" in
        --sudo-stdin) SUDO_STDIN=1 ;;
        ''|*[!0-9]*) echo "usage: $0 [--sudo-stdin] [seconds]" >&2; exit 2 ;;
        *) SECS="$a" ;;
    esac
done

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NODE_DIR="$(dirname "$DEPLOY_DIR")"
REPO_ROOT="$(dirname "$NODE_DIR")"
PY_DIR="$NODE_DIR/python"
PEERS="$REPO_ROOT/development/fake_station_peers.py"
API="http://localhost:5001"
LOG_ST=/tmp/tile-selftest-station.log
LOG_PE=/tmp/tile-selftest-peers.log

SUDO_PW=""
if [ "$SUDO_STDIN" -eq 1 ]; then
    read -r SUDO_PW
fi

run_sudo() {
    if [ -n "$SUDO_PW" ]; then
        printf '%s\n' "$SUDO_PW" | sudo -S -p '' "$@"
    else
        sudo -n "$@"
    fi
}

fail=0
ok()   { printf '  PASS  %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1"; fail=1; }

echo "=== preconditions"
if ! systemctl is-active --quiet mosquitto; then
    bad "mosquitto is not running (sudo systemctl start mosquitto; run install_pi.sh first)"
    exit 1
fi
ok "mosquitto is running"
[ -f "$PEERS" ] || { bad "missing $PEERS (push it with station_push.bat)"; exit 1; }
/usr/bin/python3 -c "import paho.mqtt.client, flask, numpy, yaml" 2>/dev/null \
    && ok "python imports (paho, flask, numpy, yaml)" || { bad "python imports failed"; exit 1; }

WAS_ACTIVE=0
if systemctl is-active --quiet tile-acoustic; then
    WAS_ACTIVE=1
    run_sudo systemctl stop tile-acoustic || { bad "could not stop tile-acoustic"; exit 1; }
    ok "stopped tile-acoustic for the test (will be restarted)"
fi

STATION_PID=""
PEERS_PID=""
cleanup() {
    [ -n "$PEERS_PID" ] && kill "$PEERS_PID" 2>/dev/null
    [ -n "$STATION_PID" ] && kill "$STATION_PID" 2>/dev/null
    wait 2>/dev/null
    if [ "$WAS_ACTIVE" -eq 1 ]; then
        run_sudo systemctl start tile-acoustic && echo "  restarted tile-acoustic"
    fi
}
trap cleanup EXIT

rm -f "$REPO_ROOT/data/station/selftest.db"
mkdir -p "$REPO_ROOT/data/station"

echo
echo "=== starting the station (synthetic mic, real broker)"
( cd "$PY_DIR" && exec /usr/bin/python3 -m acoustic.live_station --synthetic-mic >"$LOG_ST" 2>&1 ) &
STATION_PID=$!
for _ in $(seq 1 30); do
    curl -fsS -o /dev/null --max-time 1 "$API/api/station" && break
    sleep 1
done
if curl -fsS -o /dev/null --max-time 2 "$API/api/station"; then
    ok "station GUI is answering on :5001"
else
    bad "station did not start; last log lines:"
    tail -15 "$LOG_ST" | sed 's/^/        /'
    exit 1
fi

echo
echo "=== running fake camera + striker + UNO Q for ${SECS} s"
/usr/bin/python3 "$PEERS" --host localhost --interval 3 --transit 1 >"$LOG_PE" 2>&1 &
PEERS_PID=$!
sleep "$SECS"

echo
echo "=== checks"
curl -fsS --max-time 3 "$API/api/station" | python3 -c '
import json, sys
d = json.load(sys.stdin)
fails = 0
def check(ok, text):
    global fails
    print("  %s  %s" % ("PASS" if ok else "FAIL", text))
    if not ok:
        fails += 1
c, h = d["counters"], d["health"]
finals = sum(d["tally"].values())
check(c["tiles"] >= 3, "camera results received and queued (%d tiles)" % c["tiles"])
check(c["taps"] >= 3, "strike events produced clips and were bound (%d taps)" % c["taps"])
check(finals >= 3, "final grades computed (%d: %s)" % (finals, d["tally"]))
check(h["broker"]["level"] == "ok", "broker connected")
check(h["camera"]["level"] == "ok", "camera heartbeat seen (%s)" % h["camera"]["detail"])
check(h["mic"]["level"] == "ok", "microphone input alive (synthetic)")
check(h["unoq"]["level"] == "ok", "finals acknowledged (%s)" % h["unoq"]["detail"])
check(c["orphan_taps"] == 0, "no orphan taps")
check(c["timeouts"] == 0, "no tile timed out waiting for a tap")
check(d["outbox_depth"] <= 1, "delivery queue is not backing up (depth %d)" % d["outbox_depth"])
check(d["transit"]["n"] >= 3, "transit time measured (median %s s)" % d["transit"]["median_s"])
rows = d["recent"]
check(any(r["delivery"] == "acked" for r in rows), "the record shows tiles acked")
sys.exit(1 if fails else 0)
' || fail=1

echo
if [ "$fail" -eq 0 ]; then
    echo "SELF-TEST PASSED"
else
    echo "SELF-TEST FAILED  (station log: $LOG_ST   peers log: $LOG_PE)"
    echo "--- station log tail"; tail -15 "$LOG_ST" | sed 's/^/    /'
fi
exit "$fail"
