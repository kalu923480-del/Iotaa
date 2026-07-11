#!/usr/bin/env bash
# Iota Bot — Render start command.
#   1. Generate config.py from the env-driven template when it is missing
#      (config.py is gitignored, so it is never in the cloned repo).
#   2. Ensure the quote-renderer fonts exist locally, downloading any that
#      are missing (cached under iota/assets/fonts/).
#   3. Launch the bot.
set -e

cd "$(dirname "$0")"

if [ ! -f iota/config.py ]; then
  cp iota/config_template.py iota/config.py
  echo "✅ config.py generated from config_template.py"
fi

echo "🔤 Ensuring quote-renderer fonts..."
python3 - <<'PY'
import sys
sys.path.insert(0, "iota")
from utils.font_manager import ensure_fonts
ensure_fonts()
print("✅ fonts ready")
PY

exec python3 iota/bot.py
