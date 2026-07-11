"""
Iota Bot — /whisper command
Send a PRIVATE message to a user from inside a group. The group only sees a
card + a "Read whisper" button; the actual text is delivered privately (a DM
plus the button's alert popup) and a small-caps read receipt is posted back
to the group when the target opens it.
"""
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import Forbidden, BadRequest

from utils.mongo_db import (
    ensure_user, get_whisper, create_whisper, mark_whisper_read,
)
from utils.helpers import mention, resolve_target
from utils.safe_html import safe_html
from utils.fonts import sc, sc_all

logger = logging.getLogger(__name__)


async def whisper_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    msg = update.effective_message
    chat = update.effective_chat
    if chat.type == "private":
        await msg.reply_html("❌ Whisper only works inside a group or supergroup."); return

    await ensure_user(u.id, u.username or "", u.full_name)

    # Resolve the target + the message text. Supports either:
    #   .whisper @user <message>      (named target)
    #   reply to a user + .whisper <message>   (replied target)
    if msg.reply_to_message and msg.reply_to_message.from_user:
        tgt = msg.reply_to_message.from_user
        target_id = tgt.id
        target_mention = mention(tgt)
        text = " ".join(context.args or [])
    else:
        target_id, target_mention, rest = await resolve_target(
            update, context, list(context.args or []))
        text = " ".join(rest)

    if not target_id:
        await msg.reply_html(
            "❌ Mention a user or reply to them:\n"
            "<code>.whisper @user &lt;message&gt;</code>"); return
    if target_id == u.id:
        await msg.reply_html("❌ You can't whisper to yourself!"); return

    # Don't whisper to bots.
    try:
        tu = await context.bot.get_chat(target_id)
        if tu.is_bot:
            await msg.reply_html("❌ You can't whisper to a bot!"); return
    except Exception:
        pass

    text = (text or "").strip()
    if not text:
        await msg.reply_html(
            "❌ Write a message:\n<code>.whisper @user &lt;message&gt;</code>"); return

    wid = await create_whisper(u.id, target_id, chat.id, text)

    # Best-effort private delivery. If the target hasn't started the bot the
    # DM fails — that's fine, they still get the text via the "Read whisper"
    # button's alert popup, so a failed DM never blocks the whisper.
    try:
        await context.bot.send_message(
            target_id,
            f"🔥 {mention(u)} <b>{sc('whispered')}</b>:\n\n{safe_html(text)}",
            parse_mode="HTML",
        )
    except (Forbidden, BadRequest):
        pass

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✉️ " + sc("Read whisper"), callback_data=f"wsp_read_{wid}")
    ]])
    await msg.reply_html(
        sc_all(f"💬 {mention(u)} whispered to {target_mention} 🤫"),
        parse_mode="HTML", reply_markup=kb,
    )


async def whisper_read_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    wid = q.data[len("wsp_read_"):]
    w = await get_whisper(wid)
    if not w:
        await q.answer("❌ Whisper not found.", show_alert=True); return

    reader = q.from_user
    if reader.id != w["target_id"]:
        await q.answer("🔒 This whisper isn't for you.", show_alert=True); return

    # Reveal the private text to the target via the alert popup.
    await q.answer(f"🔥 {w['text']}", show_alert=True)
    await mark_whisper_read(wid)

    # Post the small-caps read receipt back into the group.
    try:
        await context.bot.send_message(
            w["chat_id"],
            sc_all(f"✅ This whisper has been read by {mention(reader)}"),
            parse_mode="HTML",
        )
    except Exception:
        pass

    # Drop the button so it can't be opened again.
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
