#!/usr/bin/env bash
# Launch Brave with zappa-lite proxy configured
# Usage: ./brave-zappa.sh [URL]

set -euo pipefail

ZAPPA_PORT="${ZAPPA_PORT:-8080}"

# Find Brave binary
BRAVE=""
for candidate in brave-browser brave-browser-stable brave google-chrome-stable; do
    if command -v "$candidate" &>/dev/null; then
        BRAVE="$candidate"
        break
    fi
done

if [ -z "$BRAVE" ]; then
    echo "ERROR: Brave browser not found."
    echo "Install it: https://brave.com/download/"
    echo "Or set BRAVE=<path> in your environment."
    exit 1
fi

echo "============================================================"
echo "  zappa-lite: Launching Brave with de-enshittification proxy"
echo "  Brave: $BRAVE"
echo "  Proxy: http://127.0.0.1:$ZAPPA_PORT"
echo "============================================================"
echo ""
echo "  IMPORTANT: Make sure zappa-lite is running first!"
echo "  Run: ./run.sh (in another terminal)"
echo ""
echo "  First time? Browse to http://mitm.it to install the"
echo "  mitmproxy CA certificate if you haven't already."
echo "============================================================"
echo ""

# Launch Brave with proxy and a separate profile (so it doesn't affect normal browsing)
# --proxy-server routes all traffic through zappa-lite
# --ignore-certificate-errors lets mitmproxy's custom cert work (HTTPS interception)
# --user-data-dir creates a separate Brave profile for proxied browsing
ZAPPA_PROFILE="$HOME/.zappa-lite/brave-profile"
mkdir -p "$ZAPPA_PROFILE"

exec "$BRAVE" \
    --proxy-server="http://127.0.0.1:$ZAPPA_PORT" \
    --user-data-dir="$ZAPPA_PROFILE" \
    --ignore-certificate-errors \
    --disable-extensions-except="" \
    --no-first-run \
    --no-default-browser-check \
    "${1:-https://news.ycombinator.com}"