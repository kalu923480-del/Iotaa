# Authored By Iota Coders © 2025
"""Simple AFK system for groups."""
from __future__ import annotations

import time
from pyrogram import filters
from pyrogram.types import Message

from config import BANNED_USERS
from IotaXMedia import app
from IotaXMedia.core.mongo import mongodb
from IotaXMedia.utils.errors import capture_err

afkdb = mongodb.afk


def _fmt_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s or not parts:
        parts.append(f"{s}s")
    return " ".join(parts)


@app.on_message(
    filters.command(["afk"])
    & filters.group
    & ~filters.user(list(BANNED_USERS))
)
@capture_err
async def afk_set(client, message: Message):
    if not message.from_user:
        return
    reason = ""
    if len(message.command) > 1:
        reason = message.text.split(None, 1)[1].strip()[:200]
    await afkdb.update_one(
        {"user_id": message.from_user.id},
        {
            "$set": {
                "user_id": message.from_user.id,
                "reason": reason,
                "since": int(time.time()),
                "chat_id": message.chat.id,
            }
        },
        upsert=True,
    )
    name = message.from_user.mention or message.from_user.first_name
    if reason:
        await message.reply_text(f"{name} is now AFK.\nReason: {reason}")
    else:
        await message.reply_text(f"{name} is now AFK.")


@app.on_message(filters.group & filters.incoming & ~filters.bot & ~filters.via_bot, group=40)
async def afk_watcher(client, message: Message):
    if not message.from_user:
        return
    uid = message.from_user.id

    # Coming back from AFK
    if message.text and not message.text.startswith("/"):
        doc = await afkdb.find_one({"user_id": uid})
        if doc:
            since = int(doc.get("since") or time.time())
            dur = _fmt_duration(time.time() - since)
            await afkdb.delete_one({"user_id": uid})
            name = message.from_user.mention or message.from_user.first_name
            try:
                await message.reply_text(f"Welcome back {name}.\nYou were AFK for {dur}.")
            except Exception:
                pass

    # Mention / reply to AFK user
    targets = set()
    if message.reply_to_message and message.reply_to_message.from_user:
        targets.add(message.reply_to_message.from_user.id)
    if message.entities:
        text = message.text or message.caption or ""
        for ent in message.entities:
            if ent.type.name == "MENTION":
                # can't easily resolve without username map; skip bare mentions
                pass
            elif ent.type.name == "TEXT_MENTION" and ent.user:
                targets.add(ent.user.id)

    for tid in targets:
        if tid == uid:
            continue
        doc = await afkdb.find_one({"user_id": tid})
        if not doc:
            continue
        since = int(doc.get("since") or time.time())
        dur = _fmt_duration(time.time() - since)
        reason = doc.get("reason") or "No reason"
        try:
            user = await client.get_users(tid)
            name = user.mention if user else str(tid)
        except Exception:
            name = str(tid)
        try:
            await message.reply_text(
                f"{name} is AFK ({dur}).\nReason: {reason}"
            )
        except Exception:
            pass
