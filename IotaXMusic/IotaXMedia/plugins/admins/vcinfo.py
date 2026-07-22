# Authored By Iota Coders © 2025
from pyrogram import filters
from pyrogram.types import Message

from config import BANNED_USERS
from IotaXMedia import app
from IotaXMedia.core.call import StreamController
from IotaXMedia.utils.database import get_volume, group_assistant
from IotaXMedia.utils.admin_filters import admin_filter


@app.on_message(filters.command(["vcinfo", "vcmembers"]) & filters.group & admin_filter & ~filters.user(list(BANNED_USERS)))
async def vc_info(client, message: Message):
    chat_id = message.chat.id
    try:
        assistant = await group_assistant(StreamController, chat_id)
        participants = await assistant.get_participants(chat_id)

        if not participants:
            return await message.reply_text("❌ No users found in the voice chat.")

        bot_vol = await get_volume(chat_id)
        msg_lines = [
            "🎧 <b>VC Members Info:</b>\n",
            f"🔊 <b>Bot stream volume:</b> <code>{bot_vol}%</code>\n",
        ]
        for p in participants:
            try:
                user = await app.get_users(p.user_id)
                name = user.mention if user else f"<code>{p.user_id}</code>"
            except Exception:
                name = f"<code>{p.user_id}</code>"

            mute_status = "🔇" if getattr(p, "muted", False) or getattr(p, "is_muted", False) else "👤"
            screen_status = "🖥️" if getattr(p, "screen_sharing", False) else ""

            info = f"{mute_status} {name}"
            if screen_status:
                info += f" | {screen_status}"
            msg_lines.append(info)

        msg_lines.append(f"\n👥 Total: <b>{len(participants)}</b>")
        await message.reply_text("\n".join(msg_lines))
    except Exception as e:
        await message.reply_text(f"❌ Failed to fetch VC info.\n<b>Error:</b> {e}")
