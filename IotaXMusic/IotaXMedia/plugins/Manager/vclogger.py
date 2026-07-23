# Authored By Iota Coders © 2025
"""Per-chat voice chat start/end logging."""
from pyrogram import filters
from pyrogram.enums import ChatMemberStatus, ChatType, MessageServiceType
from pyrogram.types import Message

from IotaXMedia import app
from IotaXMedia.core.mongo import mongodb
from IotaXMedia.misc import SUDOERS, COMMANDERS
from config import OWNER_ID, LOGGER_ID

vclogger_db = mongodb.vclogger


def _is_sudo(uid: int) -> bool:
    try:
        return uid == OWNER_ID or uid in SUDOERS
    except Exception:
        return uid == OWNER_ID


async def is_vclogger_on(chat_id: int) -> bool:
    data = await vclogger_db.find_one({"chat_id": chat_id})
    return bool(data and data.get("enabled"))


async def set_vclogger(chat_id: int, enabled: bool) -> None:
    await vclogger_db.update_one(
        {"chat_id": chat_id},
        {"$set": {"chat_id": chat_id, "enabled": enabled}},
        upsert=True,
    )


async def _is_chat_admin(client, message: Message) -> bool:
    if not message.from_user:
        return False
    if _is_sudo(message.from_user.id):
        return True
    if message.chat.type in (ChatType.PRIVATE, ChatType.BOT):
        return False
    try:
        member = await client.get_chat_member(message.chat.id, message.from_user.id)
        return member.status in COMMANDERS or member.status in (
            ChatMemberStatus.OWNER,
            ChatMemberStatus.ADMINISTRATOR,
        )
    except Exception:
        return False


@app.on_message(filters.command(["vclogger", "vclog"]) & filters.group)
async def vclogger_cmd(client, message: Message):
    if not await _is_chat_admin(client, message):
        return await message.reply_text("Only group admins can use this.")

    args = message.command[1:] if len(message.command) > 1 else []
    arg = (args[0] if args else "").lower()
    chat_id = message.chat.id

    if arg in ("on", "enable", "yes", "true", "1"):
        await set_vclogger(chat_id, True)
        return await message.reply_text(
            "VC logger enabled.\nVoice chat start/end events will be logged."
        )
    if arg in ("off", "disable", "no", "false", "0"):
        await set_vclogger(chat_id, False)
        return await message.reply_text("VC logger disabled.")

    status = "ON" if await is_vclogger_on(chat_id) else "OFF"
    return await message.reply_text(
        f"VC logger is currently {status}.\n\n"
        "Usage:\n"
        "/vclogger on — enable voice chat logging\n"
        "/vclogger off — disable voice chat logging"
    )


@app.on_message(filters.service & filters.group, group=50)
async def vclog_service_handler(client, message: Message):
    chat_id = message.chat.id
    try:
        if not await is_vclogger_on(chat_id):
            return
    except Exception:
        return

    st = message.service
    title = message.chat.title or str(chat_id)
    who = ""
    if message.from_user:
        who = message.from_user.mention or str(message.from_user.id)

    text = None
    # Cover common VC-related service types across pyrogram/kurigram versions
    name = getattr(st, "name", str(st)) if st is not None else ""
    value = str(st).lower() if st is not None else ""

    if "VIDEO_CHAT_STARTED" in name or "voice_chat_started" in value or "video_chat_started" in value:
        text = f"VC started in {title}\nBy: {who or 'unknown'}\nChat: {chat_id}"
    elif "VIDEO_CHAT_ENDED" in name or "voice_chat_ended" in value or "video_chat_ended" in value:
        text = f"VC ended in {title}\nBy: {who or 'unknown'}\nChat: {chat_id}"
    elif "VIDEO_CHAT_SCHEDULED" in name or "voice_chat_scheduled" in value:
        text = f"VC scheduled in {title}\nBy: {who or 'unknown'}\nChat: {chat_id}"
    elif "VIDEO_CHAT_MEMBERS_INVITED" in name or "voice_chat_members" in value:
        text = f"VC members invited in {title}\nBy: {who or 'unknown'}\nChat: {chat_id}"

    if not text:
        return

    # Prefer logging to LOGGER_ID; fall back to the same chat
    targets = []
    if LOGGER_ID:
        targets.append(LOGGER_ID)
    targets.append(chat_id)

    sent = False
    for target in targets:
        try:
            await client.send_message(target, text)
            sent = True
            break
        except Exception:
            continue
    if not sent:
        return
