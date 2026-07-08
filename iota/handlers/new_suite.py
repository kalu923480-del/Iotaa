"""
Iota — Extra Features Suite (User / Admin / Owner)
──────────────────────────────────────────────────
Self-contained commands added on top of the core bot. Every Telegram API
call is wrapped so a transient error (missing permission, bad input) is
reported clearly instead of crashing the command.

USERS
  /pick <a> <b> <c>   → randomly pick one of the given options
  /rand [min] [max]   → random integer (default 1-100)
  /uptime             → how long the bot has been running

ADMINS (group only)
  /gstats             → group statistics (members, admins, bots, ...)
  /adminlist          → list the group's administrators
  /chatid             → show this chat's id (handy for /leave)

OWNER
  /leave <chat_id>    → make the bot leave a chat
  /setbotname <name>  → change the bot's display name
"""
import logging
import time
import random

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from utils.permissions import owner_only, admin_only, group_only
from utils.helpers import mention_id

logger = logging.getLogger(__name__)

# Captured at import time (which happens during bot startup) → bot uptime.
BOT_START_TIME = time.time()


# ── Users ─────────────────────────────────────────────────────────────────────

async def pick_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if len(args) < 1:
        await update.effective_message.reply_html(
            "🎯 <b>Usage:</b> <code>/pick pizza burger pasta</code>\n"
            "I'll randomly pick one for you."
        )
        return
    choice = random.choice(args)
    await update.effective_message.reply_html(
        f"🎯 I pick: <b>{choice}</b>"
    )


async def rand_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    try:
        if len(args) == 0:
            lo, hi = 1, 100
        elif len(args) == 1:
            lo, hi = 1, int(args[0])
        else:
            lo, hi = int(args[0]), int(args[1])
    except ValueError:
        await update.effective_message.reply_html(
            "❌ Numbers only! <code>/rand 1 50</code>"
        )
        return
    if lo > hi:
        lo, hi = hi, lo
    if hi - lo > 10_000_000:
        await update.effective_message.reply_html(
            "❌ Range too large (max 10,000,000)."
        )
        return
    await update.effective_message.reply_html(
        f"🎲 Random number: <b>{random.randint(lo, hi)}</b> "
        f"<i>(between {lo} and {hi})</i>"
    )


async def uptime_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    secs = int(time.time() - BOT_START_TIME)
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    parts.append(f"{s}s")
    await update.effective_message.reply_html(
        f"⏱️ <b>Bot Uptime:</b> {' '.join(parts)}"
    )


# ── Admins ────────────────────────────────────────────────────────────────────

@group_only
@admin_only
async def gstats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    try:
        full = await context.bot.get_chat(chat.id)
        member_count = await context.bot.get_chat_member_count(chat.id)
        admins = await context.bot.get_chat_administrators(chat.id)
    except TelegramError as e:
        await msg.reply_html(f"❌ Could not fetch stats: {e}")
        return

    admin_n = len(admins)
    bot_n = sum(1 for a in admins if a.user.is_bot)
    human_n = admin_n - bot_n

    ctype = {
        "group": "Group", "supergroup": "Supergroup", "channel": "Channel",
    }.get(chat.type, chat.type)

    text = (
        f"📊 <b>{ctype} Stats</b>\n\n"
        f"👥 <b>Members:</b> {member_count}\n"
        f"🛡️ <b>Admins:</b> {admin_n} ({human_n} human, {bot_n} bot)\n"
        f"🆔 <b>Chat ID:</b> <code>{chat.id}</code>\n"
    )
    if full.username:
        text += f"🔗 <b>Username:</b> @{full.username}\n"
    if full.title:
        text += f"🏷️ <b>Title:</b> {full.title}\n"
    await msg.reply_html(text)


@group_only
@admin_only
async def adminlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
    except TelegramError as e:
        await msg.reply_html(f"❌ Could not fetch admins: {e}")
        return

    lines = ["🛡️ <b>Administrators</b>"]
    for a in admins:
        u = a.user
        role = "👑 Owner" if a.status == "creator" else "🛡️ Admin"
        lines.append(f"• {mention_id(u.id, u.full_name or u.first_name)} — {role}")
    await msg.reply_html("\n".join(lines))


@group_only
@admin_only
async def chatid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    await update.effective_message.reply_html(
        f"🆔 <b>Chat ID:</b> <code>{chat.id}</code>\n"
        f"Use it with the owner's <code>/leave {chat.id}</code> if needed."
    )


# ── Owner ─────────────────────────────────────────────────────────────────────

@owner_only
async def leave_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    args = context.args or []
    if not args or not args[0].lstrip("-").isdigit():
        await msg.reply_html("🚪 <b>Usage:</b> <code>/leave &lt;chat_id&gt;</code>")
        return
    cid = int(args[0])
    try:
        await context.bot.leave_chat(cid)
        await msg.reply_html(f"🚪 Left chat <code>{cid}</code>.")
    except TelegramError as e:
        await msg.reply_html(f"❌ Could not leave <code>{cid}</code>: {e}")


@owner_only
async def setbotname_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    name = " ".join(context.args or []).strip()
    if not name:
        await msg.reply_html("🏷️ <b>Usage:</b> <code>/setbotname Iota Bot</code>")
        return
    if len(name) > 64:
        await msg.reply_html("❌ Name too long (max 64 characters).")
        return
    try:
        await context.bot.set_my_name(name)
        await msg.reply_html(f"🏷️ Bot name set to <b>{name}</b>.")
    except TelegramError as e:
        await msg.reply_html(f"❌ Could not set name: {e}")
