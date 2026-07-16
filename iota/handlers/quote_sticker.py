"""
Iota Bot — /q (Quote Sticker) — ported same-to-same from IotaXMusic

This is the exact /q system used by the IotaXMusic music bot
(IotaXMedia/plugins/tools/quote.py), adapted from Pyrogram/MTProto to
python-telegram-bot (Bot API). Reply to a message with /q to turn it into a
Telegram-style quote sticker.

How it works (identical to IotaXMusic)
--------------------------------------
1. Build a quotly ``quote`` payload from the replied message (text, sender,
   entities, optional reply-context bubble) — the exact same shape
   IotaXMusic sends.
2. POST it to the external quotly API (LyoSU-compatible). It returns a PNG
   image (Telegram stickers are PNG-based; the ``.webp`` name is just the
   conventional extension).
3. Send it back as a sticker. We try a real ``sticker`` first (the IotaXMusic
   look); if Bot API rejects the raw upload, we transparently fall back to a
   ``photo`` so the user always gets the quote.

Behaviour mirrors IotaXMusic:
  /q        → quote the replied message
  /q r      → include the reply-context bubble
  /q 2..10  → quote a short thread (last N messages, text only)
"""
import base64
import logging
import re
from io import BytesIO

import aiohttp
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# Quote-API endpoints tried in order — SAME list the IotaXMusic music bot uses
# so /q keeps working across networks.
_QUOTE_ENDPOINTS = (
    "https://shnwazdev-quoteapi.vercel.app/generate.png",
    "https://bot.lyo.su/quote/generate.png",
    "https://qc-api.rizzy.eu.org/generate",
)

_HEADERS = {
    "Accept-Language": "id-ID",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/107.0.0.0 Safari/537.36 Edge/107.0.1418.42"
    ),
}
_TIMEOUT = aiohttp.ClientTimeout(total=25)


class QuotlyException(Exception):
    """Raised when no quote endpoint could produce an image."""


# ── Telegram-message → quotly field extractors ──────────────────────────────

def _sender_id(m) -> int:
    if getattr(m, "forward_origin", None) or getattr(m, "forward_date", None):
        u = getattr(m, "from_user", None)
        if u:
            return u.id
        sc = getattr(m, "sender_chat", None)
        if sc:
            return sc.id
        return 1
    if getattr(m, "from_user", None):
        return m.from_user.id
    if getattr(m, "sender_chat", None):
        return m.sender_chat.id
    return 1


def _sender_name(m) -> str:
    if getattr(m, "forward_origin", None) or getattr(m, "forward_date", None):
        u = getattr(m, "from_user", None)
        if u:
            if getattr(u, "last_name", None):
                return f"{u.first_name} {u.last_name}"
            return u.first_name or "Someone"
        sc = getattr(m, "sender_chat", None)
        if sc:
            return sc.title or "Channel"
        return "Someone"
    u = getattr(m, "from_user", None)
    if u:
        if getattr(u, "last_name", None):
            return f"{u.first_name} {u.last_name}"
        return u.first_name or "Someone"
    sc = getattr(m, "sender_chat", None)
    if sc:
        return sc.title or "Channel"
    return "Someone"


def _sender_username(m) -> str:
    if getattr(m, "forward_origin", None) or getattr(m, "forward_date", None):
        u = getattr(m, "from_user", None)
        if u and getattr(u, "username", None):
            return u.username
        return ""
    u = getattr(m, "from_user", None)
    if u and getattr(u, "username", None):
        return u.username
    sc = getattr(m, "sender_chat", None)
    if sc and getattr(sc, "username", None):
        return sc.username
    return ""


def _chat_type(m) -> str:
    chat = getattr(m, "chat", None)
    t = getattr(chat, "type", None)
    if t is None:
        return "private"
    return str(t).lower()


def _text_or_caption(m) -> str:
    return (getattr(m, "text", None) or getattr(m, "caption", None) or "")


def _entities_to_payload(m) -> list:
    """Convert PTB message entities to the quotly entity format so bold /
    italic / links / code etc. render exactly like in Telegram."""
    ents = getattr(m, "entities", None) or getattr(m, "caption_entities", None) or []
    out = []
    for e in ents:
        try:
            etype = e.type.value if hasattr(e.type, "value") else str(e.type)
        except Exception:
            etype = str(getattr(e, "type", ""))
        out.append({
            "type": etype,
            "offset": e.offset,
            "length": e.length,
        })
    return out


async def _build_payload(messages, include_reply: bool) -> dict:
    payload = {
        "type": "quote",
        "format": "png",
        "backgroundColor": "#1b1429",
        "messages": [],
    }
    for m in messages:
        entry = {
            "entities": _entities_to_payload(m),
            "chatId": _sender_id(m),
            "text": _text_or_caption(m),
            "avatar": True,
            "from": {
                "id": _sender_id(m),
                "name": _sender_name(m),
                "username": _sender_username(m),
                "type": _chat_type(m),
            },
        }
        reply = getattr(m, "reply_to_message", None)
        if include_reply and reply is not None:
            entry["replyMessage"] = {
                "name": _sender_name(reply),
                "text": _text_or_caption(reply),
                "chatId": _sender_id(reply),
            }
        else:
            entry["replyMessage"] = {}
        payload["messages"].append(entry)
    return payload


async def _render_quote(messages, include_reply: bool) -> bytes:
    """POST the payload to each endpoint until one returns a real image.
    Returns raw image bytes. Raises QuotlyException if all endpoints fail."""
    payload = await _build_payload(messages, include_reply)
    last_err = "all quote endpoints failed"
    async with aiohttp.ClientSession(headers=_HEADERS, timeout=_TIMEOUT) as s:
        for url in _QUOTE_ENDPOINTS:
            try:
                async with s.post(url, json=payload) as r:
                    body = await r.read()
                    if r.status != 200:
                        last_err = f"HTTP {r.status} from {url}"
                        continue
                    ctype = r.headers.get("content-type", "")
                    if "application/json" in ctype or body[:1] == b"{":
                        try:
                            data = await r.json(content_type=None)
                        except Exception:
                            last_err = f"invalid JSON from {url}"
                            continue
                        img_b64 = (
                            (data.get("result") or {}).get("image")
                            or data.get("image")
                        )
                        if not img_b64:
                            last_err = f"no image field from {url}"
                            continue
                        try:
                            return base64.b64decode(img_b64)
                        except Exception:
                            last_err = f"bad base64 from {url}"
                            continue
                    if body:
                        return body
                    last_err = f"empty body from {url}"
            except Exception as e:
                last_err = f"{type(e).__name__}: {e} ({url})"
    raise QuotlyException(last_err)


def _looks_like_image(b: bytes) -> bool:
    """True if `b` starts with WEBP / PNG / JPEG magic bytes. The endpoints
    occasionally answer HTTP 200 with an HTML/JSON error page; rejecting
    those avoids sending junk."""
    if not b or len(b) < 12:
        return False
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return True
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if b[:3] == b"\xff\xd8\xff":
        return True
    return False


def _fresh_bio(img: bytes, name: str) -> BytesIO:
    bio = BytesIO(img)
    bio.name = name
    return bio


def _is_webp(b: bytes) -> bool:
    return len(b) >= 12 and b[:4] == b"RIFF" and b[8:12] == b"WEBP"


def _to_webp(img: bytes) -> bytes:
    """Convert a raster (PNG/JPEG) to lossless WEBP so it can be sent as a
    real Telegram sticker (Bot API only accepts WEBP for stickers). Returns
    the original bytes if Pillow isn't available or conversion fails."""
    try:
        from PIL import Image
        im = Image.open(BytesIO(img)).convert("RGBA")
        out = BytesIO()
        im.save(out, format="WEBP", lossless=True)
        return out.getvalue()
    except Exception as e:
        logger.debug(f"/q png->webp conversion failed: {e}")
        return img


async def _send_quote(msg, img: bytes) -> None:
    """Deliver the quote.

    The quotly endpoints return a PNG (or WEBP). Bot API only accepts WEBP
    for stickers, so:
      • WEBP  → send as a sticker (the IotaXMusic look).
      • PNG/JPEG → convert to WEBP and send as a sticker; if that still
        fails, transparently fall back to a photo so the user always gets
        the quote. Photos are far more lenient to upload under Bot API.
    """
    reply_kwargs = {
        "reply_to_message_id": msg.message_id,
        "allow_sending_without_reply": True,
    }
    sticker_bytes = _to_webp(img) if not _is_webp(img) else img
    # 1) Sticker (IotaXMusic look).
    try:
        await msg.reply_sticker(_fresh_bio(sticker_bytes, "misskatyquote_sticker.webp"),
                                **reply_kwargs)
        return
    except Exception as e:
        logger.warning(f"/q reply_sticker rejected, falling back to photo: {e}")
    # 2) Photo (reliable delivery path under Bot API).
    try:
        await msg.reply_photo(_fresh_bio(img, "quote.png"), **reply_kwargs)
        return
    except Exception as e:
        logger.warning(f"/q reply_photo also failed: {e}")
        raise QuotlyException(f"send failed: {type(e).__name__}")


async def quote_sticker_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    reply = msg.reply_to_message
    if not reply:
        await msg.reply_text("❌ Reply to a message with /q to turn it into a quote!")
        return

    # Parse args: /q [r] [count] — same semantics as IotaXMusic.
    args = (context.args or [])
    include_reply = False
    count = 1
    for a in args:
        al = a.lower()
        if al == "r":
            include_reply = True
        else:
            try:
                n = int(a)
                if 1 <= n <= 10:
                    count = n
            except (ValueError, TypeError):
                continue

    if count < 1 or count > 10:
        await msg.reply_text("Invalid range", delete_after=6)
        return

    # Gather messages: Bot API can't fetch arbitrary id ranges like Pyrogram's
    # get_messages, so for count>1 we walk backwards through recent history
    # (text-only, same as IotaXMusic skips media messages).
    messages = [reply]
    if count > 1:
        try:
            older = []
            history = await context.bot.get_chat_history(
                update.effective_chat.id,
                offset_id=reply.message_id, offset=0, limit=count - 1,
            )
            for m in history:
                if getattr(m, "text", None) or getattr(m, "caption", None):
                    older.append(m)
                if len(messages) + len(older) >= count:
                    break
            messages = messages + list(reversed(older))
        except Exception:
            pass

    processing = await msg.reply_text("❄️")
    try:
        img = await _render_quote(messages, include_reply)
        if not _looks_like_image(img):
            raise QuotlyException("the quote service returned something that isn't an image")
        await _send_quote(msg, img)
    except QuotlyException as e:
        await _safe_reply(msg, f"❌ Couldn't generate the quote right now.\nReason: {e}")
    except Exception as e:
        logger.exception(f"/q unexpected error: {e}")
        await _safe_reply(
            msg, "❌ Couldn't generate the quote right now. Please try again later."
        )
    finally:
        try:
            await processing.delete()
        except Exception:
            pass


async def _safe_reply(msg, text: str):
    try:
        await msg.reply_text(text)
    except Exception as e:
        logger.debug(f"/q error-reply failed: {e}")
