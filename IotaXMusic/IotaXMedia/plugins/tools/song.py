# Authored By Iota Coders © 2025
"""YouTube song/video download for private chats."""
from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Dict, List, Optional

from pyrogram import filters
from pyrogram.enums import ChatAction
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import BANNED_USERS, SONG_DOWNLOAD_DURATION, SONG_DOWNLOAD_DURATION_LIMIT
from IotaXMedia import YouTube, app
from IotaXMedia.utils.decorators.language import language, languageCB
from IotaXMedia.utils.downloader import get_cookie_file, get_ytdlp_base_opts, yt_dlp_download
from IotaXMedia.utils.errors import capture_callback_err, capture_err
from IotaXMedia.utils.formatters import convert_bytes

SONG_COMMAND = ["song"]

_DEFAULTS = {
    "song_1": "You can download music/video from YouTube only in private chat. Start me in DM.",
    "song_2": "Usage:\n\n/song [music name] or [YouTube link]",
    "song_3": "Live link detected. Live YouTube videos can't be downloaded.",
    "song_4": "Title: {0}\n\nSelect download type:",
    "song_5": "Not a valid YouTube link.",
    "song_6": "Getting formats... please wait.",
    "song_7": "Failed to get available formats.",
    "song_8": "Downloading... please wait.",
    "song_9": "Download failed.\n\nReason: {0}",
    "song_10": "Failed to upload to Telegram.",
    "song_11": "Uploading...",
    "SG_B_1": "Open private chat",
    "SG_B_2": "Audio",
    "SG_B_3": "Video",
    "BACK_BUTTON": "Back",
    "CLOSE_BUTTON": "Close",
    "play_1": "Searching...",
    "play_3": "Failed to fetch track details. Try another query.",
    "play_4": "Duration limit exceeded ({0} min). Track is {1}.",
}


def _t(lang: Any, key: str, *args) -> str:
    try:
        if isinstance(lang, dict) and key in lang and lang[key]:
            text = lang[key]
        else:
            text = _DEFAULTS.get(key, key)
    except Exception:
        text = _DEFAULTS.get(key, key)
    if args:
        try:
            return text.format(*args)
        except Exception:
            return text
    return text


def _song_type_markup(lang, vidid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text=_t(lang, "SG_B_2"),
                    callback_data=f"song_dl audio|{vidid}",
                ),
                InlineKeyboardButton(
                    text=_t(lang, "SG_B_3"),
                    callback_data=f"song_dl video|{vidid}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(lang, "CLOSE_BUTTON"),
                    callback_data="close",
                )
            ],
        ]
    )


def _download_with_format(link: str, fmt: str) -> Optional[str]:
    """Sync yt-dlp download with explicit format (used for song downloads)."""
    try:
        from yt_dlp import YoutubeDL
        from IotaXMedia.core.dir import DOWNLOAD_DIR

        opts = get_ytdlp_base_opts()
        opts["format"] = fmt
        opts["outtmpl"] = f"{DOWNLOAD_DIR}/%(id)s.%(ext)s"
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(link, download=True) or {}
            vid = info.get("id")
            ext = info.get("ext")
            if vid and ext:
                path = f"{DOWNLOAD_DIR}/{vid}.{ext}"
                if os.path.exists(path) and os.path.getsize(path) > 1024:
                    return path
            # fallback scan
            if vid:
                for e in ("webm", "m4a", "mp3", "mp4", "mkv", "opus"):
                    path = f"{DOWNLOAD_DIR}/{vid}.{e}"
                    if os.path.exists(path) and os.path.getsize(path) > 1024:
                        return path
    except Exception as e:
        print(f"[song] download error: {e}")
    return None


@app.on_message(
    filters.command(SONG_COMMAND) & filters.group & ~filters.user(list(BANNED_USERS))
)
@capture_err
@language
async def song_command_group(client, message: Message, lang):
    uname = getattr(app, "username", None) or "Iotamusicbot"
    await message.reply_text(
        _t(lang, "song_1"),
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text=_t(lang, "SG_B_1"),
                        url=f"https://t.me/{uname}?start=song",
                    )
                ]
            ]
        ),
    )


@app.on_message(
    filters.command(SONG_COMMAND) & filters.private & ~filters.user(list(BANNED_USERS))
)
@capture_err
@language
async def song_command_private(client, message: Message, lang):
    mystic = await message.reply_text(_t(lang, "play_1"))

    url = None
    try:
        url = await YouTube.url(message)
    except Exception:
        url = None

    query = None
    if url:
        query = url
    elif message.text and len(message.command) > 1:
        query = message.text.split(None, 1)[1].strip()

    if not query:
        return await mystic.edit_text(_t(lang, "song_2"))

    try:
        title, dur_min, dur_sec, thumb, vidid = await YouTube.details(query)
    except Exception:
        return await mystic.edit_text(_t(lang, "play_3"))

    if not vidid:
        return await mystic.edit_text(_t(lang, "play_3"))

    # Live tracks often have no duration
    if not dur_min and not dur_sec:
        # still allow download attempt for non-live
        pass

    try:
        sec = int(dur_sec or 0)
    except Exception:
        sec = 0
    if sec and sec > int(SONG_DOWNLOAD_DURATION_LIMIT):
        return await mystic.edit_text(
            _t(lang, "play_4", SONG_DOWNLOAD_DURATION // 60, dur_min or sec)
        )

    thumb_url = thumb or f"https://i.ytimg.com/vi/{vidid}/hqdefault.jpg"
    caption = _t(lang, "song_4", title or vidid)
    markup = _song_type_markup(lang, vidid)

    try:
        await mystic.delete()
    except Exception:
        pass

    try:
        await message.reply_photo(
            photo=thumb_url,
            caption=caption,
            reply_markup=markup,
        )
    except Exception:
        await message.reply_text(caption, reply_markup=markup)


@app.on_callback_query(
    filters.regex(r"^song_dl ") & ~filters.user(list(BANNED_USERS))
)
@capture_callback_err
@languageCB
async def song_download_cb(client, cq, lang):
    try:
        await cq.answer("Starting download...")
    except Exception:
        pass

    try:
        payload = cq.data.split(" ", 1)[1]
        stype, vidid = payload.split("|", 1)
    except Exception:
        return await cq.answer("Invalid data", show_alert=True)

    yturl = f"https://www.youtube.com/watch?v={vidid}"
    try:
        await cq.edit_message_caption(caption=_t(lang, "song_8"))
    except Exception:
        try:
            await cq.edit_message_text(_t(lang, "song_8"))
        except Exception:
            pass

    loop = asyncio.get_running_loop()
    file_path = None
    title = vidid

    try:
        try:
            title = await YouTube.title(yturl) or vidid
        except Exception:
            title = vidid
        title = re.sub(r"\s+", " ", re.sub(r"[^\w\s\-\.\(\)\[\]]+", " ", title)).strip()[
            :180
        ] or vidid

        if stype == "audio":
            # Prefer dedicated format chain, then generic audio helper
            for fmt in (
                "bestaudio[ext=m4a]/bestaudio/best",
                "bestaudio/best",
                "140/251/250/249/bestaudio/best",
            ):
                file_path = await loop.run_in_executor(
                    None, _download_with_format, yturl, fmt
                )
                if file_path:
                    break
            if not file_path:
                file_path = await yt_dlp_download(yturl, type="audio", title=title)
        else:
            for fmt in (
                "best[height<=720][ext=mp4]/best[height<=720]/best",
                "bestvideo[height<=720]+bestaudio/best",
                "18/22/best",
            ):
                file_path = await loop.run_in_executor(
                    None, _download_with_format, yturl, fmt
                )
                if file_path:
                    break
            if not file_path:
                file_path = await yt_dlp_download(yturl, type="video", title=title)

        if not file_path or not os.path.exists(file_path):
            raise RuntimeError("download returned empty path")

        chat_id = cq.message.chat.id
        if stype == "audio":
            await app.send_chat_action(chat_id, ChatAction.UPLOAD_AUDIO)
            await app.send_audio(
                chat_id=chat_id,
                audio=file_path,
                title=title,
                caption=title,
            )
        else:
            await app.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)
            await app.send_video(
                chat_id=chat_id,
                video=file_path,
                caption=title,
                supports_streaming=True,
            )

        try:
            await cq.message.delete()
        except Exception:
            pass

    except Exception as e:
        err = str(e)[:200]
        try:
            await cq.edit_message_caption(caption=_t(lang, "song_9", err))
        except Exception:
            try:
                await cq.edit_message_text(_t(lang, "song_9", err))
            except Exception:
                await app.send_message(cq.message.chat.id, _t(lang, "song_9", err))
    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
