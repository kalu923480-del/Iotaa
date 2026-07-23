# Authored By Iota Coders © 2025
"""Queue management: clear, remove, playnext, replay, nowplaying."""
from __future__ import annotations

from pyrogram import filters
from pyrogram.types import Message

from config import BANNED_USERS
from IotaXMedia import app
from IotaXMedia.core.call import StreamController
from IotaXMedia.misc import db
from IotaXMedia.utils.admin_filters import admin_filter
from IotaXMedia.utils.database import get_cmode, is_active_chat, is_music_playing
from IotaXMedia.utils.decorators.language import language


def _chat_id_from_message(message: Message) -> int:
    return message.chat.id


async def _resolve_chat(message: Message) -> int | None:
    cmd = (message.command[0] if message.command else "").lower()
    if cmd.startswith("c"):
        chat_id = await get_cmode(message.chat.id)
        return chat_id
    return message.chat.id


@app.on_message(
    filters.command(["clearqueue", "cq", "cclear", "clearq"])
    & filters.group
    & admin_filter
    & ~filters.user(list(BANNED_USERS))
)
@language
async def clear_queue_cmd(client, message: Message, _):
    chat_id = await _resolve_chat(message)
    if chat_id is None:
        return await message.reply_text("Channel play mode is not set.")
    if not await is_active_chat(chat_id):
        return await message.reply_text("Nothing is playing right now.")
    got = db.get(chat_id)
    if not got or len(got) <= 1:
        return await message.reply_text("Queue is empty (only current track or nothing).")
    current = got[0]
    db[chat_id] = [current]
    return await message.reply_text(
        f"Queue cleared.\nKept currently playing: {current.get('title', 'Unknown')}"
    )


@app.on_message(
    filters.command(["remove", "rm", "cremove"])
    & filters.group
    & admin_filter
    & ~filters.user(list(BANNED_USERS))
)
@language
async def remove_queue_cmd(client, message: Message, _):
    chat_id = await _resolve_chat(message)
    if chat_id is None:
        return await message.reply_text("Channel play mode is not set.")
    got = db.get(chat_id)
    if not got or len(got) <= 1:
        return await message.reply_text("No queued tracks to remove.")

    if len(message.command) < 2:
        return await message.reply_text(
            "Usage: /remove <position>\n"
            "Example: /remove 2  (removes 2nd item in queue, not current)"
        )
    try:
        pos = int(message.command[1])
    except ValueError:
        return await message.reply_text("Position must be a number (starting from 1 for next track).")

    # position 1 = next track (index 1 in list)
    if pos < 1 or pos >= len(got):
        return await message.reply_text(
            f"Invalid position. Queue has {max(0, len(got) - 1)} upcoming track(s)."
        )
    removed = got.pop(pos)
    title = removed.get("title", "Unknown")
    return await message.reply_text(f"Removed from queue #{pos}: {title}")


@app.on_message(
    filters.command(["playnext", "pn", "cplaynext"])
    & filters.group
    & admin_filter
    & ~filters.user(list(BANNED_USERS))
)
@language
async def playnext_cmd(client, message: Message, _):
    """Move a queued track to play next (position 1)."""
    chat_id = await _resolve_chat(message)
    if chat_id is None:
        return await message.reply_text("Channel play mode is not set.")
    got = db.get(chat_id)
    if not got or len(got) <= 2:
        return await message.reply_text(
            "Need at least 2 upcoming tracks, or use /remove and re-queue."
        )
    if len(message.command) < 2:
        return await message.reply_text(
            "Usage: /playnext <position>\n"
            "Example: /playnext 3  (move 3rd upcoming track to play next)"
        )
    try:
        pos = int(message.command[1])
    except ValueError:
        return await message.reply_text("Position must be a number.")
    if pos < 1 or pos >= len(got):
        return await message.reply_text(
            f"Invalid position. Queue has {max(0, len(got) - 1)} upcoming track(s)."
        )
    item = got.pop(pos)
    got.insert(1, item)
    return await message.reply_text(
        f"Will play next: {item.get('title', 'Unknown')}"
    )


@app.on_message(
    filters.command(["replay", "restart", "creplay"])
    & filters.group
    & admin_filter
    & ~filters.user(list(BANNED_USERS))
)
@language
async def replay_cmd(client, message: Message, _):
    chat_id = await _resolve_chat(message)
    if chat_id is None:
        return await message.reply_text("Channel play mode is not set.")
    if not await is_active_chat(chat_id):
        return await message.reply_text("Nothing is playing right now.")
    got = db.get(chat_id)
    if not got:
        return await message.reply_text("Queue is empty.")

    # Reset played counter and re-skip to same track by inserting copy at front
    current = dict(got[0])
    current["played"] = 0
    # Keep current as index 0; StreamController.skip_stream restarts file
    file_path = current.get("file")
    video = str(current.get("streamtype", "")).lower() == "video"
    if not file_path:
        return await message.reply_text("Cannot replay this stream type.")
    try:
        await StreamController.skip_stream(chat_id, file_path, video=video)
        return await message.reply_text(
            f"Replaying: {current.get('title', 'Unknown')}"
        )
    except Exception as e:
        return await message.reply_text(f"Replay failed: {e}")


@app.on_message(
    filters.command(["np", "nowplaying", "now", "cnp"])
    & filters.group
    & ~filters.user(list(BANNED_USERS))
)
@language
async def now_playing_cmd(client, message: Message, _):
    chat_id = await _resolve_chat(message)
    if chat_id is None:
        return await message.reply_text("Channel play mode is not set.")
    if not await is_active_chat(chat_id):
        return await message.reply_text("Nothing is playing right now.")
    got = db.get(chat_id)
    if not got:
        return await message.reply_text("Nothing is playing right now.")
    cur = got[0]
    title = cur.get("title", "Unknown")
    dur = cur.get("dur", "Unknown")
    by = cur.get("by", "Unknown")
    stype = cur.get("streamtype", "audio")
    vidid = cur.get("vidid", "")
    remaining = max(0, len(got) - 1)
    status = "Playing" if await is_music_playing(chat_id) else "Paused"
    link = ""
    if vidid and len(str(vidid)) == 11:
        link = f"\nYouTube: https://www.youtube.com/watch?v={vidid}"
    text = (
        f"Now playing ({status})\n\n"
        f"Title: {title}\n"
        f"Duration: {dur}\n"
        f"Type: {stype}\n"
        f"Requested by: {by}\n"
        f"In queue: {remaining} more"
        f"{link}"
    )
    return await message.reply_text(text, disable_web_page_preview=True)
