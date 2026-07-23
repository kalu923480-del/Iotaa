"""
Iota Bot — Iota Wheel 🎡 (Iota luck system)

A fortune wheel you spin for a chance at coins / gems. Different from
/slots (which is Telegram's native dice slot machine) — this is a
weighted prize wheel with a 1-hour cooldown, and you can burn 💎 gems to
skip the cooldown and spin again immediately.

NOTE: every user-facing string below is wrapped with sc_all() (Iota-style
smallcaps) so the output matches the rest of the bot.

  /wheel            spin (respects 1h cooldown)
  /wheel gems       pay 💎 gems to skip the cooldown and spin now

Prize segments (value, kind, weight):
  🍀 +250 coins        (common)
  🪙 +500 coins        (common)
  ✨ +1000 coins
  🔥 +1500 coins
  💎 +5 gems
  😴 Nothing           (common)
  💥 Bust -300 coins   (lose some)
  🏆 JACKPOT +5000     (rare)

Weights are tuned so the EXPECTED payout is slightly negative (a small
house edge), so spinning is fun but can't be farmed for infinite coins.
"""
import logging
import random
import time

from telegram import Update
from telegram.ext import ContextTypes

from utils.mongo_db import (
    ensure_user, get_user, add_balance, deduct_balance, add_gems, deduct_gems,
    update_user,
)
from utils.helpers import mention, fmt
from utils.system_gate import games_gate
from utils.fonts import sc_all
from utils.game_ui import send_gif_result
from utils.game_art import send_game_art as _send_art, render_wheel as _render_wheel
from utils.game_rules import bot_game_disabled_msg, PVP_GAMES_HINT

logger = logging.getLogger(__name__)

COOLDOWN = 3600            # 1 hour between free spins
GEM_SKIP_COST = 10        # gems to skip the cooldown

# (label, value, kind, weight)   kind in {coins, gems, none}
_SEGMENTS = [
    ("🍀 +250",    250,   "coins", 22),
    ("🪙 +500",    500,   "coins", 20),
    ("✨ +1000",   1000,  "coins", 14),
    ("🔥 +1500",   1500,  "coins", 6),
    ("💎 +5 Gems", 5,     "gems",  8),
    ("😴 Nothing", 0,     "none",  18),
    ("💥 Bust",   -300,   "coins", 14),
    ("🏆 JACKPOT", 5000,  "coins", 4),
]
_WEIGHTS = [s[3] for s in _SEGMENTS]


@games_gate
async def wheel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        bot_game_disabled_msg("Iota Wheel (casino spin)") + PVP_GAMES_HINT
    )
