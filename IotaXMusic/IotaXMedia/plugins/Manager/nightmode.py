# Authored By Iota Coders © 2025
"""Night mode: restrict non-admins from messaging during set hours (UTC)."""
from __future__ import annotations

from datetime import datetime, timezone

from pyrogram import filters
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.types import ChatPermissions, Message

from config import BANNED_USERS, OWNER_ID
from IotaXMedia import app
from IotaXMedia.core.mongo import mongodb
from IotaXMedia.misc import SUDOERS, COMMANDERS
from IotaXMedia.utils.errors import capture_err

nightdb = mongodb.nightmode


def _is_sudo(uid: int) -> bool:
    try:
        return uid == OWNER_ID or uid in SUDOERS
    except Exception:
        return uid == OWNER_ID


async def get_night_settings(chat_id: int) -> dict:
    doc = await nightdb.find_one({"chat_id": chat_id}) or {}
    return {
        "enabled": bool(doc.get("enabled")),
        "start": int(doc.get("start", 22)),  # 22:00 UTC
        "end": int(doc.get("end", 6)),  # 06:00 UTC
    }


async def set_night_settings(chat_id: int, **kwargs) -> dict:
    cur = await get_night_settings(chat_id)
    cur.update(kwargs)
    await nightdb.update_one(
        {"chat_id": chat_id},
        {"$set": {"chat_id": chat_id, **cur}},
        upsert=True,
    )
    return cur


def _in_night_window(start: int, end: int, hour: int) -> bool:
    start = start % 24
    end = end % 24
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    # wraps midnight
    return hour >= start or hour < end


async def _is_admin(client, message: Message) -> bool:
    if not message.from_user:
        return False
    if _is_sudo(message.from_user.id):
        return True
    try:
        m = await client.get_chat_member(message.chat.id, message.from_user.id)
        return m.status in COMMANDERS or m.status in (
            ChatMemberStatus.OWNER,
            ChatMemberStatus.ADMINISTRATOR,
        )
    except Exception:
        return False


@app.on_message(
    filters.command(["nightmode", "nm"])
    & filters.group
    & ~filters.user(list(BANNED_USERS))
)
@capture_err
async def nightmode_cmd(client, message: Message):
    if not await _is_admin(client, message):
        return await message.reply_text("Only admins can manage night mode.")

    args = message.command[1:] if len(message.command) > 1 else []
    chat_id = message.chat.id
    settings = await get_night_settings(chat_id)

    if not args:
        status = "ON" if settings["enabled"] else "OFF"
        return await message.reply_text(
            f"Night mode: {status}\n"
            f"Window (UTC): {settings['start']:02d}:00 → {settings['end']:02d}:00\n\n"
            "Usage:\n"
            "/nightmode on\n"
            "/nightmode off\n"
            "/nightmode set 22 6   (start_hour end_hour UTC)"
        )

    cmd = args[0].lower()
    if cmd in ("on", "enable", "yes", "1"):
        await set_night_settings(chat_id, enabled=True)
        return await message.reply_text(
            f"Night mode enabled.\n"
            f"Non-admins restricted {settings['start']:02d}:00–{settings['end']:02d}:00 UTC."
        )
    if cmd in ("off", "disable", "no", "0"):
        await set_night_settings(chat_id, enabled=False)
        # restore default send permissions for all
        try:
            await client.set_chat_permissions(
                chat_id,
                ChatPermissions(can_send_messages=True),
            )
        except Exception:
            pass
        return await message.reply_text("Night mode disabled.")
    if cmd == "set" and len(args) >= 3:
        try:
            start = int(args[1]) % 24
            end = int(args[2]) % 24
        except ValueError:
            return await message.reply_text("Hours must be numbers 0-23.")
        s = await set_night_settings(chat_id, start=start, end=end)
        return await message.reply_text(
            f"Night window set to {s['start']:02d}:00 → {s['end']:02d}:00 UTC."
        )
    return await message.reply_text("Unknown option. Use /nightmode for help.")


@app.on_message(filters.group & filters.incoming & ~filters.service, group=45)
async def nightmode_enforcer(client, message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if not message.from_user:
        return
    try:
        settings = await get_night_settings(message.chat.id)
        if not settings["enabled"]:
            return
        hour = datetime.now(timezone.utc).hour
        if not _in_night_window(settings["start"], settings["end"], hour):
            return
        if await _is_admin(client, message):
            return
        # soft-delete messages from non-admins during night window
        try:
            await message.delete()
        except Exception:
            pass
    except Exception:
        pass
