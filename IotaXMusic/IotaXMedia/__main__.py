# Authored By Iota Coders © 2025
import asyncio
import importlib

from pyrogram import idle
from pytgcalls.exceptions import NoActiveGroupCall

import config
from IotaXMedia import LOGGER, app, userbot
from IotaXMedia.core.call import StreamController
from IotaXMedia.misc import sudo
from IotaXMedia.plugins import ALL_MODULES
from IotaXMedia.utils.database import get_banned_users, get_gbanned
from IotaXMedia.utils.cookie_handler import fetch_and_store_cookies
from config import BANNED_USERS


async def init():
    if (
        not config.STRING1
        and not config.STRING2
        and not config.STRING3
        and not config.STRING4
        and not config.STRING5
    ):
        LOGGER(__name__).warning(
            "⚠️ ɴᴏ ᴀssɪsᴛᴀɴᴛ sᴇssɪᴏɴ sᴇᴛ – VC ᴘʟᴀʏʙᴀᴄᴋ ɪs ᴅɪsᴀʙʟᴇᴅ. "
            "ᴀᴅᴅ STRING_SESSION ᴛᴏ .env ᴛᴏ ᴇɴᴀʙʟᴇ ɪᴛ. Bᴏᴛ ɪs sᴛɪʟʟ ʀᴜɴɴɪɴɢ."
        )

    # ✅ Try to fetch cookies at startup
    try:
        await fetch_and_store_cookies()
        LOGGER("IotaXMedia").info("ʏᴏᴜᴛᴜʙᴇ ᴄᴏᴏᴋɪᴇs ʟᴏᴀᴅᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ ✅")
    except Exception as e:
        LOGGER("IotaXMedia").warning(f"⚠️ᴄᴏᴏᴋɪᴇ ᴇʀʀᴏʀ: {e}")


    await sudo()

    try:
        users = await get_gbanned()
        for user_id in users:
            BANNED_USERS.add(user_id)
        users = await get_banned_users()
        for user_id in users:
            BANNED_USERS.add(user_id)
    except:
        pass

    await app.start()
    for all_module in ALL_MODULES:
        importlib.import_module("IotaXMedia.plugins" + all_module)

    LOGGER("IotaXMedia.plugins").info("ɪᴏᴛᴀ's ᴍᴏᴅᴜʟᴇs ʟᴏᴀᴅᴇᴅ...")

    await userbot.start()
    await StreamController.start()

    try:
        await StreamController.stream_call("http://docs.evostream.com/sample_content/assets/sintel1m720p.mp4")
    except NoActiveGroupCall:
        LOGGER("IotaXMedia").error(
            "ᴘʟᴇᴀsᴇ ᴛᴜʀɴ ᴏɴ ᴛʜᴇ ᴠᴏɪᴄᴇ ᴄʜᴀᴛ ᴏғ ʏᴏᴜʀ ʟᴏɢ ɢʀᴏᴜᴘ/ᴄʜᴀɴɴᴇʟ.\n\nɪᴏᴛᴀ ʙᴏᴛ sᴛᴏᴘᴘᴇᴅ..."
        )
        exit()
    except:
        pass

    await StreamController.decorators()
    LOGGER("IotaXMedia").info(
        "\x49\x6f\x74\x61\x20\x4d\x75\x73\x69\x63\x20\x52\x6f\x62\x6f\x74\x20\x53\x74\x61\x72\x74\x65\x64\x20\x53\x75\x63\x63\x65\x73\x73\x66\x75\x6c\x6c\x79\x2e\x2e\x2e"
    )
    await idle()
    await app.stop()
    await userbot.stop()
    LOGGER("IotaXMedia").info("sᴛᴏᴘᴘɪɴɢ ɪᴏᴛᴀ ᴍᴜsɪᴄ ʙᴏᴛ ...")


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(init())
