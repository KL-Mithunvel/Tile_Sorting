#!/usr/bin/env bash
#
# Install the acoustic + decision station as a systemd service on the Raspberry
# Pi 5 that also runs the camera node, plus the MQTT broker both talk through.
#
#   ./acoustic_node/deploy/install_pi.sh
#
# NOT YET RUN — written 2026-10-11 against documented Raspberry Pi OS package
# names; no Pi has run it. Read it before trusting it. See
# documents/programming/station_coordination.md for the bring-up checklist.

set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NODE_DIR="$(dirname "$DEPLOY_DIR")"
PYTHON_DIR="$NODE_DIR/python"
REPO_ROOT="$(dirname "$NODE_DIR")"
RUN_USER="${SUDO_USER:-$USER}"
SERVICE_NAME="tile-acoustic"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

echo "Repo root : $REPO_ROOT"
echo "Run as    : $RUN_USER"
echo

# apt, not pip: same reasoning as camera_node/deploy/install_pi.sh (the camera
# service uses the system interpreter, and so does this one).
echo "==> Installing system packages"
sudo apt update
sudo apt install -y \
    python3-numpy \
    python3-flask \
    python3-yaml \
    python3-sounddevice \
    libportaudio2 \
    python3-paho-mqtt \
    mosquitto \
    mosquitto-clients

# The broker. Bookworm's mosquitto 2.x refuses remote connections unless told
# otherwise, which is what we want while only this Pi and the UNO Q (a known
# address on an isolated LAN, network_plan.md) need it: listen on the LAN,
# anonymous, because the LAN has no uplink (documents/electrical/network_plan.md:
# no auth is an accepted Phase 1 risk). Do NOT use this config if the router ever gets a WAN.
CONF=/etc/mosquitto/conf.d/tile-station.conf
if [ ! -f "$CONF" ]; then
    echo "==> Writing $CONF (LAN listener, anonymous — isolated LAN only)"
    sudo tee "$CONF" > /dev/null <<'CONFEOF'
listener 1883
allow_anonymous true
persistence true
CONFEOF
fi
sudo systemctl enable --now mosquitto
sudo systemctl restart mosquitto

echo
echo "==> Verifying imports with the system interpreter"
/usr/bin/python3 - <<'PYCHECK'
import sys
missing = []
for mod in ("numpy", "flask", "yaml", "sounddevice", "paho.mqtt.client", "sqlite3"):
    try:
        __import__(mod)
    except Exception as exc:  # sounddevice raises OSError without PortAudio
        missing.append(f"{mod} ({exc})")
if missing:
    print("MISSING:", ", ".join(missing), file=sys.stderr)
    sys.exit(1)
print("imports OK")
PYCHECK

mkdir -p "$REPO_ROOT/data/station"

# The user running the service needs the audio group for the USB microphone.
sudo usermod -aG audio "$RUN_USER"

echo
echo "==> Installing systemd unit at $UNIT_PATH"
sed \
    -e "s|@PYTHON_DIR@|$PYTHON_DIR|g" \
    -e "s|@RUN_USER@|$RUN_USER|g" \
    "$DEPLOY_DIR/tile-acoustic.service" \
    | sudo tee "$UNIT_PATH" > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

echo
echo "Installed. Before starting:"
echo "  1. Find the microphone:   cd $PYTHON_DIR && /usr/bin/python3 -m acoustic.live_station --list-devices"
echo "     (pin it by NAME in acoustic/config.yaml audio.device — indices change between boots)"
echo "  2. In camera_node/python/camera/config.yaml set  mqtt.enabled: true"
echo "  3. sudo systemctl start $SERVICE_NAME ; journalctl -u $SERVICE_NAME -f"
echo "  4. Run view:  http://\$(hostname -I | awk '{print \$1}'):5001/"
echo
echo "Log out and back in (or reboot) once so the audio group applies to interactive use."
