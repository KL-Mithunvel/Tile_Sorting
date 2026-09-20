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
#     ./camera_node/deploy/install_pi.sh --with-grading   # + ONNX grade model
#
# NOT YET RUN — written 2026-09-20 against the documented Raspberry Pi OS
# package names; no Pi has been set up yet. Read it before trusting it.

set -euo pipefail

# --with-grading also installs onnxruntime, for config.yaml's optional
# `grading_model` block. Off by default because the station grades tiles from
# crack/corner geometry without it, and because onnxruntime is the one
# dependency here that apt does not package (see the section below).
WITH_GRADING=0
for arg in "$@"; do
    case "$arg" in
        --with-grading) WITH_GRADING=1 ;;
        -h|--help)
            echo "Usage: $0 [--with-grading]"
            exit 0
            ;;
        *)
            echo "Unknown option: $arg (try --help)" >&2
            exit 1
            ;;
    esac
done

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
    python3-numpy \
    python3-pil

# ---------------------------------------------------------------------------
# Optional: onnxruntime, for config.yaml's `grading_model` block.
#
# This is the one dependency apt does not package, so it has to come from pip.
# On Bookworm the system interpreter is PEP 668 "externally managed", hence
# --break-system-packages: we are deliberately adding ONE pure-addition wheel
# to the system interpreter the service runs, rather than building a venv that
# would break picamera2's numpy/libcamera ABI (see the note above).
#
# Non-fatal on purpose. `grading_model.enabled` defaults to false and
# grading_model.from_config() returns None when onnxruntime is absent, so a Pi
# where this step fails still inspects tiles — it just has no second opinion.
#
# UNVERIFIED: no Pi has run this. If the wheel is unavailable for this
# Python/arch, prefer piwheels or a venv over fighting pip here.
# ---------------------------------------------------------------------------
if [ "$WITH_GRADING" -eq 1 ]; then
    echo
    echo "==> Installing onnxruntime (optional grading model)"
    if ! sudo /usr/bin/python3 -m pip install --break-system-packages onnxruntime; then
        echo "WARNING: onnxruntime install failed. The station will still run," >&2
        echo "         with the ONNX grade model unavailable." >&2
    fi
fi

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
# Both are needed only by camera/grading_model.py, which is optional; report
# them rather than failing, so their absence is visible but not blocking.
try:
    import PIL  # noqa: F401
    print("Pillow: available (required by the ONNX grade model's preprocessing)")
except ImportError:
    print("Pillow: NOT available — the ONNX grade model cannot run")
try:
    import onnxruntime  # noqa: F401
    print("onnxruntime: available (set grading_model.enabled: true to use it)")
except ImportError:
    print("onnxruntime: NOT available — grading model off "
          "(re-run with --with-grading to install it)")
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
