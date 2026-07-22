# Authored By Iota Coders © 2025
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message, User

from IotaXMedia import app


async def extract_user(m: Message) -> User:
    if m.reply_to_message and m.reply_to_message.from_user:
        return m.reply_to_message.from_user

    entities = m.entities or []
    if not m.command or len(m.command) < 2:
        if entities:
            # Prefer first text mention if present
            for ent in entities:
                if ent.type == MessageEntityType.TEXT_MENTION and ent.user:
                    return ent.user
        raise ValueError("No user specified. Reply to a user or pass @username/id.")

    # Prefer text-mention entity when present
    for ent in entities:
        if ent.type == MessageEntityType.TEXT_MENTION and ent.user:
            return ent.user

    target = m.command[1]
    if target.isdecimal():
        return await app.get_users(int(target))
    return await app.get_users(target)