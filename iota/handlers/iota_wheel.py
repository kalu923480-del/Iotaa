"""
Iota Bot — Iota Wheel 🎡 (Iota luck system)

A fortune wheel you spin for a chance at coins / gems. Different from
/slots (which is Telegram's native dice slot machine) — this is a
weighted prize wheel with a 1-hour cooldown, and you can burn 💎 gems to
skip the cooldown and spin again immediately.

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
    u = update.effective_user
    msg = update.effective_message
    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)

    now = int(time.time())
    last = d.get("wheel_last", 0)
    remaining = COOLDOWN - (now - last)

    skip = bool(context.args) and context.args[0].lower() in ("gems", "gem", "skip", "💎")

    if remaining > 0 and not skip:
        m, s = divmod(remaining, 60)
        await msg.reply_html(
            f"⏳ <b>Iota Wheel</b> thoda rest le raha hai...\n"
            f"Agla free spin: <b>{m}m {s}s</b> baad.\n\n"
            f"Ya fir abhi spin karne ke liye: <code>/wheel gems</code> "
            f"({GEM_SKIP_COST} 💎 use honge)."
        )
        return

    if skip:
        gems = d.get("gems", 0)
        if gems < GEM_SKIP_COST:
            await msg.reply_html(
                f"❌ Cooldown skip karne ke liye {GEM_SKIP_COST} 💎 chahiye! "
                f"Tere paas: {fmt(gems)} 💎"
            )
            return
        await deduct_gems(u.id, GEM_SKIP_COST)

    # Spin!
    idx = random.choices(range(len(_SEGMENTS)), weights=_WEIGHTS, k=1)[0]
    label, value, kind, _ = _SEGMENTS[idx]

    if kind == "coins":
        if value >= 0:
            await add_balance(u.id, value)
            result = f"💰 <b>+{fmt(value)} coins</b> jeete!"
        else:
            have = (await get_user(u.id)).get("balance", 0)
            lose = min(have, -value)
            await deduct_balance(u.id, lose)
            result = f"💥 Bust! <b>-{fmt(lose)} coins</b> gaye 🥲"
    elif kind == "gems":
        await add_gems(u.id, value)
        result = f"💎 <b>+{value} gems</b> jeete!"
    else:
        result = "😴 Kuch nahi mila... try again later!"

    await update_user(u.id, wheel_last=now)

    d2 = await get_user(u.id)
    bal = fmt(d2.get("balance", 0))
    gem = fmt(d2.get("gems", 0))
    await msg.reply_html(
        f"🎡 <b>Iota Wheel spin!</b>\n\n"
        f"{label}\n{result}\n\n"
        f"💰 Coins: {bal}\n💎 Gems: {gem}\n"
        f"⏳ Agla free spin 1h baad."
    )
