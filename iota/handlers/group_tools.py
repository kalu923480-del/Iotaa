"""
Iota Group Tools
- /link — Get group invite link
- /del_link — Revoke invite link  
- /settag — Set member tag (admin)
- /mytag — Check your tag
- .promote levels with titles (Junior/Senior/Full Admin)
- Welcome back after dead
"""
import time
from telegram import Update, ChatPermissions
from telegram.ext import ContextTypes
from telegram.error import TelegramError
from utils.mongo_db import get_db, ensure_user, get_user, update_user
from utils.helpers import mention, ts, is_admin, promote_with_rights
from utils.fonts import sc

# ── Member Tags DB ────────────────────────────────────────────────────────────

async def _get_tag(uid: int, cid: int) -> str | None:
    doc = await get_db().member_tags.find_one({"_id": f"{uid}_{cid}"})
    return doc.get("tag") if doc else None

async def _set_tag(uid: int, cid: int, tag: str, by: int):
    await get_db().member_tags.update_one(
        {"_id": f"{uid}_{cid}"},
        {"$set": {"tag": tag, "uid": uid, "cid": cid, "set_by": by, "at": ts()}},
        upsert=True
    )

async def _del_tag(uid: int, cid: int):
    await get_db().member_tags.delete_one({"_id": f"{uid}_{cid}"})

# ── Dead-reason tracking ──────────────────────────────────────────────────────

async def _log_dead(uid: int, reason: str = "killed"):
    await get_db().dead_log.update_one(
        {"_id": uid},
        {"$set": {"reason": reason, "since": ts()}},
        upsert=True
    )

async def _get_dead_log(uid: int):
    return await get_db().dead_log.find_one({"_id": uid})

async def _clear_dead(uid: int):
    await get_db().dead_log.delete_one({"_id": uid})

# ── /settag ───────────────────────────────────────────────────────────────────

async def settag_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; chat = update.effective_chat; u = update.effective_user
    if chat.type == "private":
        await msg.reply_html("🚫 Group only!"); return
    if not await is_admin(update, context):
        await msg.reply_html("❌ Admins only!"); return
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_html("❌ Reply to a user to set their tag!\nUsage: /settag &lt;tag&gt; (reply)"); return
    if not context.args:
        await msg.reply_html("❌ Usage: /settag &lt;tag&gt; (reply to user)"); return
    target = msg.reply_to_message.from_user
    tag = " ".join(context.args)
    if len(tag) > 20:
        await msg.reply_html("❌ Tag too long! Max 20 chars."); return
    await _set_tag(target.id, chat.id, tag, u.id)
    await msg.reply_html(
        f"✅ {mention(target)}'s {sc('Member Tag Is Now')} {tag}."
    )

async def deltag_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; chat = update.effective_chat
    if chat.type == "private": await msg.reply_html("🚫 Group only!"); return
    if not await is_admin(update, context): await msg.reply_html("❌ Admins only!"); return
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_html("❌ Reply to a user!"); return
    target = msg.reply_to_message.from_user
    await _del_tag(target.id, chat.id)
    await msg.reply_html(f"✅ {mention(target)}'s tag removed!")

async def mytag_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; chat = update.effective_chat; u = update.effective_user
    if msg.reply_to_message and msg.reply_to_message.from_user:
        tu = msg.reply_to_message.from_user
    else:
        tu = u
    tag = await _get_tag(tu.id, chat.id)
    if tag:
        await msg.reply_html(f"🏷️ {mention(tu)}'s {sc('Tag')}: <b>{tag}</b>")
    else:
        await msg.reply_html(f"❌ {mention(tu)} {sc('has no tag!')}")

# ── /link & /del_link ─────────────────────────────────────────────────────────

async def link_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; u = update.effective_user
    if chat.type == "private": await update.message.reply_html("🚫 Group only!"); return
    if not await is_admin(update, context):
        await update.message.reply_html("❌ Admins only!"); return
    try:
        link = await context.bot.export_chat_invite_link(chat.id)
        await update.message.reply_html(
            f"🔗 <b>{sc('YOU CAN DELETE THIS LINK USING')}</b> : /del_link:\n\n"
            f"{link}"
        )
    except TelegramError as e:
        await update.message.reply_html(f"❌ {e}")

async def del_link_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private": await update.message.reply_html("🚫 Group only!"); return
    if not await is_admin(update, context):
        await update.message.reply_html("❌ Admins only!"); return
    try:
        await context.bot.revoke_chat_invite_link(
            chat.id, (await context.bot.export_chat_invite_link(chat.id))
        )
        await update.message.reply_html(f"✅ {sc('Invite link revoked!')}")
    except TelegramError as e:
        await update.message.reply_html(f"❌ {e}")

# ── Welcome Back (after dead) ─────────────────────────────────────────────────

async def welcome_back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Called on every group message.
    If user was dead and just sent a message (dead_until has passed),
    show 'Welcome Back' with away time and reason.
    """
    msg = update.effective_message; u = update.effective_user
    chat = update.effective_chat
    if not u or chat.type == "private": return

    now = ts()
    dl = await _get_dead_log(u.id)
    if not dl: return

    # Check if user is no longer dead (dead_until passed)
    user_data = await get_user(u.id)
    if user_data.get("dead_until", 0) > now:
        return  # Still dead

    # They were dead, now they sent a message
    dead_since = dl.get("since", now)
    reason     = dl.get("reason", "killed")
    away_secs  = now - dead_since
    hours      = away_secs // 3600
    minutes    = (away_secs % 3600) // 60

    await _clear_dead(u.id)

    await msg.reply_html(
        f"✦ <b>{sc('WELCOME BACK')} {mention(u)}!</b>\n"
        f"● {sc('YOU WERE AWAY FOR')}: <b>{hours} {sc('HOURS')}, {minutes} {sc('MINUTES')}</b>\n"
        f"● {sc('REASON')}: <b>{reason.upper()}</b>"
    )

# ── /tag (shortcut for mytag) ──────────────────────────────────────────────────

async def tag_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await mytag_cmd(update, context)

# ── /chathistory — toggle chat history visibility ──────────────────────────────

async def chathistory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle chat history for new members."""
    chat = update.effective_chat; u = update.effective_user
    if chat.type == "private": await update.message.reply_html("🚫 Group only!"); return
    if not await is_admin(update, context): await update.message.reply_html("❌ Admins only!"); return
    args = context.args
    if not args: await update.message.reply_html("Usage: /chathistory on|off"); return
    val = args[0].lower() == "on"
    try:
        # This uses linked channel or direct group settings
        # We store preference and inform user
        await get_db().group_settings.update_one(
            {"_id": chat.id},
            {"$set": {"chat_history_visible": val}},
            upsert=True
        )
        await update.message.reply_html(
            f"✅ {sc('Chat History For New Members')}: <b>{'Visible' if val else 'Hidden'}</b>\n"
            f"<i>(Change in Group Settings → Chat History for New Members)</i>"
        )
    except Exception as e:
        await update.message.reply_html(f"❌ {e}")
