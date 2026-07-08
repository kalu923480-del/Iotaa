"""
Iota — Gems Economy System (spend & convert your 💎 gems)
─────────────────────────────────────────────────────────
Gems are earned by buying them (premium users, via /fgems). This module
gives gems a real use:

  /gems2coins <n>  → convert N gems into coins (1 gem = GEMS_PRICE_COINS)
  /gemstore         → list gems-exclusive items
  /buygem <id>      → buy an item with gems (gems-only — coins can't be used)

GEMS-EXCLUSIVE ITEMS
  premium  → 3-month Premium, bought ONLY with gems
  diamond  → 💎 Diamond Badge (permanent, shown on profile)
  legend   → 🏆 Legend Badge (permanent, shown on profile)
  title    → 🏷️ Custom Tag/Title (gems-only; you choose the text)
  vip      → 👑 VIP role for 30 days (shown on profile)

Every purchase validates the gem balance and applies the effect atomically.
Nothing here can crash the command — bad input returns a clear message.
"""
import logging
import time

from telegram import Update
from telegram.ext import ContextTypes

from utils.mongo_db import ensure_user, get_user, update_user, add_balance
from utils.helpers import mention, fmt
from config import GEMS_PRICE_COINS

logger = logging.getLogger(__name__)

# ── Catalog (gems-only) ───────────────────────────────────────────────────────
GEM_ITEMS = {
    "diamond": {
        "name": "💎 Diamond Badge",
        "cost": 15,
        "desc": "Permanent exclusive 💎 Diamond badge shown on your profile.",
    },
    "legend": {
        "name": "🏆 Legend Badge",
        "cost": 40,
        "desc": "Permanent legendary 🏆 Legend badge shown on your profile.",
    },
    "title": {
        "name": "🏷️ Custom Title / Tag",
        "cost": 25,
        "desc": "Set your OWN custom tag (gems-only). Usage: /buygem title Your Tag",
    },
    "vip": {
        "name": "👑 VIP (30 Days)",
        "cost": 50,
        "desc": "30-day 👑 VIP role badge shown on your profile.",
    },
}

TITLE_MAX_LEN = 20


# ── Shared display helper (used by /pfp and /profile) ──────────────────────────

def owned_perks_line(d: dict) -> str:
    """One-line summary of a user's gems-exclusive perks, or '' if none."""
    parts = []
    badge = d.get("badge")
    if badge:
        parts.append(f"🎖️ {badge}")
    ct = d.get("custom_title")
    if ct:
        parts.append(f"🏷️ {ct}")
    vip_until = d.get("vip_until", 0) or 0
    if vip_until and vip_until > int(time.time()):
        parts.append("👑 VIP")
    return "  ".join(parts)


# ── Commands ───────────────────────────────────────────────────────────────────

async def gems2coins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)
    args = context.args
    if not args:
        await update.message.reply_html(
            f"💎➡️💰 <b>Convert Gems to Coins</b>\n\n"
            f"Rate: 1 💎 = {fmt(GEMS_PRICE_COINS)} coins\n"
            f"You have: <b>{d.get('gems', 0)}</b> 💎\n\n"
            f"<b>Usage:</b> <code>/gems2coins 5</code>"
        )
        return
    try:
        amt = int(args[0])
    except ValueError:
        await update.message.reply_html("❌ Numbers only! <code>/gems2coins 5</code>")
        return
    if amt <= 0:
        await update.message.reply_html("❌ Amount must be positive!")
        return
    if d.get("gems", 0) < amt:
        await update.message.reply_html(
            f"❌ Not enough gems! You have <b>{d.get('gems', 0)}</b> 💎."
        )
        return
    coins = amt * GEMS_PRICE_COINS
    await update_user(u.id, gems=d["gems"] - amt)
    await add_balance(u.id, coins)
    await update.message.reply_html(
        f"💎➡️💰 Converted <b>{amt}</b> 💎 into <b>{fmt(coins)}</b> coins!\n"
        f"💎 Gems left: <b>{d['gems'] - amt}</b>"
    )


async def gemstore_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)
    text = (
        f"💎 <b>Iota Gems Store</b>\n\n"
        f"Your gems: <b>{d.get('gems', 0)}</b> 💎\n"
        f"1 💎 = {fmt(GEMS_PRICE_COINS)} coins (use /gems2coins)\n\n"
        f"🛍️ <b>Gems-Exclusive Items</b> (coins can't buy these!)\n"
    )
    for iid, item in GEM_ITEMS.items():
        text += f"• <b>{item['name']}</b> — {item['cost']} 💎\n  └ {item['desc']}\n"
    text += "\n<b>Buy:</b> <code>/buygem &lt;id&gt;</code>  (e.g. /buygem diamond)"
    await update.message.reply_html(text)


async def buygem_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)
    args = context.args or []
    if not args or args[0].lower() not in GEM_ITEMS:
        await update.message.reply_html(
            "🛍️ <b>Gems Store</b>\n\n"
            "Usage: <code>/buygem &lt;id&gt;</code>\n"
            "IDs: " + ", ".join(f"<code>{i}</code>" for i in GEM_ITEMS) +
            "\n\nSee /gemstore for details."
        )
        return

    iid = args[0].lower()
    item = GEM_ITEMS[iid]
    cost = item["cost"]

    if d.get("gems", 0) < cost:
        await update.message.reply_html(
            f"❌ Not enough gems for <b>{item['name']}</b>!\n"
            f"Need <b>{cost}</b> 💎, you have <b>{d.get('gems', 0)}</b> 💎."
        )
        return

    # Apply the effect for each item type.
    if iid == "diamond":
        await update_user(u.id, gems=d["gems"] - cost, badge="💎 Diamond")
        await update.message.reply_html(
            f"🎖️ {mention(u)} earned the <b>💎 Diamond</b> badge!\n"
            f"Remaining gems: <b>{d['gems'] - cost}</b> 💎"
        )
        return

    if iid == "legend":
        await update_user(u.id, gems=d["gems"] - cost, badge="🏆 Legend")
        await update.message.reply_html(
            f"🎖️ {mention(u)} earned the <b>🏆 Legend</b> badge!\n"
            f"Remaining gems: <b>{d['gems'] - cost}</b> 💎"
        )
        return

    if iid == "title":
        title_text = " ".join(args[1:]).strip()
        if not title_text:
            await update.message.reply_html(
                "🏷️ <b>Usage:</b> <code>/buygem title Your Custom Tag</code>"
            )
            return
        if len(title_text) > TITLE_MAX_LEN:
            title_text = title_text[:TITLE_MAX_LEN]
        await update_user(u.id, gems=d["gems"] - cost, custom_title=title_text)
        await update.message.reply_html(
            f"🏷️ {mention(u)} set their tag to <b>{title_text}</b>!\n"
            f"Remaining gems: <b>{d['gems'] - cost}</b> 💎"
        )
        return

    if iid == "vip":
        now = int(time.time())
        await update_user(
            u.id, gems=d["gems"] - cost,
            vip_until=now + 30 * 86400,
        )
        await update.message.reply_html(
            f"👑 {mention(u)} is now <b>VIP</b> for 30 days!\n"
            f"Remaining gems: <b>{d['gems'] - cost}</b> 💎"
        )
        return
