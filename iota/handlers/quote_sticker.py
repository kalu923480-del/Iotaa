"""
Iota Bot — /q (Quote Sticker / Image Command) — upgraded + quote-bot port

Reply to a message with /q to turn it into a styled quote card. Ports the
best of the standalone quote-bot into iota's Python stack (no external
quote-api / TDLib needed — rendering is local via utils.quote_render.py).

Commands
  /q                 → quote the replied message (WEBP sticker)
  /q 2 /q 3 …        → quote a short thread (last N messages)
  /q png /q img      → send as a PNG image instead of a sticker
  /q dark|light|white|purple|blue|telegram   → theme
  /q color #ff3366   → custom background colour
  /q border|noborder → toggle the card outline
  /q s1.5            → hi-res scale (PNG only)
  /q c               → crop transparent margin (PNG only)
  /q r               → include reply context (default on)
  /q anon            → anonymise the sender (privacy)
  /q rate            → attach 👍/👎 rating buttons
  /qrand             → random quote from this chat's history
  /qtop              → top-rated quotes in this chat
  /qrate             → toggle rating by default for your quotes
  /qcolor <theme>    → set your default background theme
  /qemoji <emoji>    → set your emoji brand (corner mark)
  /privacy           → toggle anonymise-by-default
  /qarchive          → save the replied message to your quote collection
  /qforget           → delete your most recent saved quote

Everything degrades gracefully: if Mongo is down, ratings/archive simply
no-op and the quote still renders + sends.
"""
import io
import logging
import random
import time

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from utils.helpers import get_profile_photo_id
from utils.quote_render import render_quote_card, QuoteRenderError
from utils.safe_html import safe_html
from utils.quote_store import (
    get_settings, set_setting, save_quote, rate_quote,
    top_quotes, forget_quote,
)

logger = logging.getLogger(__name__)

_avatar_cache: dict = {}

MAX_QUOTE_LENGTH = 400
MAX_THREAD = 10
MODES = {"png", "img"}
THEME_NAMES = {"dark", "light", "white", "purple", "blue", "telegram"}
BORDER_ON = {"border"}
BORDER_OFF = {"noborder"}
RATE_ON = {"rate"}
ANON_ON = {"anon", "privacy"}


def _reply_thumb_file_id(msg):
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


def _msg_to_dict(m):
    u = m.from_user
    text = m.text or m.caption
    if not text or not text.strip():
        return None
    name = (u.full_name or u.first_name or "Someone") if u else "Someone"
    return {"name": name, "text": text.strip(), "uid": u.id if u else 0}


def _rating_kb(qid):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("👍", callback_data=f"qr_{qid}_up"),
        InlineKeyboardButton("👎", callback_data=f"qr_{qid}_down"),
    ]])


async def _gather_messages(update, context, reply, count):
    primary = reply
    msgs = []
    head = _msg_to_dict(primary)
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
                d = _msg_to_dict(m)
                if d:
                    older.append(d)
                if len(msgs) + len(older) >= count:
                    break
            msgs = msgs + list(reversed(older))
        except Exception as e:
            logger.debug(f"/q thread history fetch failed: {e}")
    return msgs


async def _fetch_avatar(context, sender):
    if not sender:
        return None
    try:
        fid = await get_profile_photo_id(context, sender.id)
        if fid:
            now = time.time()
            cached = _avatar_cache.get(sender.id)
            if cached and cached[0] > now and cached[1] is not None:
                return cached[1]
            tgf = await context.bot.get_file(fid)
            avatar_bytes = bytes(await tgf.download_as_bytearray())
            _avatar_cache[sender.id] = (now + 3600, avatar_bytes)
            return avatar_bytes
    except Exception as e:
        logger.debug(f"/q avatar fetch failed for {getattr(sender, 'id', '?')}: {e}")
    return None


async def _make_quote(update, context, messages, primary_msg, opts):
    """Render + send a quote with the given options. Returns nothing."""
    msg = update.effective_message
    mode = opts.get("mode", "sticker")
    theme = opts.get("theme", "dark")
    border = opts.get("border", True)
    scale = opts.get("scale", 1.0)
    crop = opts.get("crop", False)
    privacy = opts.get("privacy", False)
    emoji = opts.get("emoji")
    rate = opts.get("rate", False)

    sender = primary_msg.from_user if primary_msg else None
    avatar_bytes = await _fetch_avatar(context, sender)
    timestamp = None
    if primary_msg and primary_msg.date:
        try:
            timestamp = primary_msg.date.strftime("%H:%M")
        except Exception:
            timestamp = None

    reply_preview = None
    if primary_msg and primary_msg.reply_to_message:
        rp = primary_msg.reply_to_message
        rptext = (rp.text or rp.caption or "").strip()
        if rptext:
            ru = rp.from_user
            rpname = (ru.full_name or ru.first_name or "Someone") if ru else "Someone"
            rp_media = bool(rp.photo or rp.video or rp.animation
                            or rp.sticker or rp.audio or rp.voice or rp.document)
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

    try:
        img_bytes = render_quote_card(
            messages, avatar_bytes, theme=theme, mode=mode,
            reply_preview=reply_preview, timestamp=timestamp, border=border,
            scale=scale, crop=crop, emoji_brand=emoji, privacy=privacy,
        )
    except QuoteRenderError as e:
        await msg.reply_html(safe_html(str(e)))
        return
    except Exception as e:
        logger.exception(f"/q render failed: {e}")
        await msg.reply_html("❌ Couldn't generate that quote — please try again.")
        return

    file_obj = io.BytesIO(img_bytes)
    file_obj.name = "quote.webp" if mode == "sticker" else "quote.png"
    try:
        if rate:
            # Rated quotes are always photos so we can attach + update buttons.
            qid = await save_quote(
                msg.from_user.id, msg.chat_id,
                "Anonymous" if privacy else messages[0]["name"],
                messages[0]["text"], theme,
            )
            await msg.reply_photo(file_obj, caption="💬 Quote",
                                  reply_markup=_rating_kb(qid))
        elif mode == "sticker":
            await msg.reply_sticker(file_obj)
        else:
            await msg.reply_photo(file_obj, caption="💬 Quote")
    except Exception as e:
        logger.warning(f"/q send failed: {e}")
        try:
            await msg.reply_html("❌ Couldn't send that quote — please try again.")
        except Exception:
            pass


async def quote_sticker_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    reply = msg.reply_to_message
    if not reply:
        await msg.reply_html(
            "❌ Reply to a message with /q to turn it into a quote!"
        )
        return

    opts = await get_settings(msg.from_user.id)
    mode = "sticker"
    theme = opts.get("theme", "dark")
    border = True
    scale = 1.0
    crop = False
    privacy = opts.get("privacy", False)
    emoji = opts.get("emoji")
    rate = opts.get("rate", False)
    count = 1

    for a in (context.args or []):
        al = a.lower()
        if al in MODES:
            mode = "png"
        elif al in THEME_NAMES:
            theme = al
        elif al in BORDER_OFF:
            border = False
        elif al in BORDER_ON:
            border = True
        elif al in RATE_ON:
            rate = True
        elif al in ANON_ON:
            privacy = True
        elif al == "color":
            # handled below when followed by a hex value
            continue
        elif al == "c":
            crop = True
        elif al.startswith("s") and al[1:]:
            try:
                scale = max(0.5, min(4.0, float(al[1:])))
            except ValueError:
                pass
        elif al == "r":
            pass
        elif a.lower().startswith("#") and len(a) in (4, 7):
            theme = "color " + a
        elif a.isdigit():
            n = int(a)
            if 1 <= n <= MAX_THREAD:
                count = n

    # Validate message type.
    body = reply.text or reply.caption or ""
    if not body.strip():
        if reply.sticker:
            await msg.reply_html("❌ Can't quote a sticker — reply to a text message instead."); return
        if reply.photo or reply.video or reply.animation:
            await msg.reply_html("❌ Reply to a text message (a caption works too) to quote it."); return
        if reply.voice or reply.audio:
            await msg.reply_html("❌ Can't quote a voice/audio message — reply to text."); return
        await msg.reply_html("❌ This message type isn't supported."); return
    if len(body) > MAX_QUOTE_LENGTH:
        await msg.reply_html(
            f"❌ That message is too long to fit on a quote "
            f"({len(body)}/{MAX_QUOTE_LENGTH} max)."
        )
        return

    messages = await _gather_messages(update, context, reply, count)
    if not messages:
        await msg.reply_html("❌ Nothing quotable found in that message."); return

    await _make_quote(update, context, messages, reply,
                      dict(mode=mode, theme=theme, border=border, scale=scale,
                           crop=crop, privacy=privacy, emoji=emoji, rate=rate))


async def qrand_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    if chat.type == "private":
        await msg.reply_html("🎲 /qrand sirf groups mein chalao!"); return
    try:
        history = await context.bot.get_chat_history(chat.id, limit=200)
    except Exception as e:
        logger.debug(f"/qrand history failed: {e}")
        await msg.reply_html("❌ Chat history nahi mili."); return
    candidates = [m for m in history
                  if (m.text or m.caption) and (m.text or m.caption).strip()
                  and m.from_user]
    if not candidates:
        await msg.reply_html("❌ Is chat mein koi quotable message nahi."); return
    pick = random.choice(candidates)
    d = _msg_to_dict(pick)
    await _make_quote(update, context, [d], pick, await get_settings(msg.from_user.id))


async def qtop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    rows = await top_quotes(chat.id, limit=10)
    if not rows:
        await msg.reply_html("🏆 Is chat mein abhi koi rated quote nahi.\n"
                              "Quote banao aur /q rate use karo!"); return
    lines = ["🏆 <b>Tᴏᴘ Qᴜᴏᴛᴇs</b>", "━" * 18]
    for i, r in enumerate(rows, 1):
        text = (r.get("text") or "").replace("\n", " ")
        if len(text) > 60:
            text = text[:57] + "…"
        lines.append(f"{i}. {r.get('name', '?')}: {safe_html(text)} "
                     f"👍{r.get('up', 0)} 👎{r.get('down', 0)}")
    await msg.reply_html("\n".join(lines))


async def qrate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    cur = await get_settings(msg.from_user.id)
    new = not cur.get("rate", False)
    await set_setting(msg.from_user.id, rate=new)
    await msg.reply_html(f"🔘 Rating {'ON' if new else 'OFF'} kar diya.\n"
                          f"Ab /q karne par 👍/👎 buttons aayenge.")


async def qcolor_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    arg = (context.args[0] if context.args else "").lower()
    if not arg:
        await msg.reply_html("Usage: <code>/qcolor dark|light|white|purple|blue|telegram</code> "
                             "ya <code>/qcolor #ff3366</code>"); return
    theme = arg if arg in THEME_NAMES else ("color " + arg if arg.startswith("#") else arg)
    await set_setting(msg.from_user.id, theme=theme)
    await msg.reply_html(f"🎨 Default background set to <b>{safe_html(arg)}</b>.")


async def qemoji_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    arg = context.args[0] if context.args else ""
    if not arg:
        await msg.reply_html("Usage: <code>/qemoji 💜</code> — set a corner brand emoji."); return
    await set_setting(msg.from_user.id, emoji=arg[:4])
    await msg.reply_html(f"✨ Emoji brand set to {arg[:4]}.")


async def privacy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    cur = await get_settings(msg.from_user.id)
    new = not cur.get("privacy", False)
    await set_setting(msg.from_user.id, privacy=new)
    await msg.reply_html(f"🔒 Privacy mode {'ON' if new else 'OFF'} — "
                          f"quotes will {'hide' if new else 'show'} the sender.")


async def qarchive_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    reply = msg.reply_to_message
    if not reply:
        await msg.reply_html("❌ Archive karne ke liye kisi message par reply karo + /qarchive"); return
    d = _msg_to_dict(reply)
    if not d:
        await msg.reply_html("❌ Is message mein text nahi."); return
    opts = await get_settings(msg.from_user.id)
    qid = await save_quote(msg.from_user.id, msg.chat_id, d["name"], d["text"],
                           opts.get("theme", "dark"))
    await msg.reply_html(f"📥 Quote archive kar liya! (id: <code>{qid}</code>)\n"
                          f"Apni top quotes /qtop se dekho.")


async def qforget_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    arg = context.args[0] if context.args else None
    if arg:
        ok = await forget_quote(msg.from_user.id, arg)
        await msg.reply_html("🗑️ Quote delete kiya." if ok else "❌ Wo quote nahi mila.")
        return
    # No id given → delete the user's most recent archived quote.
    try:
        from utils.mongo_db import get_db
        doc = await get_db()["quote_archive"].find_one(
            {"uid": msg.from_user.id}, sort=[("ts", -1)]
        )
    except Exception as e:
        logger.debug(f"/qforget lookup failed: {e}")
        doc = None
    if not doc:
        await msg.reply_html("❌ Tere paas koi archived quote nahi."); return
    ok = await forget_quote(msg.from_user.id, doc["_id"])
    await msg.reply_html("🗑️ Latest archived quote delete kiya." if ok
                          else "❌ Delete nahi ho saka.")


async def quote_rate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        _, qid, vote = q.data.split("_")
    except Exception:
        return
    up = vote == "up"
    res = await rate_quote(qid, up)
    try:
        await q.edit_message_reply_markup(reply_markup=_rating_kb(qid))
        await q.edit_message_caption(
            caption=f"💬 Quote — 👍 {res['up']}  👎 {res['down']}"
        )
    except Exception as e:
        logger.debug(f"quote rate callback edit failed: {e}")
