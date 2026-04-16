#!/usr/bin/env bash
# zappa-lite launcher for Brave browser
# Starts mitmproxy + launches Brave with proxy configured and certs trusted

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load .env files
for envfile in .env ~/.hermes/.env; do
    if [ -f "$envfile" ]; then
        set -a
        source "$envfile"
        set +a
    fi
done

# Defaults
ZAPPA_PORT="${ZAPPA_PORT:-8080}"
ZAPPA_MODEL="${ZAPPA_MODEL:-google/gemini-2.0-flash-001}"

echo "============================================================"
echo "  zappa-lite: AI-powered internet de-enshittification proxy"
echo "  Model: $ZAPPA_MODEL"
echo "  Listening: http://127.0.0.1:$ZAPPA_PORT"
echo "============================================================"
echo ""

# Check for mitmproxy CA cert — install if missing
CERT_DIR="$HOME/.mitmproxy"
CERT_FILE="$CERT_DIR/mitmproxy-ca-cert.pem"
SYSTEM_CERT="/usr/local/share/ca-certificates/mitmproxy.crt"

if [ ! -f "$CERT_FILE" ]; then
    echo "First run: generating mitmproxy CA certificate..."
    mkdir -p "$CERT_DIR"
    uv run mitmdump --help > /dev/null 2>&1 || true
    # cert is generated on first mitmproxy run
fi

# Install CA cert to system trust store (for Brave/Chromium)
if [ -f "$CERT_FILE" ] && [ ! -f "$SYSTEM_CERT" ]; then
    echo "Installing mitmproxy CA cert to system trust store..."
    sudo mkdir -p /usr/local/share/ca-certificates
    sudo cp "$CERT_FILE" "$SYSTEM_CERT"
    sudo update-ca-certificates 2>/dev/null || true
    echo "System cert installed."
fi

# Also trust cert for Chromium/Brave specifically (NSS database)
NSS_DB="$HOME/.pki/nssdb"
if [ -f "$CERT_FILE" ]; then
    echo "Installing cert into Chromium/Brave NSS database..."
    mkdir -p "$NSS_DB"
    certutil -d "sql:$NSS_DB" -A -t "C,," -n "mitmproxy" -i "$CERT_FILE" 2>/dev/null || {
        echo "certutil not found. Install nss-utils:"
        echo "  sudo apt install libnss3-tools"
        echo ""
        echo "Alternative: browse to http://mitm.it through the proxy and install manually."
    }
fi

echo ""
echo "Starting mitmproxy on port $ZAPPA_PORT..."
echo "Press Ctrl+C to stop."
echo ""

# Start mitmproxy in background
exec uv run mitmdump \
    --listen-port "$ZAPPA_PORT" \
    --set console_eventlog_verbosity=info \
    --set termlog_verbosity=debug \
    -s zappa.py