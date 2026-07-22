# Authored By Iota Coders © 2025
"""VC stream volume control — /volume, /volup, /voldown."""
from pyrogram import filters
from pyrogram.types import Message

from IotaXMedia import app
from IotaXMedia.core.call import StreamController
from IotaXMedia.utils.database import get_volume, is_active_chat
from IotaXMedia.utils.decorators import AdminRightsCheck
from IotaXMedia.utils.inline import close_markup
from config import BANNED_USERS, DEFAULT_VOLUME, MAX_VOLUME, MIN_VOLUME, VOLUME_STEP


def _bar(vol: int) -> str:
    # 0–200 → 10-block bar
    filled = max(0, min(10, round(vol / 20)))
    return "█" * filled + "░" * (10 - filled)


@app.on_message(
    filters.command(["volume", "vol", "cvolume", "cvol"])
    & filters.group
    & ~filters.user(list(BANNED_USERS))
)
@AdminRightsCheck
async def volume_cmd(cli, message: Message, _, chat_id):
    if not await is_active_chat(chat_id):
        return await message.reply_text(_["general_5"])

    args = message.command[1:] if message.command else []
    current = await get_volume(chat_id)

    if not args:
        return await message.reply_text(
            f"**Volume:** `{current}%`\n"
            f"`{_bar(current)}`\n\n"
            f"• `/volume <{MIN_VOLUME}-{MAX_VOLUME}>` set level\n"
            f"• `/volup` / `/voldown` step ±{VOLUME_STEP}\n"
            f"• Default: `{DEFAULT_VOLUME}`",
            reply_markup=close_markup(_),
        )

    raw = args[0].strip().lower().replace("%", "")
    if raw in ("up", "+"):
        target = current + VOLUME_STEP
    elif raw in ("down", "-"):
        target = current - VOLUME_STEP
    else:
        try:
            target = int(float(raw))
        except ValueError:
            return await message.reply_text(
                f"❌ Use a number `{MIN_VOLUME}`–`{MAX_VOLUME}` (e.g. `/volume 80`)."
            )

    vol = await StreamController.change_volume(chat_id, target)
    await message.reply_text(
        f"**Volume set to** `{vol}%`\n`{_bar(vol)}`\n"
        f"by {message.from_user.mention}",
        reply_markup=close_markup(_),
    )


@app.on_message(
    filters.command(["volup", "volumeup", "vup"])
    & filters.group
    & ~filters.user(list(BANNED_USERS))
)
@AdminRightsCheck
async def volume_up(cli, message: Message, _, chat_id):
    if not await is_active_chat(chat_id):
        return await message.reply_text(_["general_5"])
    current = await get_volume(chat_id)
    vol = await StreamController.change_volume(chat_id, current + VOLUME_STEP)
    await message.reply_text(
        f"**Volume** `{vol}%` (up)\n`{_bar(vol)}`",
        reply_markup=close_markup(_),
    )


@app.on_message(
    filters.command(["voldown", "volumedown", "vdown"])
    & filters.group
    & ~filters.user(list(BANNED_USERS))
)
@AdminRightsCheck
async def volume_down(cli, message: Message, _, chat_id):
    if not await is_active_chat(chat_id):
        return await message.reply_text(_["general_5"])
    current = await get_volume(chat_id)
    vol = await StreamController.change_volume(chat_id, current - VOLUME_STEP)
    await message.reply_text(
        f"**Volume** `{vol}%` (down)\n`{_bar(vol)}`",
        reply_markup=close_markup(_),
    )
