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
    python3-pip \
    libportaudio2 \
    python3-paho-mqtt \
    mosquitto \
    mosquitto-clients

# sounddevice has no apt package on Debian 13 (trixie) - found 2026-10-15, the
# first time this script ran. It is a small pure-Python wrapper over the
# libportaudio2 installed above, so pip it into the service user's ~/.local
# (--user keeps it out of the system dirs; --break-system-packages is the PEP 668
# opt-in). The service runs as that user with the system interpreter, which picks
# ~/.local up.
echo "==> Installing sounddevice (pip, --user)"
sudo -u "$RUN_USER" /usr/bin/python3 -m pip install --user --break-system-packages sounddevice

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

# Time. The control LAN is isolated (no internet NTP), so nodes would drift
# apart. The Pi is the always-on hub (it already hosts the broker), so it serves
# time to the LAN; the UNO Q and the master PC sync to it (see
# documents/electrical/network_plan.md §6). `local stratum 10` makes it hand out
# its own clock when it has no upstream source, `makestep` lets it correct a
# large error at start-up instead of slewing for hours, and fake-hwclock restores
# the last-known time after a power cut so logs do not restart from 1970 (a Pi 5
# with an RTC battery keeps real time; this is the fallback).
# Installing chrony replaces systemd-timesyncd.
LAN_CIDR="${LAN_CIDR:-192.168.1.0/24}"
echo "==> Time server (chrony) for $LAN_CIDR"
sudo apt install -y chrony fake-hwclock || echo "WARNING: chrony/fake-hwclock install failed; clocks will drift"
TIMECONF=/etc/chrony/conf.d/tile-station.conf
if [ -d /etc/chrony/conf.d ] && [ ! -f "$TIMECONF" ]; then
    sudo tee "$TIMECONF" > /dev/null <<TIMEEOF
# Written by acoustic_node/deploy/install_pi.sh. Isolated LAN: serve our own clock.
allow $LAN_CIDR
local stratum 10
makestep 1.0 3
rtcsync
TIMEEOF
    sudo systemctl enable chrony >/dev/null 2>&1 || true
    sudo systemctl restart chrony || echo "WARNING: chrony did not restart; check: systemctl status chrony"
fi

echo
echo "==> Verifying imports with the system interpreter"
# As the service user: sounddevice lives in that user's ~/.local, not root's.
sudo -u "$RUN_USER" /usr/bin/python3 - <<'PYCHECK'
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

# Make the camera service forward tiles to the broker. A systemd drop-in (not an
# edit of camera/config.yaml) so a later code push, which overwrites config.yaml,
# cannot silently switch the hand-off off.
CAM_UNIT=/etc/systemd/system/tile-camera.service
if [ -f "$CAM_UNIT" ]; then
    CAM_EXEC="$(grep '^ExecStart=' "$CAM_UNIT" | head -1 | cut -d= -f2-)"
    case "$CAM_EXEC" in
        *--mqtt*) echo "tile-camera already forwards tiles (--mqtt)." ;;
        *)
            echo "==> tile-camera: adding the --mqtt drop-in"
            sudo mkdir -p /etc/systemd/system/tile-camera.service.d
            printf '[Service]\nExecStart=\nExecStart=%s --mqtt\n' "$CAM_EXEC" \
                | sudo tee /etc/systemd/system/tile-camera.service.d/mqtt.conf > /dev/null
            sudo systemctl daemon-reload
            ;;
    esac
else
    echo "NOTE: tile-camera is not installed yet (camera_node/deploy/install_pi.sh)."
    echo "      Run this script again after installing it, so it can enable --mqtt."
fi

chmod +x "$DEPLOY_DIR"/*.sh "$DEPLOY_DIR"/mqtt_probe.py 2>/dev/null || true

echo
echo "Installed. Next, in this order:"
echo "  1. Software self-test (no mic/camera/UNO Q needed):  $DEPLOY_DIR/selftest.sh"
echo "  2. Find the microphone:   cd $PYTHON_DIR && /usr/bin/python3 -m acoustic.live_station --list-devices"
echo "     (pin it by NAME in acoustic/config.yaml audio.device — indices change between boots)"
echo "  3. Bring the whole line up in order:  $DEPLOY_DIR/system.sh start --no-unoq   (drop --no-unoq once the UNO Q feed runs)"
echo "  4. Is everything answering?           $DEPLOY_DIR/diagnose.sh"
echo "  5. Run view:  http://\$(hostname -I | awk '{print \$1}'):5001/"
echo
echo "Log out and back in (or reboot) once so the audio group applies to interactive use."
