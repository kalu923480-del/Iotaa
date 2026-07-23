# Authored By Iota Coders © 2025
"""Personal song favorites stored in MongoDB."""
from __future__ import annotations

from pyrogram import filters
from pyrogram.types import Message

from config import BANNED_USERS
from IotaXMedia import app
from IotaXMedia.core.mongo import mongodb
from IotaXMedia.misc import db
from IotaXMedia.utils.database import is_active_chat
from IotaXMedia.utils.errors import capture_err

favdb = mongodb.favorites


async def _get_user_favs(user_id: int) -> list:
    doc = await favdb.find_one({"user_id": user_id}) or {}
    return list(doc.get("songs") or [])


async def _save_user_favs(user_id: int, songs: list) -> None:
    await favdb.update_one(
        {"user_id": user_id},
        {"$set": {"user_id": user_id, "songs": songs[:50]}},
        upsert=True,
    )


@app.on_message(
    filters.command(["fav", "favorite", "favourite", "savefav"])
    & ~filters.user(list(BANNED_USERS))
)
@capture_err
async def fav_add_cmd(client, message: Message):
    if not message.from_user:
        return
    user_id = message.from_user.id
    title = None
    vidid = None
    link = None

    if len(message.command) > 1:
        title = message.text.split(None, 1)[1].strip()
        if "youtube.com" in title or "youtu.be" in title:
            link = title
            title = title
    elif message.chat and await is_active_chat(message.chat.id):
        got = db.get(message.chat.id)
        if got:
            title = got[0].get("title")
            vidid = got[0].get("vidid")
            if vidid and len(str(vidid)) == 11:
                link = f"https://www.youtube.com/watch?v={vidid}"

    if not title:
        return await message.reply_text(
            "Usage:\n"
            "/fav — save currently playing track\n"
            "/fav <song name or YouTube link>"
        )

    songs = await _get_user_favs(user_id)
    entry = {"title": title, "vidid": vidid or "", "link": link or ""}
    # de-dupe by title/link
    for s in songs:
        if s.get("title") == title or (link and s.get("link") == link):
            return await message.reply_text("Already in your favorites.")
    songs.insert(0, entry)
    await _save_user_favs(user_id, songs)
    return await message.reply_text(f"Saved to favorites:\n{title}")


@app.on_message(
    filters.command(["unfav", "unfavorite", "delfav"])
    & ~filters.user(list(BANNED_USERS))
)
@capture_err
async def fav_remove_cmd(client, message: Message):
    if not message.from_user:
        return
    songs = await _get_user_favs(message.from_user.id)
    if not songs:
        return await message.reply_text("Your favorites list is empty.")
    if len(message.command) < 2:
        return await message.reply_text(
            "Usage: /unfav <number>\nUse /favorites to see numbers."
        )
    try:
        idx = int(message.command[1]) - 1
    except ValueError:
        return await message.reply_text("Number required. Example: /unfav 1")
    if idx < 0 or idx >= len(songs):
        return await message.reply_text("Invalid number.")
    removed = songs.pop(idx)
    await _save_user_favs(message.from_user.id, songs)
    return await message.reply_text(f"Removed: {removed.get('title')}")


@app.on_message(
    filters.command(["favorites", "favs", "mylikes"])
    & ~filters.user(list(BANNED_USERS))
)
@capture_err
async def fav_list_cmd(client, message: Message):
    if not message.from_user:
        return
    songs = await _get_user_favs(message.from_user.id)
    if not songs:
        return await message.reply_text(
            "No favorites yet.\nUse /fav while a song is playing."
        )
    lines = ["Your favorites:\n"]
    for i, s in enumerate(songs[:30], 1):
        title = s.get("title") or "Unknown"
        link = s.get("link") or ""
        if link:
            lines.append(f"{i}. {title}\n   {link}")
        else:
            lines.append(f"{i}. {title}")
    lines.append("\n/unfav <n> to remove")
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3900] + "\n..."
    await message.reply_text(text, disable_web_page_preview=True)
