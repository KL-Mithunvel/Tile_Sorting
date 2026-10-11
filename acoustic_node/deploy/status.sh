#!/usr/bin/env bash
#
# Read-only health check of the station on the Pi: services, ports, the station's
# own view of itself, the hardware it depends on. Changes nothing.
#
#   ./acoustic_node/deploy/status.sh
#
# NOT YET RUN on a Pi — written 2026-10-12 from documentation.

set -u
API="${TILE_STATION_URL:-http://localhost:5001}"

echo "=== services"
for s in mosquitto tile-camera tile-acoustic; do
    printf '  %-14s %s\n' "$s" "$(systemctl is-active "$s" 2>&1)"
done

echo
echo "=== listening ports (1883 broker, 5000 camera GUI, 5001 station GUI)"
ss -ltn 2>/dev/null | awk 'NR==1 || /:(1883|5000|5001)[[:space:]]/' | sed 's/^/  /'

echo
echo "=== station (what the station says about itself)"
if body="$(curl -fsS --max-time 3 "$API/api/station" 2>/dev/null)"; then
    printf '%s' "$body" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("  state     :", d["state"], "-", d["state_detail"])
for k, v in d["health"].items():
    print("  %-10s: %-5s %s" % (k, v["level"], v["detail"]))
print("  tally     :", d["tally"])
c = d["counters"]
print("  counters  :", {k: c[k] for k in sorted(c)})
t = d["transit"]
print("  transit   : n=%s median=%s p95=%s max=%s suggested max_wait_s=%s" % (
    t["n"], t["median_s"], t["p95_s"], t["max_s"], t["suggested_max_wait_s"]))
print("  settings  :", d["settings"])
alarms = [a for a in d["alarms"] if a["level"] == "alarm"]
for a in alarms[:5]:
    print("  ALARM     : [%s] %s" % (a["kind"], a["message"]))
if not alarms:
    print("  alarms    : none")
'
else
    echo "  station GUI not answering on $API (is tile-acoustic running?)"
fi

echo
echo "=== broker: retained station topics"
if command -v mosquitto_sub >/dev/null 2>&1; then
    timeout 4 mosquitto_sub -h localhost -t 'station/#' -v -W 3 2>/dev/null | cut -c1-200 | sed 's/^/  /' \
        || true
else
    echo "  mosquitto_sub not installed (apt install mosquitto-clients)"
fi

echo
echo "=== audio inputs (the USB microphone should be listed)"
arecord -l 2>&1 | sed 's/^/  /' | head -12

echo
echo "=== USB topology (microphone and camera on DIFFERENT controllers, away from each other)"
lsusb -t 2>&1 | sed 's/^/  /' | head -25

echo
echo "=== serial ports (the Nano, by id)"
ls -l /dev/serial/by-id 2>&1 | sed 's/^/  /' | head -8

echo
echo "=== thermal (throttled=0x0 is clean)"
vcgencmd measure_temp 2>/dev/null | sed 's/^/  /'
vcgencmd get_throttled 2>/dev/null | sed 's/^/  /'
