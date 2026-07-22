#!/usr/bin/env bash
# IotaXMusic — Render / Docker start script
set -euo pipefail

cd "$(dirname "$0")"

export PATH="${HOME}/.deno/bin:/usr/local/bin:${PATH}"
export PORT="${PORT:-8080}"
export HEALTH_PORT="${PORT}"

# Optional: materialize cookies from COOKIE_URL before boot
if [ -n "${COOKIE_URL:-}" ]; then
  echo "🍪 COOKIE_URL set — cookies will be fetched on bot start"
fi

echo "🚀 Starting Iota Music Bot on PORT=${PORT}..."
exec python3 -m IotaXMedia
