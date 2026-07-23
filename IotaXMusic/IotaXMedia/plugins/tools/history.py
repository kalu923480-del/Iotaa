# Authored By Iota Coders © 2025
"""Per-chat play history."""
from __future__ import annotations

from pyrogram import filters
from pyrogram.types import Message

from config import BANNED_USERS
from IotaXMedia import app
from IotaXMedia.core.mongo import mongodb
from IotaXMedia.misc import db
from IotaXMedia.utils.admin_filters import admin_filter
from IotaXMedia.utils.errors import capture_err

histdb = mongodb.play_history


async def push_history(chat_id: int, title: str, vidid: str = "", by: str = "") -> None:
    """Call from stream layer optionally; also exposed for manual use."""
    entry = {
        "title": title,
        "vidid": vidid or "",
        "by": by or "",
    }
    doc = await histdb.find_one({"chat_id": chat_id}) or {}
    items = list(doc.get("items") or [])
    # skip exact consecutive duplicate
    if items and items[0].get("title") == title and items[0].get("vidid") == vidid:
        return
    items.insert(0, entry)
    await histdb.update_one(
        {"chat_id": chat_id},
        {"$set": {"chat_id": chat_id, "items": items[:40]}},
        upsert=True,
    )


async def get_history(chat_id: int) -> list:
    doc = await histdb.find_one({"chat_id": chat_id}) or {}
    return list(doc.get("items") or [])


@app.on_message(
    filters.command(["history", "phistory", "played"])
    & filters.group
    & ~filters.user(list(BANNED_USERS))
)
@capture_err
async def history_cmd(client, message: Message):
    items = await get_history(message.chat.id)
    # merge current if playing
    got = db.get(message.chat.id)
    lines = ["Play history:\n"]
    if got:
        cur = got[0]
        lines.append(
            f"Now: {cur.get('title', 'Unknown')} (by {cur.get('by', '?')})"
        )
        # also push current into history for convenience
        try:
            await push_history(
                message.chat.id,
                cur.get("title", ""),
                str(cur.get("vidid") or ""),
                str(cur.get("by") or ""),
            )
            items = await get_history(message.chat.id)
        except Exception:
            pass
    if not items:
        lines.append("No history yet. Play some songs first.")
    else:
        for i, it in enumerate(items[:20], 1):
            title = it.get("title") or "Unknown"
            by = it.get("by") or ""
            vid = it.get("vidid") or ""
            extra = f" — {by}" if by else ""
            if vid and len(str(vid)) == 11:
                lines.append(
                    f"{i}. {title}{extra}\n   https://youtu.be/{vid}"
                )
            else:
                lines.append(f"{i}. {title}{extra}")
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3900] + "\n..."
    await message.reply_text(text, disable_web_page_preview=True)


@app.on_message(
    filters.command(["clearhistory", "chistory"])
    & filters.group
    & admin_filter
    & ~filters.user(list(BANNED_USERS))
)
@capture_err
async def clear_history_cmd(client, message: Message):
    await histdb.delete_one({"chat_id": message.chat.id})
    await message.reply_text("Play history cleared for this chat.")
