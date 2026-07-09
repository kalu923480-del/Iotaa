#!/usr/bin/env bash
# Iota Bot — Render (or any PaaS) start script.
#
# 1. If a real config.py does not exist (e.g. on Render, where the
#    gitignored local config.py is NOT in the cloned repo), generate one
#    from the committed, secret-free config_template.py. All secrets come
#    from environment variables set in the deploy dashboard.
# 2. Launch the bot. Its post_init() starts the Ludo Mini App web server on
#    $PORT (WEBAPP_PORT), so a single Web Service process serves BOTH the
#    Telegram long-poll bot AND the Ludo Mini App over HTTPS.
set -eu

cd "$(dirname "$0")"

if [ ! -f config.py ]; then
  echo "ℹ️  config.py not found — generating from config_template.py (env-driven)."
  cp config_template.py config.py
fi

# Render exposes the listening port as $PORT; the Ludo server reads it via
# config.WEBAPP_PORT. Make sure it is exported for good measure.
export PORT="${PORT:-8080}"

echo "🚀 Starting Iota Bot..."
exec python3 bot.py
