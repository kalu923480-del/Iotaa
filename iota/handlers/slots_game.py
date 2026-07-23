"""
Iota Bot — /slots (Casino Slot Machine)

Uses Telegram's own NATIVE animated slot machine emoji (🎰) via
bot.send_dice() instead of building a fake spinning-reel UI in text.
This means:
  - The spin animation is Telegram's real, polished native animation —
    no custom graphics needed.
  - The outcome is generated SERVER-SIDE by Telegram, not by the bot —
    so results are provably fair; Iota can't rig outcomes even if
    someone wanted her to, because she doesn't decide them.

HOW TELEGRAM'S SLOT VALUE WORKS (Bot API, documented behaviour):
Telegram returns a `value` from 1-64 for a 🎰 dice. Subtracting 1 gives
a number 0-63, which splits into three base-4 "digits" (each 0-3) — one
per reel — that map to: 0=bar, 1=grapes, 2=lemon, 3=seven. value=64
(all three digits = 3) means all sevens = the jackpot.

PAYOUT TABLE (rebalanced for a healthy house edge — see note below):
  🎰 777 (jackpot)      → 15x bet   (1/64 chance)
  Three of any kind      → 4x bet   (3/64 chance)
  Two matching (any)       → 1x bet (get your bet back, 36/64 chance)
  No match                   → lose bet (24/64 chance)

🔴 IMPORTANT — WHY "two matching" IS 1x, NOT MORE:
A naive first draft paid 1.5x for any two matching symbols. Since 36 of
the 64 possible outcomes have at least two matching symbols, that alone
would have given an expected return of ~123% per bet — a NEGATIVE house
edge, meaning the average player would profit infinitely over time and
could farm unlimited coins, breaking the whole economy. This table was
verified by simulating all 64 possible outcomes: it gives a ~1.6% house
edge (in line with how real slot machines are typically tuned), while
still returning players' bets on a very common outcome so the game
doesn't feel punishing.
"""
import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import DiceEmoji

from utils.mongo_db import ensure_user, get_user, add_balance, deduct_balance
from utils.helpers import mention, fmt
from utils.safe_html import safe_html
from utils.system_gate import games_gate
from utils.game_ui import send_gif_result
from utils.game_art import send_game_art as _send_art, render_slots as _render_slots
from utils.game_rules import bot_game_disabled_msg, PVP_GAMES_HINT

logger = logging.getLogger(__name__)

_SYMBOLS = ["🅱️", "🍇", "🍋", "7️⃣"]  # index 0-3, matches the bit-decoding below
MIN_BET = 50
MAX_BET = 5000


def _decode_slot_value(value: int) -> tuple[str, str, str]:
    """Decodes Telegram's 1-64 slot dice value into 3 reel symbols."""
    v = value - 1  # 0-63
    reel1 = v % 4
    reel2 = (v // 4) % 4
    reel3 = (v // 16) % 4
    return _SYMBOLS[reel1], _SYMBOLS[reel2], _SYMBOLS[reel3]


def _payout_multiplier(reels: tuple[str, str, str]) -> float:
    r1, r2, r3 = reels
    if r1 == r2 == r3 == "7️⃣":
        return 15.0  # jackpot
    if r1 == r2 == r3:
        return 4.0   # three of a kind
    if r1 == r2 or r2 == r3 or r1 == r3:
        return 1.0   # two matching — bet returned, break-even
    return 0.0       # no match


@games_gate
async def slots_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        bot_game_disabled_msg("Slot Machine (casino)") + PVP_GAMES_HINT
    )
