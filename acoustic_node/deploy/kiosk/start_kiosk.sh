#!/usr/bin/env bash
#
# Open the station's run view fullscreen in Chromium (kiosk mode).
# Started at desktop login by install_kiosk.sh; can also be run by hand.
#
# NOT YET RUN on a Pi — written 2026-10-11 from documentation.

set -u
URL="${TILE_KIOSK_URL:-http://localhost:5001/}"

# Chromium's binary is `chromium-browser` on Raspberry Pi OS Bookworm, `chromium`
# elsewhere.
BROWSER=""
for candidate in chromium-browser chromium; do
    if command -v "$candidate" >/dev/null 2>&1; then
        BROWSER="$candidate"
        break
    fi
done
if [ -z "$BROWSER" ]; then
    echo "No Chromium found (tried chromium-browser, chromium)." >&2
    exit 1
fi

# Wait for the station GUI instead of showing Chromium's "can't connect" page
# that never recovers on its own. The page itself shows a red NO DATA banner if
# the station stops answering later, so this only has to cover boot order.
for _ in $(seq 1 120); do
    if curl -fsS -o /dev/null --max-time 2 "$URL"; then
        break
    fi
    sleep 1
done

exec "$BROWSER" \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-pinch \
    --overscroll-history-navigation=0 \
    --incognito \
    "$URL"
