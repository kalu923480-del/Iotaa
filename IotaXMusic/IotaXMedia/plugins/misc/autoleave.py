# Authored By Iota Coders © 2025
import asyncio
from datetime import datetime

from pyrogram.enums import ChatType

import config
from IotaXMedia import app
from IotaXMedia.core.call import StreamController, autoend
from IotaXMedia.utils.database import get_client, group_assistant, is_active_chat, is_autoend


async def auto_leave():
    if config.AUTO_LEAVING_ASSISTANT:
        while not await asyncio.sleep(config.AUTO_LEAVE_ASSISTANT_TIME):
            from IotaXMedia.core.userbot import assistants

            for num in assistants:
                client = await get_client(num)
                left = 0
                try:
                    async for i in client.get_dialogs():
                        if i.chat.type in [
                            ChatType.SUPERGROUP,
                            ChatType.GROUP,
                            ChatType.CHANNEL,
                        ]:
                            protected = {config.LOGGER_ID} | set(
                                getattr(config, "PROTECTED_CHAT_IDS", set()) or set()
                            )
                            if i.chat.id not in protected:
                                if left == 20:
                                    continue
                                if not await is_active_chat(i.chat.id):
                                    try:
                                        await client.leave_chat(i.chat.id)
                                        left += 1
                                    except:
                                        continue
                except:
                    pass


asyncio.create_task(auto_leave())


async def auto_end():
    """Leave VC only if empty (assistant alone) for several consecutive checks."""
    empty_streak: dict[int, int] = {}
    NEED_STREAK = 6  # 6 * 5s = ~30s alone before leaving

    while not await asyncio.sleep(5):
        ender = await is_autoend()
        if not ender:
            empty_streak.clear()
            continue

        # Snapshot active chats from StreamController
        active_ids = list(getattr(StreamController, "active_calls", set()) or [])
        for chat_id in active_ids:
            try:
                if not await is_active_chat(chat_id):
                    empty_streak.pop(chat_id, None)
                    continue
                assistant = await group_assistant(StreamController, chat_id)
                participants = await assistant.get_participants(chat_id)
                # Count non-assistant humans
                human = 0
                for p in participants or []:
                    uid = getattr(p, "user_id", None)
                    if uid and not getattr(p, "is_self", False):
                        human += 1
                if human <= 0:
                    empty_streak[chat_id] = empty_streak.get(chat_id, 0) + 1
                else:
                    empty_streak[chat_id] = 0
                    continue

                if empty_streak.get(chat_id, 0) < NEED_STREAK:
                    continue

                empty_streak.pop(chat_id, None)
                autoend.pop(chat_id, None)
                try:
                    await StreamController.stop_stream(chat_id)
                except Exception:
                    continue
                try:
                    await app.send_message(
                        chat_id,
                        "» ʙᴏᴛ ʟᴇғᴛ ᴠᴄ — ɴᴏ ʟɪsᴛᴇɴᴇʀs ғᴏʀ ~30s.",
                    )
                except Exception:
                    continue
            except Exception:
                continue

        # Drop streaks for chats no longer active
        for cid in list(empty_streak.keys()):
            if cid not in active_ids:
                empty_streak.pop(cid, None)


asyncio.create_task(auto_end())
