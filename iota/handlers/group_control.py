"""
Iota Group Control — powerful admin tools to manage the chat itself
- /setgtitle <text>   → rename the group
- /setgdesc <text>    → set group description
- /setgpic            → reply to a photo to set it as group pic
- /slowmode <secs>    → set chat slow-mode (0 = off)
- /invitelink         → show the current primary invite link
- /revoke             → generate a fresh primary invite link
- /del                → delete the replied message

Works both in groups and in DM (after selecting an active group with /mygroups).
All commands are admin-only (bot-owner bypasses, as elsewhere).
"""
import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import TelegramError
from utils.helpers import resolve_target_chat
from utils.safe_html import safe_html

logger = logging.getLogger(__name__)


async def setgtitle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await msg.reply_html(err); return
    if not context.args:
        await msg.reply_html("Usage: <code>/setgtitle &lt;new title&gt;</code>"); return
    new_title = " ".join(context.args)
    try:
        await context.bot.set_chat_title(chat_id, new_title[:128])
        await msg.reply_html(f"✅ <b>Title updated!</b>\n📛 {safe_html(new_title[:128])}")
    except TelegramError as e:
        await msg.reply_html(f"❌ Failed: {safe_html(str(e))}")


async def setgdesc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await msg.reply_html(err); return
    if not context.args:
        await msg.reply_html("Usage: <code>/setgdesc &lt;new description&gt;</code>"); return
    desc = " ".join(context.args)
    try:
        await context.bot.set_chat_description(chat_id, desc[:255])
        await msg.reply_html("✅ <b>Description updated!</b>")
    except TelegramError as e:
        await msg.reply_html(f"❌ Failed: {safe_html(str(e))}")


async def setgpic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set group photo. Works in group OR DM (reply to a photo in DM after /mygroups)."""
    msg = update.effective_message
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await msg.reply_html(err); return
    if not (msg.reply_to_message and msg.reply_to_message.photo):
        await msg.reply_html(
            "📷 Reply to a <b>photo</b> with <code>/setgpic</code>\n"
            f"(Active group: <b>{safe_html(title)}</b>)"
        ); return
    photo = msg.reply_to_message.photo[-1]
    try:
        # Prefer file_id string — works when the photo was sent in DM or group
        file_id = photo.file_id
        await context.bot.set_chat_photo(chat_id, photo=file_id)
        await msg.reply_html(f"✅ <b>Group photo updated</b> for {safe_html(title)}!")
    except TelegramError as e:
        await msg.reply_html(f"❌ Failed: {safe_html(str(e))}")


async def slowmode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await msg.reply_html(err); return
    if not context.args:
        cur = 0
        try:
            ch = await context.bot.get_chat(chat_id)
            cur = ch.slow_mode_delay or 0
        except Exception:
            pass
        await msg.reply_html(
            f"🐢 <b>Slow Mode — {safe_html(title)}</b>\n\n"
            f"Current: <b>{cur}s</b>\n"
            f"Usage: <code>/slowmode &lt;seconds&gt;</code> (0 = off, max 60)"
        ); return
    try:
        secs = int(context.args[0])
    except ValueError:
        await msg.reply_html("❌ Seconds must be a number!"); return
    secs = max(0, min(secs, 60))
    try:
        await context.bot.set_chat_slow_mode_delay(chat_id, secs)
        await msg.reply_html(
            "✅ Slow mode " + ("disabled." if secs == 0 else f"set to <b>{secs}s</b>.")
        )
    except TelegramError as e:
        await msg.reply_html(f"❌ Failed: {safe_html(str(e))}")


async def invitelink_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await msg.reply_html(err); return
    try:
        link = await context.bot.export_chat_invite_link(chat_id)
        await msg.reply_html(
            f"🔗 <b>Invite Link — {safe_html(title)}</b>\n<code>{safe_html(link)}</code>"
        )
    except TelegramError as e:
        await msg.reply_html(f"❌ Failed: {safe_html(str(e))}")


async def revoke_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await msg.reply_html(err); return
    try:
        await context.bot.create_chat_invite_link(chat_id, creates_join_request=False)
        link = await context.bot.export_chat_invite_link(chat_id)
        await msg.reply_html(
            f"🔄 <b>Invite link revoked & regenerated!</b>\n\n"
            f"🔗 <code>{safe_html(link)}</code>"
        )
    except TelegramError as e:
        await msg.reply_html(f"❌ Failed: {safe_html(str(e))}")


async def del_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a replied message — must be used *inside* the group (message ids
    are chat-local; DM reply cannot target a group message)."""
    msg = update.effective_message
    chat = update.effective_chat
    if chat.type == "private":
        await msg.reply_html(
            "🗑️ <b>/del</b> group ke andar use karo (message pe reply karke).\n"
            "DM se group messages delete nahi ho sakte."
        ); return
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await msg.reply_html(err); return
    target = msg.reply_to_message
    if not target:
        await msg.reply_html("🗑️ Reply to the message you want to delete."); return
    try:
        await context.bot.delete_message(chat_id, target.message_id)
    except TelegramError as e:
        await msg.reply_html(f"❌ Failed: {safe_html(str(e))}")
