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

    # Cookies: COOKIE_URL → fetch; else Render /etc/secrets or local cookies.txt
    try:
        from IotaXMedia.utils.cookie_handler import resolve_cookie_path

        if config.COOKIE_URL:
            await fetch_and_store_cookies()
            LOGGER("IotaXMedia").info("YouTube cookies loaded from COOKIE_URL")
        else:
            found = resolve_cookie_path()
            if found:
                LOGGER("IotaXMedia").info(f"YouTube cookies loaded from {found}")
            else:
                LOGGER("IotaXMedia").warning(
                    "No YouTube cookies — set COOKIE_URL, COOKIE_FILE, "
                    "or Render Secret File /etc/secrets/cookies.txt"
                )
    except Exception as e:
        LOGGER("IotaXMedia").warning(f"Cookie error: {e}")


    await sudo()

    try:
        users = await get_gbanned()
        for user_id in users:
            BANNED_USERS.add(user_id)
        users = await get_banned_users()
        for user_id in users:
            BANNED_USERS.add(user_id)
    except Exception:
        pass

    await app.start()
    for all_module in ALL_MODULES:
        importlib.import_module("IotaXMedia.plugins" + all_module)

    LOGGER("IotaXMedia.plugins").info("ɪᴏᴛᴀ's ᴍᴏᴅᴜʟᴇs ʟᴏᴀᴅᴇᴅ...")

    await userbot.start()
    await StreamController.start()

    try:
        await StreamController.stream_call(
            "http://docs.evostream.com/sample_content/assets/sintel1m720p.mp4"
        )
    except NoActiveGroupCall:
        LOGGER("IotaXMedia").warning(
            "Log group voice chat is off (or LOGGER_ID unset) — continuing without VC health-check."
        )
    except Exception as e:
        LOGGER("IotaXMedia").warning(f"Startup stream check skipped: {e}")

    # Sync live bot username into config for deep-links / admin checks
    try:
        if getattr(app, "username", None):
            config.BOT_USERNAME = app.username
    except Exception:
        pass

    await StreamController.decorators()

    # Render free-tier 24/7: HTTP /health + self-ping (same pattern as iota/)
    _stop_health = None
    try:
        from IotaXMedia.utils.keep_alive import (
            start_health_server,
            stop_health_server,
            render_keepalive_job,
        )

        await start_health_server()
        asyncio.create_task(render_keepalive_job(interval=300))
        _stop_health = stop_health_server
    except Exception as e:
        LOGGER("IotaXMedia").warning(f"⚠️ Health/keep-alive not started: {e}")

    LOGGER("IotaXMedia").info(
        "\x49\x6f\x74\x61\x20\x4d\x75\x73\x69\x63\x20\x52\x6f\x62\x6f\x74\x20\x53\x74\x61\x72\x74\x65\x64\x20\x53\x75\x63\x63\x65\x73\x73\x66\x75\x6c\x6c\x79\x2e\x2e\x2e"
    )
    try:
        await idle()
    finally:
        try:
            if _stop_health:
                await _stop_health()
        except Exception:
            pass
        try:
            await app.stop()
        except Exception:
            pass
        try:
            await userbot.stop()
        except Exception:
            pass
        LOGGER("IotaXMedia").info("sᴛᴏᴘᴘɪɴɢ ɪᴏᴛᴀ ᴍᴜsɪᴄ ʙᴏᴛ ...")


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(init())
