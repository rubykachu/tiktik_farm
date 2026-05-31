#!/usr/bin/env bash
# restart.sh — recreate containers, start cloudflared tunnel, auto-update HOST in .env
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
TUNNEL_LOG=$(mktemp)
CF_PID=""

cleanup() {
    rm -f "$TUNNEL_LOG"
    [ -n "$CF_PID" ] && kill "$CF_PID" 2>/dev/null || true
}
trap cleanup EXIT
trap 'echo ""; echo "Stopped."; exit 0' INT TERM

echo "▶ Recreating containers..."
make -C "$SCRIPT_DIR" recreate

echo "▶ Starting cloudflared tunnel..."
cloudflared tunnel --url http://localhost:3000 >"$TUNNEL_LOG" 2>&1 &
CF_PID=$!

echo -n "  Waiting for tunnel URL"
TUNNEL_URL=""
for i in $(seq 1 30); do
    TUNNEL_URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1 || true)
    [ -n "$TUNNEL_URL" ] && break
    echo -n "."
    sleep 1
done
echo ""

if [ -z "$TUNNEL_URL" ]; then
    echo "✗ Could not detect tunnel URL after 30s. cloudflared output:"
    cat "$TUNNEL_LOG"
    exit 1
fi

echo "▶ Tunnel: $TUNNEL_URL"

# Update or append HOST in .env
touch "$ENV_FILE"
if grep -q "^HOST=" "$ENV_FILE"; then
    sed -i.bak "s|^HOST=.*|HOST=$TUNNEL_URL|" "$ENV_FILE" && rm "${ENV_FILE}.bak"
else
    echo "HOST=$TUNNEL_URL" >> "$ENV_FILE"
fi

# `docker compose restart` does NOT re-read .env — it reuses the running
# container's environment. Recreate so the new HOST value is injected.
echo "▶ Recreating api to apply new HOST..."
make -C "$SCRIPT_DIR" recreate-api

echo "✓ Ready at $TUNNEL_URL"

# Keep cloudflared running in foreground
wait "$CF_PID"
