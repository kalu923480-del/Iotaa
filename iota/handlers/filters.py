"""
Iota Admin Filters — keyword auto-responders
- /filter <keyword> <reply>   → save an auto-reply for a word/phrase
- /filters                   → list all filters in this chat
- /stop <keyword>            → delete a single filter
- /clearfilters              → delete ALL filters in this chat
- Enforcement: any group text containing a filter keyword gets an
  automatic reply (see filter_enforcement_handler).
"""
import logging
from telegram import Update
from telegram.ext import ContextTypes
from utils.mongo_db import (
    save_filter, match_filter, list_filters, delete_filter, clear_filters
)
from utils.helpers import is_admin
from utils.safe_html import safe_html
from config import OWNER_ID

logger = logging.getLogger(__name__)


async def _is_admin_or_owner(update, context) -> bool:
    uid = update.effective_user.id
    if uid == OWNER_ID:
        return True
    return await is_admin(update, context)


async def _is_admin_or_owner_resolved(update, context, chat_id: int) -> bool:
    uid = update.effective_user.id
    if uid == OWNER_ID:
        return True
    try:
        from utils.group_session import is_user_group_admin
        if await is_user_group_admin(context.bot, chat_id, uid):
            return True
    except Exception:
        pass
    return await is_admin(update, context)


async def filter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    msg = update.effective_message
    if not await _is_admin_or_owner_resolved(update, context, chat_id):
        await msg.reply_html("❌ <b>Admins only!</b>")
        return
    if not context.args or len(context.args) < 2:
        await msg.reply_html(
            "📝 <b>Save a Filter</b>\n\n"
            "Usage: <code>/filter &lt;keyword&gt; &lt;reply text&gt;</code>\n\n"
            "Example: <code>/filter rules Check #rules in the pinned message</code>\n"
            "💡 Reply to a photo/sticker/GIF with <code>/filter &lt;keyword&gt;</code> "
            "to auto-send that media when the keyword is used."
        )
        return

    keyword = context.args[0]
    file_id = None
    ftype = "text"
    replied = msg.reply_to_message
    if replied:
        if replied.photo:
            file_id = replied.photo[-1].file_id
            ftype = "photo"
        elif replied.sticker:
            file_id = replied.sticker.file_id
            ftype = "sticker"
        elif replied.animation:
            file_id = replied.animation.file_id
            ftype = "animation"

    text = safe_html(" ".join(context.args[1:])) if ftype == "text" else ""
    await save_filter(chat_id, keyword, text, file_id=file_id, ftype=ftype)
    await msg.reply_html(
        f"✅ <b>Filter saved!</b>\n\n"
        f"🔑 Keyword: <code>{safe_html(keyword)}</code>\n"
        f"📦 Type: {ftype}"
    )


async def filters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=False)
    if err:
        await update.message.reply_html(err); return
    rows = await list_filters(chat_id)
    if not rows:
        await update.message.reply_html(
            "📭 <b>No filters set!</b>\n\n"
            "Add one with <code>/filter &lt;keyword&gt; &lt;reply&gt;</code>"
        )
        return
    text = f"📋 <b>Filters in {safe_html(title)} ({len(rows)})</b>\n\n"
    for r in rows:
        kw = r.get("keyword", "?")
        text += f"🔹 <code>{safe_html(kw)}</code> — {r.get('ftype','text')}\n"
    text += "\n💡 Delete with /stop &lt;keyword&gt;"
    await update.message.reply_html(text)


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    msg = update.effective_message
    if not await _is_admin_or_owner_resolved(update, context, chat_id):
        await msg.reply_html("❌ <b>Admins only!</b>")
        return
    if not context.args:
        await msg.reply_html("Usage: <code>/stop &lt;keyword&gt;</code>")
        return
    kw = context.args[0]
    if await delete_filter(chat_id, kw):
        await msg.reply_html(f"🗑️ <b>Filter removed:</b> <code>{safe_html(kw)}</code>")
    else:
        await msg.reply_html(f"❌ No filter named <code>{safe_html(kw)}</code>.")


async def clearfilters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    msg = update.effective_message
    if not await _is_admin_or_owner_resolved(update, context, chat_id):
        await msg.reply_html("❌ <b>Admins only!</b>")
        return
    n = await clear_filters(chat_id)
    await msg.reply_html(f"🧹 <b>Removed {n} filter(s)</b> from this chat.")


async def filter_enforcement_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-reply when a group message contains a saved filter keyword.
    Runs only on normal text (not commands, not in DMs)."""
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not msg.text or chat.type == "private":
        return
    if msg.text.startswith("/"):
        return
    try:
        f = await match_filter(chat.id, msg.text)
    except Exception:
        return
    if not f:
        return
    ftype = f.get("ftype", "text")
    try:
        if ftype == "photo" and f.get("file_id"):
            await msg.reply_photo(photo=f["file_id"], caption=f.get("text", ""),
                                  parse_mode="HTML" if f.get("text") else None)
        elif ftype == "sticker" and f.get("file_id"):
            await msg.reply_sticker(sticker=f["file_id"])
        elif ftype == "animation" and f.get("file_id"):
            await msg.reply_animation(animation=f["file_id"], caption=f.get("text", ""),
                                       parse_mode="HTML" if f.get("text") else None)
        else:
            await msg.reply_html(f.get("text", ""))
    except Exception:
        # Never let a filter reply break the chat flow.
        pass
