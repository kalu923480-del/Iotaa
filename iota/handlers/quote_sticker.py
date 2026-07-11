"""
Iota Bot — /q (Quote Sticker / Image Command)

Reply to a message with /q (or .q) to turn it into a styled quote card.

Usage:
  /q                 → quote the replied message (WEBP sticker)
  /q 2  /q 3         → quote a short thread (last N messages)
  /q png  /q img     → send as a PNG image instead of a sticker
  /q dark | light | white | purple | blue | telegram   → theme
  /q color #ff3366   → custom background colour
  /q border | noborder   → toggle the card outline
  /q png  /q img     → send as a PNG image instead of a sticker

Flags can be combined, e.g.  /q 3 purple png noborder
"""
import logging
import time

from telegram import Update
from telegram.ext import ContextTypes

from utils.helpers import get_profile_photo_id
from utils.quote_render import render_quote_card, QuoteRenderError
from utils.safe_html import safe_html

logger = logging.getLogger(__name__)

# In-memory avatar cache (uid -> (expiry_ts, bytes)) so repeated /q in busy
# groups don't re-download the same profile photo every time.
_avatar_cache: dict = {}


def _reply_thumb_file_id(msg):
    """Best thumbnail file_id for a replied message that contains media,
    or None if there is no usable thumbnail."""
    if msg.photo:
        return msg.photo[-1].file_id
    if msg.video and msg.video.thumbnail:
        return msg.video.thumbnail.file_id
    if msg.animation and msg.animation.thumbnail:
        return msg.animation.thumbnail.file_id
    if msg.sticker and msg.sticker.thumbnail:
        return msg.sticker.thumbnail.file_id
    if msg.audio and msg.audio.thumbnail:
        return msg.audio.thumbnail.file_id
    if msg.document and msg.document.thumbnail:
        return msg.document.thumbnail.file_id
    return None


MAX_QUOTE_LENGTH = 400      # per-message cap before we refuse
MAX_THREAD = 10             # hard cap on /q N
MODES = {"png", "img"}
THEME_NAMES = {"dark", "light", "white", "purple", "blue", "telegram"}
BORDER_ON = {"border"}
BORDER_OFF = {"noborder"}


async def _gather_messages(update, context, reply, count):
    """Build the list of {name,text,uid} dicts to render.

    Starts from the replied message; if count > 1, walks backwards through
    chat history to collect a short thread. Always returns at least [reply].
    """
    primary = reply
    msgs = []

    async def _as_dict(m):
        u = m.from_user
        text = m.text or m.caption
        if not text or not text.strip():
            return None
        name = (u.full_name or u.first_name or "Someone") if u else "Someone"
        return {"name": name, "text": text.strip(),
                "uid": u.id if u else 0}

    head = await _as_dict(primary)
    if head:
        msgs.append(head)

    if count > 1:
        try:
            older = []
            history = await context.bot.get_chat_history(
                update.effective_chat.id,
                offset_id=primary.message_id, offset=0, limit=count - 1,
            )
            for m in history:
                d = await _as_dict(m)
                if d:
                    older.append(d)
                if len(msgs) + len(older) >= count:
                    break
            # Keep the replied (primary) message first so the card header
            # (avatar + name) always belongs to it; older messages follow.
            msgs = msgs + list(reversed(older))
        except Exception as e:
            logger.debug(f"/q thread history fetch failed: {e}")

    return msgs


async def quote_sticker_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    reply = msg.reply_to_message

    if not reply:
        await msg.reply_html(
            "❌ Reply to a message with /q to turn it into a quote!"
        ); return

    # ── Parse arguments ────────────────────────────────────────────────
    mode = "sticker"
    theme = "dark"
    count = 1
    border = True
    args = list(context.args or [])
    i = 0
    while i < len(args):
        a = args[i].lower()
        if a in MODES:
            mode = "png"
        elif a in THEME_NAMES:
            theme = a
        elif a in BORDER_OFF:
            border = False
        elif a in BORDER_ON:
            border = True
        elif a == "color":
            if i + 1 < len(args):
                theme = "color " + args[i + 1]
            i += 1; continue
        elif a.isdigit():
            n = int(a)
            if 1 <= n <= MAX_THREAD:
                count = n
        i += 1

    # ── Validate message type ──────────────────────────────────────────
    if not (reply.text or reply.caption) or not (reply.text or reply.caption).strip():
        if reply.sticker:
            await msg.reply_html("❌ Can't quote a sticker — reply to a text message instead."); return
        if reply.photo or reply.video or reply.animation:
            await msg.reply_html("❌ Reply to a text message (a caption works too) to quote it."); return
        if reply.voice or reply.audio:
            await msg.reply_html("❌ Can't quote a voice/audio message — reply to text."); return
        await msg.reply_html("❌ This message type isn't supported."); return

    if len(reply.text or reply.caption or "") > MAX_QUOTE_LENGTH:
        await msg.reply_html(
            f"❌ That message is too long to fit on a quote "
            f"({len(reply.text or reply.caption)}/{MAX_QUOTE_LENGTH} max)."
        ); return

    messages = await _gather_messages(update, context, reply, count)
    if not messages:
        await msg.reply_html("❌ Nothing quotable found in that message."); return

    # ── Reply preview (nested quote) ───────────────────────────────────
    reply_preview = None
    if reply.reply_to_message:
        rp = reply.reply_to_message
        rptext = (rp.text or rp.caption or "").strip()
        if rptext:
            ru = rp.from_user
            rpname = (ru.full_name or ru.first_name or "Someone") if ru else "Someone"
            rp_media = bool(rp.photo or rp.video or rp.animation
                            or rp.sticker or rp.audio or rp.voice or rp.document)
            # Fetch the actual replied-media thumbnail so the reply preview
            # shows a real image, not just a placeholder box.
            media_bytes = None
            if rp_media:
                tfid = _reply_thumb_file_id(rp)
                if tfid:
                    try:
                        tgf = await context.bot.get_file(tfid)
                        media_bytes = bytes(await tgf.download_as_bytearray())
                    except Exception as e:
                        logger.debug(f"/q reply media fetch failed: {e}")
            reply_preview = {"name": rpname, "text": rptext[:120],
                             "media": rp_media, "media_bytes": media_bytes}

    timestamp = None
    if reply.date:
        try:
            timestamp = reply.date.strftime("%H:%M")
        except Exception:
            timestamp = None

    # ── Avatar ─────────────────────────────────────────────────────────
    sender = reply.from_user
    avatar_bytes = None
    if sender:
        try:
            fid = await get_profile_photo_id(context, sender.id)
            if fid:
                now = time.time()
                cached = _avatar_cache.get(sender.id)
                if cached and cached[0] > now and cached[1] is not None:
                    avatar_bytes = cached[1]
                else:
                    tgf = await context.bot.get_file(fid)
                    avatar_bytes = bytes(await tgf.download_as_bytearray())
                    _avatar_cache[sender.id] = (now + 3600, avatar_bytes)
        except Exception as e:
            logger.debug(f"/q avatar fetch failed for {sender.id}: {e}")
            avatar_bytes = None

    # ── Render + send ──────────────────────────────────────────────────
    try:
        img_bytes = render_quote_card(
            messages, avatar_bytes, theme=theme, mode=mode,
            reply_preview=reply_preview, timestamp=timestamp, border=border,
        )
    except QuoteRenderError as e:
        await msg.reply_html(safe_html(str(e))); return
    except Exception as e:
        logger.exception(f"/q render failed: {e}")
        await msg.reply_html("❌ Couldn't generate that quote — please try again."); return

    import io
    file_obj = io.BytesIO(img_bytes)
    file_obj.name = "quote.webp" if mode == "sticker" else "quote.png"
    try:
        if mode == "sticker":
            await msg.reply_sticker(file_obj)
        else:
            await msg.reply_photo(file_obj, caption="💬 Quote")
    except Exception as e:
        logger.warning(f"/q send failed: {e}")
        await msg.reply_html("❌ Couldn't send that quote — please try again.")
