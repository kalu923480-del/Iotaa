# Authored By Iota Coders © 2025
"""Play free public radio streams in VC."""
from __future__ import annotations

from pyrogram import filters
from pyrogram.types import Message

from config import BANNED_USERS
from IotaXMedia import app
from IotaXMedia.utils.database import get_playmode, get_playtype, is_active_chat
from IotaXMedia.utils.errors import capture_err
from IotaXMedia.utils.stream.stream import stream
from IotaXMedia.misc import SUDOERS
from strings import get_string
from IotaXMedia.utils.database import get_lang

RADIO_STATIONS = {
    "1": {"name": "Lofi Radio", "url": "http://stream.zeno.fm/0r0xa792kwzuv"},
    "2": {"name": "Chillhop", "url": "http://stream.zeno.fm/f3wvbbqmdg8uv"},
    "3": {"name": "Jazz Lounge", "url": "http://stream.zeno.fm/kbuzrp9ys5quv"},
    "4": {"name": "EDM Hits", "url": "http://stream.zeno.fm/fg48r8z4v0hvv"},
    "5": {"name": "Bollywood", "url": "http://stream.zeno.fm/60ef4p33vxquv"},
    "6": {"name": "BBC World Service", "url": "http://stream.live.vc.bbcmedia.co.uk/bbc_world_service"},
}


def _list_text() -> str:
    lines = ["Radio stations:\n"]
    for k, v in RADIO_STATIONS.items():
        lines.append(f"{k}. {v['name']}")
    lines.append("\nUsage: /radio <number>")
    lines.append("Example: /radio 1")
    lines.append("Custom: /radio <direct_stream_url>")
    return "\n".join(lines)


@app.on_message(
    filters.command(["radio", "radiostation"])
    & filters.group
    & ~filters.user(list(BANNED_USERS))
)
@capture_err
async def radio_cmd(client, message: Message):
    language = await get_lang(message.chat.id)
    _ = get_string(language)

    if len(message.command) < 2:
        return await message.reply_text(_list_text())

    key = message.command[1].strip()
    station = RADIO_STATIONS.get(key)
    if not station:
        if key.startswith("http://") or key.startswith("https://"):
            station = {"name": "Custom Radio", "url": key}
        else:
            return await message.reply_text("Invalid station.\n\n" + _list_text())

    chat_id = message.chat.id
    mystic = await message.reply_text(f"Starting radio: {station['name']}...")
    try:
        await stream(
            _,
            mystic,
            message.from_user.id,
            station["url"],
            chat_id,
            station["name"],
            message.chat.id,
            video=False,
            streamtype="index",
            forceplay=False,
        )
    except Exception as e:
        return await mystic.edit_text(f"Failed to start radio:\n{e}")
