"""
Iota Advanced Admin System
- Lock/Unlock (messages, media, stickers, gifs, links, polls)
- Flood control with auto-mute
- Captcha on join
- Rules system
- Warn system (setwarnlimit, setwarnmode)
- Notes system (/save, /get)
- Approval system
- Log channel
- Clean service messages
- Anti-channel pin
- Owner Announce (broadcast to all groups)
"""
import logging
import asyncio, re, time, random
from telegram import Update, ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import TelegramError
from utils.mongo_db import (
    get_db, ensure_user, get_user,
    set_note, get_note, delete_note, list_notes, clear_notes,
)
from utils.helpers import mention, ts, is_admin
from utils.safe_html import safe_html

logger = logging.getLogger(__name__)

# ── DB helpers ────────────────────────────────────────────────────────────────

def _db(): return get_db()

async def _get_group_settings(cid: int) -> dict:
    # Delegate to the centralised, always-complete default document in
    # utils/mongo_db (ensure_group_settings). This guarantees the field set
    # matches what the auto-tracking path and other handlers expect.
    from utils.mongo_db import ensure_group_settings
    return await ensure_group_settings(cid)

async def _update_gs(cid: int, **kw):
    await _db().group_settings.update_one({"_id": cid}, {"$set": kw}, upsert=True)

# ── Runtime flood tracker ─────────────────────────────────────────────────────
_flood_track: dict = {}   # {chat_id: {user_id: [timestamps]}}

# ── Notes ─────────────────────────────────────────────────────────────────────

async def _save_note(cid, name, content):
    await set_note(cid, name, content)

async def _get_note(cid, name):
    val = await get_note(cid, name)
    return {"content": val} if val else None

async def _list_notes(cid):
    keys = await list_notes(cid)
    return [{"name": k} for k in keys]

async def _del_note(cid, name):
    await delete_note(cid, name)

# ── Warns (advanced) ──────────────────────────────────────────────────────────

async def _count_user_warns(cid, uid):
    return await _db().warnings.count_documents({"chat_id": cid, "user_id": uid})

# ═══════════════════════════════════════════════════════════════════════
#  LOCK / UNLOCK
# ═══════════════════════════════════════════════════════════════════════

LOCK_TYPES = {
    "messages": "lock_messages",
    "msg":      "lock_messages",
    "media":    "lock_media",
    "photo":    "lock_media",
    "sticker":  "lock_stickers",
    "stickers": "lock_stickers",
    "gif":      "lock_gifs",
    "gifs":     "lock_gifs",
    "link":     "lock_links",
    "links":    "lock_links",
    "url":      "lock_links",
    "poll":     "lock_polls",
    "polls":    "lock_polls",
    "forward":  "lock_forwards",
    "forwards": "lock_forwards",
    "game":     "lock_games",
    "games":    "lock_games",
}


async def lock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    if not context.args:
        await update.message.reply_html(
            "🔒 <b>Lock Types:</b>\n"
            "messages | media | sticker | gif | link | poll | forward | game\n\n"
            "Usage: /lock &lt;type&gt;"
        ); return
    lock_type = context.args[0].lower()
    field = LOCK_TYPES.get(lock_type)
    if not field: await update.message.reply_html(f"❌ Unknown type: {lock_type}"); return
    await _update_gs(chat_id, **{field: True})
    await update.message.reply_html(f"🔒 <b>{lock_type}</b> locked!")


async def unlock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    if not context.args:
        await update.message.reply_html("Usage: /unlock &lt;type&gt;"); return
    lock_type = context.args[0].lower()
    field = LOCK_TYPES.get(lock_type)
    if not field: await update.message.reply_html(f"❌ Unknown type!"); return
    await _update_gs(chat_id, **{field: False})
    await update.message.reply_html(f"🔓 <b>{lock_type}</b> unlocked!")


async def locks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=False)
    if err:
        await update.message.reply_html(err); return
    gs = await _get_group_settings(chat_id)
    def _s(v): return "🔒" if v else "🔓"
    await update.message.reply_html(
        f"🔐 <b>Lock Status — {safe_html(title)}</b>\n\n"
        f"{_s(gs['lock_messages'])} Messages\n"
        f"{_s(gs['lock_media'])} Media/Photos\n"
        f"{_s(gs['lock_stickers'])} Stickers\n"
        f"{_s(gs['lock_gifs'])} GIFs\n"
        f"{_s(gs['lock_links'])} Links\n"
        f"{_s(gs['lock_polls'])} Polls\n"
        f"{_s(gs['lock_forwards'])} Forwards\n"
        f"{_s(gs['lock_games'])} Games"
    )


# ── Lock enforcement handler ───────────────────────────────────────────────────

async def lock_enforcement_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    chat = update.effective_chat
    u    = update.effective_user
    if not msg or not u or chat.type == "private": return
    if await is_admin(update, context, u.id): return

    gs = await _get_group_settings(chat.id)

    should_delete = False

    if gs["lock_stickers"] and msg.sticker:
        should_delete = True
    elif gs["lock_gifs"] and msg.animation:
        should_delete = True
    elif gs["lock_polls"] and msg.poll:
        should_delete = True
    elif gs["lock_forwards"] and msg.forward_origin:
        should_delete = True
    elif gs["lock_media"] and (msg.photo or msg.video or msg.document or msg.audio or msg.voice):
        should_delete = True
    elif gs["lock_links"] and msg.text:
        if re.search(r'https?://|t\.me/', msg.text, re.IGNORECASE):
            should_delete = True

    if should_delete:
        try:
            await msg.delete()
            if not gs["silent_actions"]:
                warn = await context.bot.send_message(
                    chat.id,
                    f"🔒 {mention(u)} — That content is locked in this group!",
                    parse_mode="HTML"
                )
                asyncio.create_task(_auto_del(context.bot, chat.id, warn.message_id, 5))
        except Exception:
            pass


async def _auto_del(bot, cid, mid, delay):
    await asyncio.sleep(delay)
    try: await bot.delete_message(cid, mid)
    except Exception as e:
        logger.debug(f"Suppressed error in advanced_admin.py: {e}")
        pass


# ═══════════════════════════════════════════════════════════════════════
#  SETFLOOD / CLEARFLOOD
# ═══════════════════════════════════════════════════════════════════════

async def setflood_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    args = context.args
    if not args:
        gs = await _get_group_settings(chat_id)
        status = f"Limit: {gs['flood_limit']} msgs" if gs['flood_limit'] else "OFF"
        await update.message.reply_html(
            f"🌊 Flood Control: <b>{status}</b>\n"
            f"Action: <b>{gs['flood_action']}</b>\n\n"
            "Usage: /setflood <number> — Set flood limit\n"
            "/setflood off — Disable\n"
            "/floodmode mute/ban/kick"
        ); return
    if args[0].lower() == "off":
        await _update_gs(chat_id, flood_limit=0)
        await update.message.reply_html("🌊 Flood control <b>disabled</b>!")
    else:
        try:
            limit = int(args[0])
            await _update_gs(chat_id, flood_limit=limit)
            await update.message.reply_html(f"🌊 Flood limit set to <b>{limit} messages</b>!")
        except ValueError:
            await update.message.reply_html("❌ Use a number or 'off'!")


async def floodmode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    args = context.args
    if not args or args[0].lower() not in ("mute","ban","kick"):
        await update.message.reply_html("Usage: /floodmode mute/ban/kick"); return
    mode = args[0].lower()
    await _update_gs(chat_id, flood_action=mode)
    await update.message.reply_html(f"🌊 Flood action set to: <b>{mode}</b>!")


async def flood_check_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check flood per message."""
    msg  = update.effective_message
    chat = update.effective_chat
    u    = update.effective_user
    if not msg or not u or chat.type == "private": return
    if await is_admin(update, context, u.id): return

    gs = await _get_group_settings(chat.id)
    if not gs["flood_limit"]: return

    now   = ts()
    cdata = _flood_track.setdefault(chat.id, {})
    times = [t for t in cdata.get(u.id, []) if now - t < 5]
    times.append(now)
    cdata[u.id] = times

    if len(times) > gs["flood_limit"]:
        cdata[u.id] = []
        action = gs["flood_action"]
        try:
            if action == "mute":
                from datetime import datetime, timezone
                until = datetime.fromtimestamp(now + 300, tz=timezone.utc)
                await context.bot.restrict_chat_member(
                    chat.id, u.id, ChatPermissions(can_send_messages=False), until_date=until
                )
                act_text = "muted for 5 min"
            elif action == "ban":
                await context.bot.ban_chat_member(chat.id, u.id)
                act_text = "banned"
            else:
                await context.bot.ban_chat_member(chat.id, u.id)
                await context.bot.unban_chat_member(chat.id, u.id)
                act_text = "kicked"

            if not gs["silent_actions"]:
                warn = await context.bot.send_message(
                    chat.id,
                    f"⚡ {mention(u)} {act_text} for flooding!",
                    parse_mode="HTML"
                )
                asyncio.create_task(_auto_del(context.bot, chat.id, warn.message_id, 10))
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════
#  RULES
# ═══════════════════════════════════════════════════════════════════════

async def rules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=False)
    if err:
        await update.message.reply_html(err); return
    gs = await _get_group_settings(chat_id)
    if not gs["rules"]:
        await update.message.reply_html("📋 No rules set! Admins use /setrules"); return
    await update.message.reply_html(
        f"📋 <b>Rules — {safe_html(title)}</b>\n\n{safe_html(gs['rules'])}"
    )


async def setrules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    msg = update.effective_message
    text = ""
    if msg.reply_to_message and msg.reply_to_message.text:
        text = msg.reply_to_message.text
    elif context.args:
        text = " ".join(context.args)
    if not text:
        await update.message.reply_html(
            f"Usage: /setrules &lt;rules text&gt;\n"
            f"(Active: <b>{safe_html(title)}</b>)"
        ); return
    await _update_gs(chat_id, rules=text)
    await update.message.reply_html(
        f"✅ Rules updated for <b>{safe_html(title)}</b>!"
    )


async def clearrules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    await _update_gs(chat_id, rules="")
    await update.message.reply_html(
        f"✅ Rules cleared for <b>{safe_html(title)}</b>!"
    )


# ═══════════════════════════════════════════════════════════════════════
#  WARN SYSTEM (advanced)
# ═══════════════════════════════════════════════════════════════════════

async def setwarnlimit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    if not context.args: await update.message.reply_html("Usage: /setwarnlimit <number>"); return
    try: limit = int(context.args[0])
    except Exception as e:
        logger.debug(f"Suppressed error in advanced_admin.py: {e}")
        await update.message.reply_html("❌ Invalid number!"); return
    await _update_gs(chat_id, warn_limit=limit)
    await update.message.reply_html(f"⚠️ Warn limit set to <b>{limit}</b>!")


async def setwarnmode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    if not context.args or context.args[0].lower() not in ("ban","mute","kick"):
        await update.message.reply_html("Usage: /setwarnmode ban/mute/kick"); return
    mode = context.args[0].lower()
    await _update_gs(chat_id, warn_mode=mode)
    await update.message.reply_html(f"⚠️ Warn mode set to: <b>{mode}</b>!")


async def warnlimit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    gs = await _get_group_settings(chat_id)
    await update.message.reply_html(
        f"⚠️ Warn limit: <b>{gs['warn_limit']}</b>\n"
        f"Mode: <b>{gs['warn_mode']}</b>"
    )


async def resetallwarns_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    result = await _db().warnings.delete_many({"chat_id": chat_id})
    await update.message.reply_html(f"✅ Cleared <b>{result.deleted_count}</b> warnings!")


async def warnings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    msg = update.effective_message
    if msg.reply_to_message and msg.reply_to_message.from_user:
        tu = msg.reply_to_message.from_user
    elif context.args:
        try: uid = int(context.args[0])
        except Exception as e:
            logger.debug(f"Suppressed error in advanced_admin.py: {e}")
            await msg.reply_html("❌ Invalid user!"); return
        try:
            m = await context.bot.get_chat_member(chat_id, uid)
            tu = m.user
        except Exception as e:
            logger.debug(f"Suppressed error in advanced_admin.py: {e}")
            await msg.reply_html("❌ User not found!"); return
    else:
        tu = update.effective_user
    count = await _count_user_warns(chat_id, tu.id)
    gs = await _get_group_settings(chat_id)
    await msg.reply_html(
        f"⚠️ {mention(tu)}: <b>{count}/{gs['warn_limit']}</b> warnings"
    )


# ═══════════════════════════════════════════════════════════════════════
#  NOTES
# ═══════════════════════════════════════════════════════════════════════

async def save_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    msg = update.effective_message
    args = context.args
    if not args: await msg.reply_html("Usage: /save <name> <content>\nOr reply: /save <name>"); return
    name = args[0].lower()
    if msg.reply_to_message and msg.reply_to_message.text:
        content = msg.reply_to_message.text
    elif len(args) > 1:
        content = " ".join(args[1:])
    else:
        await msg.reply_html("❌ Provide content or reply to a message!"); return
    await _save_note(chat_id, name, content)
    await msg.reply_html(f"✅ Note <b>{safe_html(name)}</b> saved!")


async def get_note_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle #notename or /get notename."""
    msg  = update.effective_message
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=False)
    if err:
        return
    text = msg.text or ""
    name = None
    if text.startswith("#") and len(text) > 1:
        name = text[1:].split()[0].lower()
    elif text.startswith("/get ") and len(text) > 5:
        name = text[5:].strip().lower()
    if not name: return
    note = await _get_note(chat_id, name)
    if note:
        await msg.reply_html(f"📝 <b>{safe_html(name)}</b>\n\n{safe_html(note['content'])}")


async def notes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=False)
    if err:
        await update.message.reply_html(err); return
    notes = await _list_notes(chat_id)
    if not notes: await update.message.reply_html("📋 No notes saved!"); return
    text = f"📝 <b>Notes — {safe_html(title)}</b>\n\n"
    for n in notes:
        text += f"• #{safe_html(n['name'])}\n"
    text += "\nUse #notename to get a note"
    await update.message.reply_html(text)


async def clear_note_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    if not context.args: await update.message.reply_html("Usage: /clear <notename>"); return
    name = context.args[0].lower()
    await _del_note(chat_id, name)
    await update.message.reply_html(f"✅ Note <b>{name}</b> deleted!")


# ═══════════════════════════════════════════════════════════════════════
#  LOG CHANNEL
# ═══════════════════════════════════════════════════════════════════════

async def setlogchannel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    if not context.args: await update.message.reply_html("Usage: /logchannel &lt;channel_id&gt;"); return
    try: cid = int(context.args[0])
    except Exception as e:
        logger.debug(f"Suppressed error in advanced_admin.py: {e}")
        await update.message.reply_html("❌ Invalid channel ID!"); return
    await _update_gs(chat_id, log_channel=cid)
    await update.message.reply_html(
        f"✅ Log channel for <b>{safe_html(title)}</b> set to <code>{cid}</code>!"
    )


async def nolog_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    await _update_gs(chat_id, log_channel=0)
    await update.message.reply_html(f"✅ Log channel removed for <b>{safe_html(title)}</b>!")


# ═══════════════════════════════════════════════════════════════════════
#  CLEAN SERVICE MESSAGES
# ═══════════════════════════════════════════════════════════════════════

async def cleanservice_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    args = context.args
    state = args[0].lower() == "on" if args else True
    await _update_gs(chat_id, clean_service=state)
    await update.message.reply_html(
        f"🧹 Clean service messages: <b>{'ON' if state else 'OFF'}</b>"
    )


async def clean_service_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-delete join/leave service messages if enabled."""
    msg  = update.effective_message
    chat = update.effective_chat
    if not msg: return
    gs = await _get_group_settings(chat.id)
    if gs["clean_service"]:
        try: await msg.delete()
        except Exception as e:
            logger.debug(f"Suppressed error in advanced_admin.py: {e}")
            pass


# ═══════════════════════════════════════════════════════════════════════
#  ANTI CHANNEL PIN
# ═══════════════════════════════════════════════════════════════════════

async def antichannelpin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    args = context.args
    state = args[0].lower() == "on" if args else True
    await _update_gs(chat_id, anti_channel_pin=state)
    await update.message.reply_html(
        f"📌 Anti-channel pin: <b>{'ON' if state else 'OFF'}</b>"
    )


async def channel_pin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    chat = update.effective_chat
    if not msg or not msg.pinned_message: return
    gs = await _get_group_settings(chat.id)
    if gs["anti_channel_pin"]:
        try: await context.bot.unpin_chat_message(chat.id, msg.pinned_message.message_id)
        except Exception as e:
            logger.debug(f"Suppressed error in advanced_admin.py: {e}")
            pass


# ═══════════════════════════════════════════════════════════════════════
#  DISABLE / ENABLE COMMANDS
# ═══════════════════════════════════════════════════════════════════════

async def disable_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    if not context.args: await update.message.reply_html("Usage: /disable <command>"); return
    cmd = context.args[0].lstrip("/").lower()
    gs = await _get_group_settings(chat_id)
    disabled = gs["disabled_cmds"]
    if cmd not in disabled:
        disabled.append(cmd)
        await _update_gs(chat_id, disabled_cmds=disabled)
    await update.message.reply_html(f"🚫 Command <b>/{cmd}</b> disabled!")


async def enable_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    if not context.args: await update.message.reply_html("Usage: /enable <command>"); return
    cmd = context.args[0].lstrip("/").lower()
    gs = await _get_group_settings(chat_id)
    disabled = [d for d in gs["disabled_cmds"] if d != cmd]
    await _update_gs(chat_id, disabled_cmds=disabled)
    await update.message.reply_html(f"✅ Command <b>/{cmd}</b> enabled!")


async def disabled_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    gs = await _get_group_settings(chat_id)
    if not gs["disabled_cmds"]:
        await update.message.reply_html("✅ No disabled commands!"); return
    text = "🚫 <b>Disabled Commands:</b>\n\n" + "\n".join(f"• /{c}" for c in gs["disabled_cmds"])
    await update.message.reply_html(text)


# ═══════════════════════════════════════════════════════════════════════
#  SILENT ACTIONS
# ═══════════════════════════════════════════════════════════════════════

async def silentactions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    args = context.args
    state = args[0].lower() == "on" if args else True
    await _update_gs(chat_id, silent_actions=state)
    await update.message.reply_html(
        f"🤫 Silent actions: <b>{'ON' if state else 'OFF'}</b>\n"
        f"{'(No notifications for auto-actions)' if state else '(Notifications enabled)'}"
    )


# ═══════════════════════════════════════════════════════════════════════
#  ADMINCACHE / ADMINS REFRESH
# ═══════════════════════════════════════════════════════════════════════

async def admincache_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        await update.message.reply_html(
            f"✅ Admin cache refreshed! <b>{len(admins)}</b> admins found."
        )
    except Exception as e:
        await update.message.reply_html(f"❌ {e}")


# ═══════════════════════════════════════════════════════════════════════
#  GOODBYE
# ═══════════════════════════════════════════════════════════════════════

async def setgoodbye_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    args = context.args
    if not args:
        await update.message.reply_html(
            "Usage: /setgoodbye on/off\n"
            "Or: /setgoodbye <message>\n"
            "Use {name} for user name"
        ); return
    if args[0].lower() in ("on","off"):
        await _update_gs(chat_id, goodbye_enabled=args[0].lower()=="on")
        await update.message.reply_html(f"👋 Goodbye: <b>{args[0]}</b>!")
    else:
        msg_text = " ".join(args)
        await _update_gs(chat_id, goodbye_enabled=True, goodbye_msg=msg_text)
        await update.message.reply_html(f"✅ Goodbye message set!")


# ═══════════════════════════════════════════════════════════════════════
#  CAPTCHA (basic toggle)
# ═══════════════════════════════════════════════════════════════════════

_captcha_pending: dict = {}  # user_id -> {chat_id, msg_id, answer}


async def captcha_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    args = context.args
    if not args or args[0].lower() not in ("on","off"):
        gs = await _get_group_settings(chat_id)
        await update.message.reply_html(
            f"🔐 Captcha: <b>{'ON' if gs['captcha_enabled'] else 'OFF'}</b>\n\n"
            "Usage: /captcha on/off"
        ); return
    state = args[0].lower() == "on"
    await _update_gs(chat_id, captcha_enabled=state)
    await update.message.reply_html(f"🔐 Captcha: <b>{'ON' if state else 'OFF'}</b>!")


async def captcha_new_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    chat = update.effective_chat
    if not msg or not msg.new_chat_members: return
    gs = await _get_group_settings(chat.id)
    if not gs["captcha_enabled"]: return

    for member in msg.new_chat_members:
        if member.is_bot: continue
        # Mute user immediately
        try:
            await context.bot.restrict_chat_member(
                chat.id, member.id, ChatPermissions(can_send_messages=False)
            )
        except Exception as e:
            logger.debug(f"Suppressed error in advanced_admin.py: {e}")
            pass

        a = random.randint(1, 9); b = random.randint(1, 9)
        answer = a + b
        import uuid; gid = str(uuid.uuid4())[:6]
        _captcha_pending[member.id] = {"chat_id": chat.id, "answer": answer}

        opts = list(set([answer, answer+1, answer-1, answer+2]))[:4]
        random.shuffle(opts)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(str(o), callback_data=f"captcha_{member.id}_{o}")
            for o in opts
        ]])
        cap_msg = await context.bot.send_message(
            chat.id,
            f"🔐 Welcome {mention(member)}!\n\nProve you're human: <b>{a} + {b} = ?</b>\n\n"
            f"You have <b>{gs['captcha_time']}s</b> to answer!",
            parse_mode="HTML",
            reply_markup=kb
        )
        asyncio.create_task(
            _captcha_timeout(context, chat.id, member.id, cap_msg.message_id, gs["captcha_time"])
        )


async def _captcha_timeout(context, cid, uid, mid, wait):
    await asyncio.sleep(wait)
    if uid in _captcha_pending:
        _captcha_pending.pop(uid, None)
        try:
            await context.bot.ban_chat_member(cid, uid)
            await context.bot.delete_message(cid, mid)
        except Exception as e:
            logger.debug(f"Suppressed error in advanced_admin.py: {e}")
            pass


async def captcha_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; u = q.from_user; await q.answer()
    parts = q.data.split("_")
    target_uid = int(parts[1]); chosen = int(parts[2])
    if u.id != target_uid:
        await q.answer("Not your captcha!", show_alert=True); return
    pending = _captcha_pending.pop(target_uid, None)
    if not pending: return
    if chosen == pending["answer"]:
        try:
            await context.bot.restrict_chat_member(
                pending["chat_id"], target_uid,
                ChatPermissions(can_send_messages=True,
                                can_send_photos=True, can_send_videos=True, can_send_audios=True,
                                can_send_documents=True, can_send_video_notes=True, can_send_voice_notes=True,
                                can_send_polls=True, can_send_other_messages=True,
                                can_add_web_page_previews=True, can_invite_users=True)
            )
            await q.edit_message_text(f"✅ {mention(u)} passed the captcha! Welcome!", parse_mode="HTML")
        except Exception as e:
            logger.debug(f"Suppressed error in advanced_admin.py: {e}")
            pass
    else:
        try:
            await context.bot.ban_chat_member(pending["chat_id"], target_uid)
            await q.edit_message_text(f"❌ {mention(u)} failed captcha! Removed.", parse_mode="HTML")
        except Exception as e:
            logger.debug(f"Suppressed error in advanced_admin.py: {e}")
            pass


# ═══════════════════════════════════════════════════════════════════════
#  OWNER ANNOUNCE — Send message to all groups via bot
# ═══════════════════════════════════════════════════════════════════════

async def announce_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    NOTE: This function is NOT currently registered in bot.py — the
    active /announce command is handlers/owner_panel.py's announce_cmd
    (imported there as owner_announce_cmd). This copy is kept only for
    reference; it is not imported or wired up anywhere, so it cannot
    cause a duplicate-registration conflict. Identity string fixed below
    regardless, in case this is ever reused.
    """
    """Owner sends announcement to ONE or ALL groups.
    Usage: /announce <group_id or all> <message>
    Example: /announce all Enjoy the new Iota bot update!
    """
    from config import OWNER_ID
    u = update.effective_user
    if u.id != OWNER_ID:
        await update.message.reply_html("❌ Owner only!"); return
    args = context.args
    if len(args) < 2:
        await update.message.reply_html(
            "📢 <b>Owner Announce</b>\n\n"
            "Usage: /announce all &lt;message&gt;\n"
            "Or: /announce &lt;group_id&gt; &lt;message&gt;\n\n"
            "Example:\n"
            "/announce all Enjoy the new Iota bot update! 🎉"
        ); return

    target = args[0].lower()
    message = " ".join(args[1:])
    full_msg = f"📢 <b>Announcement</b>\n\n{message}"

    if target == "all":
        # Get all unique chat_ids from group_settings
        cursor = _db().group_settings.find({}, {"_id": 1})
        chats = await cursor.to_list(10000)
        sent = 0; failed = 0
        status = await update.message.reply_html(
            f"📢 Sending to <b>{len(chats)}</b> groups..."
        )
        for ch in chats:
            try:
                await context.bot.send_message(ch["_id"], full_msg, parse_mode="HTML")
                sent += 1
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.debug(f"Suppressed error in advanced_admin.py: {e}")
                failed += 1
        await status.edit_text(
            f"📢 Done!\n✅ Sent: {sent}\n❌ Failed: {failed}",
            parse_mode="HTML"
        )
    else:
        try:
            cid = int(target)
            await context.bot.send_message(cid, full_msg, parse_mode="HTML")
            await update.message.reply_html(f"✅ Sent to <code>{cid}</code>!")
        except ValueError:
            await update.message.reply_html("❌ Invalid group ID! Use a number or 'all'.")
        except Exception as e:
            await update.message.reply_html(f"❌ Failed: {e}")


# ═══════════════════════════════════════════════════════════════════════
#  SETLANG (group language)
# ═══════════════════════════════════════════════════════════════════════

async def setlang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    if not context.args:
        await update.message.reply_html("Usage: /setlang en/hi/bn/te/mr"); return
    lang = context.args[0].lower()
    await _update_gs(chat_id, lang=lang)
    await update.message.reply_html(f"🌐 Language set to: <b>{lang}</b>!")


# ═══════════════════════════════════════════════════════════════════════
#  APPROVAL SYSTEM
# ═══════════════════════════════════════════════════════════════════════

async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    msg = update.effective_message
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_html("❌ Reply to someone!"); return
    tu = msg.reply_to_message.from_user
    await _db().approved.update_one(
        {"chat_id": chat_id, "user_id": tu.id},
        {"$set": {"approved": True}},
        upsert=True
    )
    await msg.reply_html(f"✅ {mention(tu)} is now <b>approved</b> in this group!")


async def unapprove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    msg = update.effective_message
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_html("❌ Reply to someone!"); return
    tu = msg.reply_to_message.from_user
    await _db().approved.delete_one({"chat_id": chat_id, "user_id": tu.id})
    await msg.reply_html(f"❌ {mention(tu)} <b>unapproved</b>!")


async def approved_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    cursor = _db().approved.find({"chat_id": chat_id})
    docs = await cursor.to_list(50)
    if not docs: await update.message.reply_html("📋 No approved users!"); return
    text = f"✅ <b>Approved Users</b>\n\n"
    for d in docs:
        text += f"• <code>{d['user_id']}</code>\n"
    await update.message.reply_html(text)
