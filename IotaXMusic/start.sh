#!/usr/bin/env bash
# IotaXMusic — Render / Docker start script (24/7 ready)
set -euo pipefail

cd "$(dirname "$0")"

export PATH="${HOME}/.deno/bin:/usr/local/bin:${PATH:-/usr/local/bin:/usr/bin:/bin}"
export PORT="${PORT:-8080}"
export HEALTH_PORT="${PORT}"
export PYTHONUNBUFFERED=1

# Render auto-injects RENDER_EXTERNAL_URL on Web Services.
# Fallback: user can set KEEPALIVE_URL after first deploy.
if [ -n "${RENDER_EXTERNAL_URL:-}" ]; then
  echo "🌐 RENDER_EXTERNAL_URL=${RENDER_EXTERNAL_URL}"
elif [ -n "${KEEPALIVE_URL:-}" ]; then
  echo "🌐 KEEPALIVE_URL=${KEEPALIVE_URL}"
else
  echo "ℹ️  No public URL yet — set KEEPALIVE_URL after first deploy for 24/7 self-ping"
fi

if [ -n "${COOKIE_URL:-}" ]; then
  echo "🍪 COOKIE_URL set — cookies will be fetched on bot start"
elif [ -f /etc/secrets/cookies.txt ]; then
  echo "🍪 Found Render Secret File /etc/secrets/cookies.txt"
elif [ -n "${COOKIE_FILE:-}" ] && [ -f "${COOKIE_FILE}" ]; then
  echo "🍪 COOKIE_FILE=${COOKIE_FILE}"
else
  echo "⚠️  No cookies configured — YouTube download may fail (set COOKIE_URL or Secret File)"
fi

echo "🚀 Starting Iota Music Bot on PORT=${PORT}..."
exec python3 -m IotaXMedia
