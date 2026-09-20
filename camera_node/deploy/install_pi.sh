#!/usr/bin/env bash
#
# Install the camera inspection node as a systemd service on a Raspberry Pi 5
# running Raspberry Pi OS Desktop (64-bit).
#
#   Automation_Architecture.md §5.2 — camera station = Raspberry Pi 5 (16 GB),
#   decided 2026-09-20. This replaces the Arduino App Lab deploy path that
#   camera_node/ used to share with acoustic_node/ (tools/uno_q/push.bat).
#
# Usage, on the Pi, from a checkout of this repo:
#
#     chmod +x camera_node/deploy/install_pi.sh
#     ./camera_node/deploy/install_pi.sh
#
# NOT YET RUN — written 2026-09-20 against the documented Raspberry Pi OS
# package names; no Pi has been set up yet. Read it before trusting it.

set -euo pipefail

# ---------------------------------------------------------------------------
# Work out where we are and who we are, rather than hardcoding paths/users.
# ---------------------------------------------------------------------------
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NODE_DIR="$(dirname "$DEPLOY_DIR")"
PYTHON_DIR="$NODE_DIR/python"
REPO_ROOT="$(dirname "$NODE_DIR")"

RUN_USER="${SUDO_USER:-$USER}"
SERVICE_NAME="tile-camera"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

echo "Repo root : $REPO_ROOT"
echo "Node dir  : $NODE_DIR"
echo "Run as    : $RUN_USER"
echo

# ---------------------------------------------------------------------------
# Dependencies.
#
# Deliberately apt, not pip-into-a-venv. python3-picamera2 is built against
# the system numpy and the system libcamera; installing a second numpy/OpenCV
# into an isolated venv is the usual way to get an ABI mismatch that only
# shows up at the first capture_array() call. Everything this node imports
# (numpy, cv2, flask, yaml) is packaged by Raspberry Pi OS, so use those.
#
# If you are running a USB webcam only and don't need picamera2, a plain venv
# with deploy/requirements-pi.txt also works — see deploy/README.md.
# ---------------------------------------------------------------------------
echo "==> Installing system packages"
sudo apt update
sudo apt install -y \
    python3-picamera2 \
    python3-opencv \
    python3-flask \
    python3-yaml \
    python3-numpy

echo
echo "==> Verifying imports with the system interpreter"
/usr/bin/python3 - <<'PYCHECK'
import sys
missing = []
for mod in ("cv2", "numpy", "flask", "yaml"):
    try:
        __import__(mod)
    except ImportError as exc:
        missing.append(f"{mod} ({exc})")
try:
    import picamera2  # noqa: F401
    print("picamera2: available (CSI camera module supported)")
except ImportError:
    print("picamera2: NOT available — USB webcam only (set camera.backend: \"usb\")")
if missing:
    print("MISSING:", ", ".join(missing), file=sys.stderr)
    sys.exit(1)
print("core imports OK")
PYCHECK

# ---------------------------------------------------------------------------
# Snapshot directory. config.yaml's capture_snapshots.output_dir is relative
# to the repo root (see camera/snapshot.py resolve_output_dir()), and data/ is
# gitignored, so it won't exist on a fresh clone.
# ---------------------------------------------------------------------------
echo
echo "==> Creating snapshot directory"
mkdir -p "$REPO_ROOT/data/camera_captures"

# ---------------------------------------------------------------------------
# systemd unit, generated from the template so the paths and user are this
# machine's, not something hardcoded in a checked-in file.
# ---------------------------------------------------------------------------
echo
echo "==> Installing systemd unit at $UNIT_PATH"
sed \
    -e "s|@PYTHON_DIR@|$PYTHON_DIR|g" \
    -e "s|@RUN_USER@|$RUN_USER|g" \
    "$DEPLOY_DIR/tile-camera.service" \
    | sudo tee "$UNIT_PATH" > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

echo
echo "Installed. Useful commands:"
echo "  sudo systemctl start   $SERVICE_NAME"
echo "  sudo systemctl status  $SERVICE_NAME"
echo "  sudo systemctl restart $SERVICE_NAME"
echo "  journalctl -u $SERVICE_NAME -f"
echo
echo "Dashboard will be at http://\$(hostname -I | awk '{print \$1}'):5000/"
echo "Before starting it, check the camera is seen:"
echo "  cd $PYTHON_DIR && /usr/bin/python3 -m camera.live_dashboard --list-devices"
