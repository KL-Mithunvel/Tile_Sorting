#!/usr/bin/env bash
#
# Make the Pi's attached screen show the station run view at login.
#
#   ./acoustic_node/deploy/kiosk/install_kiosk.sh
#
# Raspberry Pi OS Bookworm Desktop uses the labwc compositor on a Pi 5 (Wayland)
# and Wayfire / X11 on other setups, and they read different autostart files.
# This writes the entry for each of them plus the generic XDG one, so whichever
# the desktop uses picks it up. Remove them with --uninstall.
#
# NOT YET RUN on a Pi — written 2026-10-11 from documentation. Which of the
# three entries a given image actually honours is exactly the part to verify.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$DIR/start_kiosk.sh"
chmod +x "$SCRIPT"

LABWC="$HOME/.config/labwc/autostart"
LXDE="$HOME/.config/lxsession/LXDE-pi/autostart"
XDG="$HOME/.config/autostart/tile-kiosk.desktop"
MARK="# tile-kiosk"

if [ "${1:-}" = "--uninstall" ]; then
    for f in "$LABWC" "$LXDE"; do
        [ -f "$f" ] && sed -i "/$MARK/d" "$f"
    done
    rm -f "$XDG"
    echo "Kiosk autostart removed."
    exit 0
fi

# labwc (Pi 5, Bookworm default)
mkdir -p "$(dirname "$LABWC")"
grep -q "$MARK" "$LABWC" 2>/dev/null || echo "\"$SCRIPT\" & $MARK" >> "$LABWC"

# LXDE / X11
mkdir -p "$(dirname "$LXDE")"
grep -q "$MARK" "$LXDE" 2>/dev/null || echo "@\"$SCRIPT\" $MARK" >> "$LXDE"

# Generic XDG autostart
mkdir -p "$(dirname "$XDG")"
cat > "$XDG" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Tile station kiosk
Exec="$SCRIPT"
X-GNOME-Autostart-enabled=true
DESKTOP

echo "Kiosk autostart installed. Log out and back in (or reboot) to see it."
echo "Test now with:  $SCRIPT"
echo "Exit kiosk mode with Alt+F4; screen blanking is left to raspi-config (Display Options)."
