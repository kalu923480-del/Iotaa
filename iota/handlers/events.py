"""Iota Bot — Global events commands (/events, /startevent, /stopevent, /eventlist)."""
import logging
from telegram import Update
from telegram.ext import ContextTypes

from utils.mongo_db import ensure_user, get_user
from utils.helpers import mention
from utils.fonts import sc, bold_sc
from utils.permissions import owner_only, group_only
from utils.events import EVENT_TYPES, get_active_events, start_event, stop_event

logger = logging.getLogger(__name__)


async def events_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List active global events (public)."""
    events = await get_active_events()
    if not events:
        await update.message.reply_html(
            f"📅 {sc('No active events right now.')}\n"
            f"{sc('Ask the owner to start one with')} /startevent"
        )
        return
    lines = [f"📅 <b>{sc('Active Events')}</b>", "━" * 18]
    for ev in events:
        key = ev.get("_id")
        info = EVENT_TYPES.get(key, {})
        name = info.get("name", key)
        emoji = info.get("emoji", "📌")
        import time
        remaining = max(0, ev.get("ends", 0) - int(time.time()))
        h, rem = divmod(remaining, 3600)
        m = rem // 60
        lines.append(f"{emoji} <b>{name}</b> — {h}h {m}m left")
    await update.message.reply_html("\n".join(lines))


@owner_only
async def startevent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner-only: start an event."""
    args = context.args or []
    if not args:
        await update.message.reply_html(
            f"⚠️ {sc('Usage')}: /startevent <key> [hours]\n"
            f"{sc('Available keys')}: " + ", ".join(EVENT_TYPES.keys())
        )
        return
    key = args[0].lower()
    try:
        from config import EVENT_DEFAULT_HOURS
        default_h = float(EVENT_DEFAULT_HOURS)
    except Exception:
        default_h = 6.0
    try:
        hours = float(args[1]) if len(args) > 1 else default_h
    except ValueError:
        await update.message.reply_html(f"❌ {sc('Hours must be a number.')}")
        return
    if hours <= 0 or hours > 168:
        await update.message.reply_html(f"❌ {sc('Hours must be between 0 and 168.')}")
        return
    ok, msg, _ = await start_event(key, hours, update.effective_user.id)
    await update.message.reply_html(msg)


@owner_only
async def stopevent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner-only: stop an event."""
    args = context.args or []
    if not args:
        await update.message.reply_html(f"⚠️ {sc('Usage')}: /stopevent <key>")
        return
    key = args[0].lower()
    ok, msg = await stop_event(key)
    await update.message.reply_html(msg)


async def eventlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all available event types (public)."""
    lines = [f"📋 <b>{sc('Available Events')}</b>", "━" * 18]
    for key, info in EVENT_TYPES.items():
        lines.append(
            f"{info['emoji']} <b>{info['name']}</b> (/{key})\n"
            f"   {info['desc']} — x{info['multiplier']} {info['field']}"
        )
    await update.message.reply_html("\n".join(lines))
