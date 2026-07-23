# Authored By Iota Coders © 2025
from functools import wraps
from typing import Callable, Awaitable, Any

from pyrogram import Client
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.types import Message

from config import BOT_USERNAME, OWNER_ID
from IotaXMedia.misc import SUDOERS, COMMANDERS


Handler = Callable[..., Awaitable[Any]]


def _is_sudo(user_id: int) -> bool:
    try:
        if user_id == OWNER_ID:
            return True
        return user_id in SUDOERS
    except Exception:
        return user_id == OWNER_ID


def _is_private(message: Message) -> bool:
    return message.chat.type in (ChatType.PRIVATE, ChatType.BOT)


# ────────────────────────────────────────────────────────────
# generic admin_required(priv1, priv2, …)
# ────────────────────────────────────────────────────────────
def admin_required(*privileges: str):
    """
    Usage:

    @app.on_message(filters.command("promote"))
    @admin_required("can_promote_members")
    async def handler(client, message): ...
    """

    def decorator(func: Handler) -> Handler:
        @wraps(func)
        async def wrapper(client: Client, message: Message, *a, **kw):
            if not message.from_user:
                return await message.reply_text("Unhide your account to use this command.")

            if _is_private(message):
                if _is_sudo(message.from_user.id):
                    return await func(client, message, *a, **kw)
                return await message.reply_text("Use this command in groups only.")

            if _is_sudo(message.from_user.id):
                return await func(client, message, *a, **kw)

            try:
                member = await message.chat.get_member(message.from_user.id)
            except ValueError:
                return await message.reply_text("Use this command in groups only.")
            except Exception:
                return await message.reply_text("Could not verify admin rights.")

            allowed = False
            if member.status == ChatMemberStatus.OWNER:
                allowed = True
            elif member.status == ChatMemberStatus.ADMINISTRATOR:
                if member.privileges:
                    allowed = all(
                        getattr(member.privileges, p, False) for p in privileges
                    )

            if not allowed:
                missing = ", ".join(privileges) or "admin"
                return await message.reply_text(f"You lack {missing} permission.")
            return await func(client, message, *a, **kw)

        return wrapper

    return decorator


# ────────────────────────────────────────────────────────────
# Bot capability decorators
# ────────────────────────────────────────────────────────────
def _require_bot_priv(flag: str, friendly: str):
    def deco(func: Handler) -> Handler:
        @wraps(func)
        async def inner(client: Client, message: Message, *a, **kw):
            if _is_private(message):
                return await message.reply_text("Use this command in groups only.")
            try:
                me_user = await client.get_me()
                bot_id = me_user.id
            except Exception:
                bot_id = BOT_USERNAME
            try:
                me = await client.get_chat_member(message.chat.id, bot_id)
            except Exception:
                title = message.chat.title or "this chat"
                return await message.reply_text(
                    f"I don't have the right {friendly} in {title}."
                )
            if not (
                me.status == ChatMemberStatus.ADMINISTRATOR
                and getattr(me.privileges, flag, False)
            ):
                title = message.chat.title or "this chat"
                return await message.reply_text(
                    f"I don't have the right {friendly} in {title}."
                )
            return await func(client, message, *a, **kw)

        return inner

    return deco


bot_admin = _require_bot_priv("can_manage_chat", "to manage chat")
bot_can_ban = _require_bot_priv("can_restrict_members", "to restrict members")
bot_can_change_info = _require_bot_priv("can_change_info", "to change group info")
bot_can_promote = _require_bot_priv("can_promote_members", "to promote members")
bot_can_pin = _require_bot_priv("can_pin_messages", "to pin messages")
bot_can_del = _require_bot_priv("can_delete_messages", "to delete messages")


# ────────────────────────────────────────────────────────────
# User‑side decorators
# ────────────────────────────────────────────────────────────
def _user_lacks_right(message: Message, text: str):
    return message.reply_text(text)


def user_admin(func: Handler) -> Handler:
    @wraps(func)
    async def wrapper(client: Client, message: Message, *a, **kw):
        if _is_private(message):
            return await message.reply("Use this command in groups only.")

        if message.sender_chat:
            if message.sender_chat.id == message.chat.id:
                return await message.reply(
                    "Anonymous admin: please switch to your user account."
                )
            return await message.reply_text("You are not an admin.")

        if not message.from_user:
            return await message.reply_text("Unhide your account to use this command.")

        user_id = message.from_user.id
        if _is_sudo(user_id):
            return await func(client, message, *a, **kw)

        try:
            member = await client.get_chat_member(message.chat.id, user_id)
        except Exception:
            return await message.reply_text("Could not verify admin rights.")

        if member.status not in COMMANDERS:
            return await message.reply_text("You are not an admin.")
        return await func(client, message, *a, **kw)

    return wrapper


def _user_priv_required(flag: str, friendly: str):
    def deco(func: Handler) -> Handler:
        @wraps(func)
        async def inner(client: Client, message: Message, *a, **kw):
            if _is_private(message):
                return await message.reply_text("Use this command in groups only.")
            if not message.from_user:
                return await message.reply_text("Unhide your account to use this command.")
            if _is_sudo(message.from_user.id):
                return await func(client, message, *a, **kw)
            try:
                user = await client.get_chat_member(
                    message.chat.id, message.from_user.id
                )
            except Exception:
                return await message.reply_text("Could not verify permissions.")
            if user.status not in COMMANDERS:
                return await message.reply_text(f"You lack the right to {friendly}.")
            if not getattr(user.privileges, flag, False) and user.status != ChatMemberStatus.OWNER:
                return await message.reply_text(f"You lack the right to {friendly}.")
            return await func(client, message, *a, **kw)

        return inner

    return deco


user_can_ban = _user_priv_required("can_restrict_members", "restrict users")
user_can_del = _user_priv_required("can_delete_messages", "delete messages")
user_can_change_info = _user_priv_required("can_change_info", "change group info")
user_can_promote = _user_priv_required("can_promote_members", "promote users")
