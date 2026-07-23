# Authored By Iota Coders © 2025
"""Per-chat auto-approve for join requests."""
from pyrogram import filters
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.types import ChatJoinRequest, Message

from IotaXMedia import app
from IotaXMedia.core.mongo import mongodb
from IotaXMedia.misc import SUDOERS, COMMANDERS
from config import OWNER_ID

autoapprove_db = mongodb.autoapprove


def _is_sudo(uid: int) -> bool:
    try:
        return uid == OWNER_ID or uid in SUDOERS
    except Exception:
        return uid == OWNER_ID


async def is_autoapprove_on(chat_id: int) -> bool:
    data = await autoapprove_db.find_one({"chat_id": chat_id})
    return bool(data and data.get("enabled"))


async def set_autoapprove(chat_id: int, enabled: bool) -> None:
    await autoapprove_db.update_one(
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


@app.on_message(filters.command(["autoapprove", "autoaccept"]) & filters.group)
async def autoapprove_cmd(client, message: Message):
    if not await _is_chat_admin(client, message):
        return await message.reply_text("Only group admins can use this.")

    args = message.command[1:] if len(message.command) > 1 else []
    arg = (args[0] if args else "").lower()
    chat_id = message.chat.id

    if arg in ("on", "enable", "yes", "true", "1"):
        await set_autoapprove(chat_id, True)
        return await message.reply_text(
            "Auto-approve enabled.\nNew join requests will be approved automatically."
        )
    if arg in ("off", "disable", "no", "false", "0"):
        await set_autoapprove(chat_id, False)
        return await message.reply_text("Auto-approve disabled.")

    status = "ON" if await is_autoapprove_on(chat_id) else "OFF"
    return await message.reply_text(
        f"Auto-approve is currently {status}.\n\n"
        "Usage:\n"
        "/autoapprove on — approve join requests automatically\n"
        "/autoapprove off — disable auto approval"
    )


@app.on_chat_join_request()
async def autoapprove_join_request(client, request: ChatJoinRequest):
    chat_id = request.chat.id
    try:
        if not await is_autoapprove_on(chat_id):
            return
        await client.approve_chat_join_request(chat_id, request.from_user.id)
    except Exception:
        pass
