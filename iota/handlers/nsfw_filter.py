"""
Iota — Automatic NSFW media filter (stickers + photos).

No public commands. Runs automatically in every group:
  • High-confidence only (default threshold 90)
  • Admins never blocked
  • Cache by file_unique_id
  • Silent delete by default (no noisy warnings)

Scoring lives in utils/nsfw_filter.py (multi-signal, anti false-positive).
"""
from __future__ import annotations

import logging
import io
import asyncio
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from utils.mongo_db import (
    get_prot,
    nsfw_cache_get, nsfw_cache_set,
    log_mod_action,
)
from utils.nsfw_filter import (
    merge_nsfw_settings, combine_score, should_delete,
)
from utils.helpers import is_admin, ts

logger = logging.getLogger(__name__)

# Hard automatic policy (no admin commands required)
_AUTO_THRESHOLD = 90
_MAX_BYTES = 2 * 1024 * 1024
_SCAN_STICKERS = True
_SCAN_PHOTOS = True
_SILENT = True  # no public "NSFW removed" spam


async def _download_media(msg, max_bytes: int = _MAX_BYTES) -> Optional[bytes]:
    file_obj = None
    if msg.sticker:
        if getattr(msg.sticker, "is_video", False):
            return None
        file_obj = msg.sticker
    elif msg.photo:
        file_obj = msg.photo[-1]
    else:
        return None

    try:
        if file_obj.file_size and file_obj.file_size > max_bytes:
            return None
        f = await file_obj.get_file()
        buf = io.BytesIO()
        await f.download_to_memory(out=buf)
        return buf.getvalue()
    except Exception:
        return None


async def _delete_msg(msg):
    try:
        await msg.delete()
    except Exception:
        pass


async def nsfw_media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-scan stickers/photos in groups; delete only high-confidence NSFW."""
    msg = update.effective_message
    chat = update.effective_chat
    u = update.effective_user

    if not msg or not u or not chat:
        return
    if chat.type not in ("group", "supergroup"):
        return
    if u.is_bot:
        return
    try:
        if await is_admin(update, context, u.id):
            return
    except Exception:
        pass

    # Optional per-group hard-off only if explicitly stored False in DB.
    # Default / missing → always ON (fully automatic).
    try:
        prot = await get_prot(chat.id) or {}
    except Exception:
        prot = {}
    cfg = merge_nsfw_settings(prot)
    # Only skip if an old explicit disable was saved; new groups always scan.
    if prot.get("nsfw_enabled") is False:
        return

    is_sticker = bool(msg.sticker)
    is_photo = bool(msg.photo) and not is_sticker

    if is_sticker and not _SCAN_STICKERS:
        return
    if is_photo and not _SCAN_PHOTOS:
        return
    if is_sticker and getattr(msg.sticker, "is_video", False):
        return
    if not is_sticker and not is_photo:
        return

    unique_id = ""
    set_name = ""
    emoji = None
    if is_sticker:
        unique_id = getattr(msg.sticker, "file_unique_id", "") or ""
        set_name = getattr(msg.sticker, "set_name", "") or ""
        emoji = getattr(msg.sticker, "emoji", None)
    elif is_photo:
        unique_id = msg.photo[-1].file_unique_id if msg.photo else ""

    threshold = _AUTO_THRESHOLD
    try:
        threshold = max(90, int(cfg.get("nsfw_threshold") or 90))  # never below 90 auto
    except Exception:
        threshold = 90

    # Cache hit (known NSFW)
    if unique_id:
        try:
            cached = await nsfw_cache_get(unique_id)
            if cached and cached.get("is_nsfw") and int(cached.get("score") or 0) >= threshold:
                await _delete_msg(msg)
                try:
                    await log_mod_action(
                        chat.id, 0, "nsfw_auto",
                        target_id=u.id,
                        reason="cache",
                        meta={"score": cached.get("score"), "uid": unique_id},
                    )
                except Exception:
                    pass
                return
            # Known clean — skip re-download
            if cached and cached.get("is_nsfw") is False and int(cached.get("score") or 0) < threshold:
                return
        except Exception:
            pass

    image_bytes = await _download_media(msg)
    # Stickers can still be scored from set_name/emoji without pixels
    if image_bytes is None and not set_name and not emoji:
        return

    try:
        result = combine_score(
            set_name=set_name,
            emoji=emoji,
            image_bytes=image_bytes,
            unique_id=unique_id,
            settings=prot,
        )
    except Exception as e:
        logger.debug("nsfw combine_score failed: %s", e)
        return

    score = int(result.get("score", 0))
    reasons = result.get("reasons") or []
    is_nsfw = should_delete(result, threshold)

    if unique_id:
        try:
            await nsfw_cache_set(unique_id, score, reasons, is_nsfw)
        except Exception:
            pass

    if not is_nsfw:
        return

    await _delete_msg(msg)
    try:
        await log_mod_action(
            chat.id, 0, "nsfw_auto",
            target_id=u.id,
            reason=",".join(str(r) for r in reasons)[:120],
            meta={"score": score, "set": set_name[:64] if set_name else ""},
        )
    except Exception:
        pass

    # Silent by default — no public shaming / false-positive noise
    if not _SILENT:
        try:
            notice = await context.bot.send_message(
                chat.id,
                "🔞 Media removed (auto filter).",
                disable_notification=True,
            )
            async def _gone():
                await asyncio.sleep(5)
                try:
                    await context.bot.delete_message(chat.id, notice.message_id)
                except Exception:
                    pass
            asyncio.create_task(_gone())
        except Exception:
            pass
