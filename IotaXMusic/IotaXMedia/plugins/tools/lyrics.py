# Authored By Iota Coders © 2025
"""Fetch song lyrics via public lyrics.ovh API."""
from __future__ import annotations

import aiohttp
from pyrogram import filters
from pyrogram.types import Message

from config import BANNED_USERS
from IotaXMedia import app
from IotaXMedia.misc import db
from IotaXMedia.utils.database import is_active_chat
from IotaXMedia.utils.errors import capture_err


async def _fetch_lyrics(artist: str, title: str) -> str | None:
    url = f"https://api.lyrics.ovh/v1/{artist}/{title}"
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                lyrics = (data or {}).get("lyrics")
                if lyrics and lyrics.strip():
                    return lyrics.strip()
    except Exception:
        return None
    return None


async def _search_lyrics_query(query: str) -> str | None:
    """Try artist - title split, then title-only guess."""
    q = (query or "").strip()
    if not q:
        return None
    # Common patterns: "artist - title" or "title by artist"
    artist, title = "", q
    if " - " in q:
        artist, title = [x.strip() for x in q.split(" - ", 1)]
    elif " by " in q.lower():
        parts = q.lower().split(" by ", 1)
        # original casing approx
        idx = q.lower().find(" by ")
        title = q[:idx].strip()
        artist = q[idx + 4 :].strip()
    if artist and title:
        lyr = await _fetch_lyrics(artist, title)
        if lyr:
            return lyr
    # fallback: unknown artist
    for a in (artist or "Unknown", "Various Artists"):
        lyr = await _fetch_lyrics(a, title or q)
        if lyr:
            return lyr
    return None


@app.on_message(
    filters.command(["lyrics", "lyric", "ly"])
    & ~filters.user(list(BANNED_USERS))
)
@capture_err
async def lyrics_cmd(client, message: Message):
    query = None
    if len(message.command) > 1:
        query = message.text.split(None, 1)[1].strip()
    elif message.reply_to_message and (
        message.reply_to_message.text or message.reply_to_message.caption
    ):
        query = (
            message.reply_to_message.text or message.reply_to_message.caption or ""
        ).strip()
    else:
        # try current playing track in groups
        if message.chat and message.chat.id:
            chat_id = message.chat.id
            if await is_active_chat(chat_id):
                got = db.get(chat_id)
                if got:
                    query = got[0].get("title")

    if not query:
        return await message.reply_text(
            "Usage:\n"
            "/lyrics artist - song name\n"
            "/lyrics <song name>\n"
            "Or reply to a message / use while music is playing."
        )

    wait = await message.reply_text(f"Searching lyrics for:\n{query}")
    lyrics = await _search_lyrics_query(query)
    if not lyrics:
        return await wait.edit_text(
            "Lyrics not found.\nTry: /lyrics Artist - Song Title"
        )

    header = f"Lyrics: {query}\n\n"
    text = header + lyrics
    # Telegram message limit ~4096
    if len(text) > 4000:
        text = text[:3900] + "\n\n... (truncated)"
    await wait.edit_text(text)
