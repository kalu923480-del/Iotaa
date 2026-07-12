"""
Iota Bot — Utility & Info Commands (net-new, missing features)

Commands:
  /weather <city>        — current weather (wttr.in, no API key)
  /currency <amt> <f> <t>— FX converter (open.er-api.com, no key)
  /wiki <query>          — Wikipedia summary
  /define <word>         — dictionary definition (dictionaryapi.dev)
  /short <url>           — URL shortener (is.gd, no key)
  /time [zone]           — local time in a timezone (zoneinfo)
  /sticky <text>         — pin a sticky notice in the group
  /sticky off            — remove the sticky
  /feedback <text>       — send feedback to the owner
  /schedule <mins> <msg> — DM yourself a message later
  /rss add|list|remove   — subscribe to an RSS feed

All network calls are wrapped so a failure degrades to a clear message
rather than crashing the bot (matches the project's "never raise" contract).
"""
import logging
import time
import asyncio
import xml.etree.ElementTree as ET
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from utils.mongo_db import get_db, ensure_user
from utils.helpers import mention, fmt
from utils.fonts import sc
from config import OWNER_ID

logger = logging.getLogger(__name__)


async def _get_json(url: str, timeout: float = 8.0):
    import aiohttp
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                return await r.json()
    except Exception as e:
        logger.debug(f"_get_json failed for {url}: {e}")
        return None


async def _get_text(url: str, timeout: float = 8.0) -> str | None:
    import aiohttp
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                return await r.text()
    except Exception as e:
        logger.debug(f"_get_text failed for {url}: {e}")
        return None


# ── /weather ────────────────────────────────────────────────────────────
async def weather_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_html("🌤️ Usage: <code>/weather London</code>")
        return
    city = " ".join(context.args)
    text = await _get_text(
        "https://wttr.in/" + city.replace(" ", "%20") + "?format=4"
    )
    if not text:
        await update.message.reply_html("🌧️ Weather fetch nahi ho paya. Try again later.")
        return
    await update.message.reply_html(f"🌤️ <b>Weather — {sc(city)}</b>\n<pre>{text.strip()}</pre>")


# ── /currency ───────────────────────────────────────────────────────────
async def currency_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.message.reply_html("💱 Usage: <code>/currency 100 usd inr</code>")
        return
    try:
        amt = float(context.args[0])
    except ValueError:
        await update.message.reply_html("❌ Amount number hona chahiye!")
        return
    frm = context.args[1].upper()
    to = context.args[2].upper()
    data = await _get_json(f"https://open.er-api.com/v6/latest/{frm}")
    if not data or data.get("result") != "success":
        await update.message.reply_html("💱 Rate fetch fail. Try later.")
        return
    rate = data["rates"].get(to)
    if rate is None:
        await update.message.reply_html(f"💱 Unknown currency: {to}")
        return
    conv = amt * rate
    await update.message.reply_html(
        f"💱 <b>{fmt(int(amt))} {frm}</b> = <b>{fmt(int(conv))} {to}</b>\n"
        f"📊 1 {frm} = {rate:.4f} {to}"
    )


# ── /wiki ───────────────────────────────────────────────────────────────
async def wiki_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_html("📚 Usage: <code>/wiki Python</code>")
        return
    q = " ".join(context.args)
    data = await _get_json(
        "https://en.wikipedia.org/api/rest_v1/page/summary/"
        + q.replace(" ", "_")
    )
    if not data or "extract" not in data:
        await update.message.reply_html(f"📚 '{sc(q)}' ke liye kuch nahi mila.")
        return
    extract = data["extract"]
    if len(extract) > 800:
        extract = extract[:800] + "…"
    url = data.get("content_urls", {}).get("desktop", {}).get("page", "")
    await update.message.reply_html(
        f"📚 <b>{sc(data.get('title', q))}</b>\n\n{extract}\n\n🔗 {url}"
    )


# ── /define ─────────────────────────────────────────────────────────────
async def define_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_html("📖 Usage: <code>/define serendipity</code>")
        return
    word = context.args[0].lower()
    data = await _get_json(f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}")
    if not data or not isinstance(data, list):
        await update.message.reply_html(f"📖 '{sc(word)}' ka meaning nahi mila.")
        return
    lines = [f"📖 <b>{word}</b>"]
    for entry in data[:1]:
        for meaning in entry.get("meanings", [])[:2]:
            pos = meaning.get("partOfSpeech", "")
            defs = meaning.get("definitions", [])
            if defs:
                lines.append(f"({pos}) {defs[0].get('definition', '')}")
    await update.message.reply_html("\n".join(lines))


# ── /short ──────────────────────────────────────────────────────────────
async def short_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_html("🔗 Usage: <code>/short https://long.url</code>")
        return
    url = context.args[0]
    import aiohttp
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://is.gd/create.php",
                             params={"format": "simple", "url": url},
                             timeout=aiohttp.ClientTimeout(total=8)) as r:
                short = await r.text()
        if short and short.startswith("http"):
            await update.message.reply_html(f"🔗 Shortened:\n{short}")
        else:
            await update.message.reply_html(f"🔗 Original:\n{url}")
    except Exception:
        await update.message.reply_html(f"🔗 Original:\n{url}")


# ── /time ───────────────────────────────────────────────────────────────
async def time_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        from zoneinfo import ZoneInfo, available_timezones
    except Exception:
        await update.message.reply_html("🕐 Timezones unavailable on this server.")
        return
    if not context.args:
        import datetime
        now = datetime.datetime.now()
        await update.message.reply_html(
            f"🕐 Local server time: {now.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return
    zone = context.args[0]
    if "/" not in zone:
        zone = f"Asia/{zone.title()}"
    if zone not in available_timezones():
        await update.message.reply_html(
            f"🕐 Unknown timezone: {sc(zone)}. Try 'Asia/Kolkata' ya 'Europe/London'."
        )
        return
    import datetime
    z = ZoneInfo(zone)
    t = datetime.datetime.now(z)
    await update.message.reply_html(
        f"🕐 <b>{sc(zone)}</b>: {t.strftime('%Y-%m-%d %H:%M:%S')}"
    )


# ── /sticky ─────────────────────────────────────────────────────────────
async def sticky_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    u = update.effective_user
    if chat.type == "private":
        await update.message.reply_html("📌 Sticky sirf groups mein use karo!")
        return
    if not context.args or context.args[0].lower() == "off":
        await get_db().sticky.delete_one({"_id": chat.id})
        try:
            await context.bot.unpin_all_chat_messages(chat.id)
        except Exception:
            pass
        await update.message.reply_html("📌 Sticky hata diya!")
        return
    text = " ".join(context.args)
    await get_db().sticky.update_one(
        {"_id": chat.id}, {"$set": {"text": text}}, upsert=True
    )
    # Pin it immediately and remember its id so our own re-pin is ignored.
    try:
        m = await update.message.reply_html(f"📌 <b>Sticky:</b>\n{text}")
        await context.bot.pin_chat_message(chat.id, m.message_id,
                                           disable_notification=True)
        await get_db().sticky.update_one(
            {"_id": chat.id}, {"$set": {"message_id": m.message_id}}
        )
    except Exception:
        pass
    await update.message.reply_html(f"📌 Sticky set! Agar koi aur pin karega toh ye wapas pin hoga.\n\n{text}")


async def repin_sticky_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Re-pin the group's sticky whenever someone pins a *different* message.
    Ignores our own sticky pin (tracked by message_id) to avoid a loop."""
    chat = update.effective_chat
    if not chat or chat.type == "private":
        return
    doc = await get_db().sticky.find_one({"_id": chat.id})
    if not doc:
        return
    # If the just-pinned message IS our sticky, do nothing (prevents loop).
    pinned = update.message.pinned_message if update.message else None
    if pinned and pinned.message_id == doc.get("message_id"):
        return
    try:
        m = await context.bot.send_message(chat.id, doc["text"])
        await context.bot.pin_chat_message(chat.id, m.message_id,
                                           disable_notification=True)
        await get_db().sticky.update_one(
            {"_id": chat.id}, {"$set": {"message_id": m.message_id}}
        )
    except Exception:
        pass


# ── /feedback ───────────────────────────────────────────────────────────
async def feedback_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    if not context.args:
        await update.message.reply_html("💬 Usage: <code>/feedback your message</code>")
        return
    text = " ".join(context.args)
    await get_db().feedback.insert_one({
        "user_id": u.id, "text": text, "ts": int(time.time())
    })
    try:
        await context.bot.send_message(
            OWNER_ID,
            f"💬 <b>Feedback from {mention(u)}</b>\n\n{text}",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await update.message.reply_html("💬 Feedback bhej diya! Owner check karega. 💙")


# ── /schedule ───────────────────────────────────────────────────────────
async def schedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if len(context.args) < 2:
        await update.message.reply_html(
            "⏰ Usage: <code>/schedule 30 remind me to sleep</code> (minutes)"
        )
        return
    try:
        mins = int(context.args[0])
    except ValueError:
        await update.message.reply_html("❌ Minutes ek number hona chahiye!")
        return
    if mins < 1 or mins > 60 * 24 * 30:
        await update.message.reply_html("❌ 1 minute se 30 din ke beech hona chahiye!")
        return
    msg = " ".join(context.args[1:])
    when = int(time.time()) + mins * 60
    await get_db().schedules.insert_one({
        "user_id": u.id, "text": msg, "at": when
    })
    if getattr(context, "job_queue", None):
        context.job_queue.run_once(
            _send_scheduled, mins * 60, data={"user_id": u.id, "text": msg}
        )
    await update.message.reply_html(
        f"⏰ Scheduled! {mins}m baad DM mein yaad dilaoonga: {msg}"
    )


async def _send_scheduled(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    try:
        await context.bot.send_message(
            data["user_id"],
            f"⏰ <b>Reminder:</b>\n{data['text']}",
            parse_mode="HTML"
        )
    except Exception:
        pass


async def rehydrate_schedules(application):
    """Re-create pending schedules after a restart (jobs don't survive)."""
    try:
        now_ts = int(time.time())
        docs = await get_db().schedules.find({"at": {"$gt": now_ts}}).to_list(500)
        for d in docs:
            delay = max(1, d["at"] - now_ts)
            if getattr(application, "job_queue", None):
                application.job_queue.run_once(
                    _send_scheduled, delay,
                    data={"user_id": d["user_id"], "text": d["text"]}
                )
    except Exception as e:
        logger.debug(f"rehydrate_schedules failed: {e}")


# ── /rss ────────────────────────────────────────────────────────────────
async def rss_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    args = context.args
    if not args:
        await update.message.reply_html(
            "📰 <b>RSS</b>\n/rss add &lt;url&gt;\n/rss list\n/rss remove &lt;url&gt;"
        )
        return
    sub = args[0].lower()
    if sub == "add":
        if len(args) < 2:
            await update.message.reply_html("📰 Usage: /rss add &lt;feed_url&gt;")
            return
        url = args[1]
        await get_db().rss.update_one(
            {"chat_id": chat.id, "url": url},
            {"$set": {"chat_id": chat.id, "url": url, "last": ""}},
            upsert=True
        )
        await update.message.reply_html(f"📰 Subscribed! 📰 {url}")
    elif sub == "list":
        docs = await get_db().rss.find({"chat_id": chat.id}).to_list(50)
        if not docs:
            await update.message.reply_html("📰 Koi feeds nahi.")
            return
        await update.message.reply_html(
            "📰 <b>Your feeds:</b>\n" + "\n".join(d["url"] for d in docs)
        )
    elif sub == "remove":
        if len(args) < 2:
            await update.message.reply_html("📰 Usage: /rss remove &lt;feed_url&gt;")
            return
        res = await get_db().rss.delete_one({"chat_id": chat.id, "url": args[1]})
        await update.message.reply_html(
            "📰 Unsubscribed." if res.deleted_count else "📰 Feed nahi mila."
        )


async def rss_check_loop(application):
    """Periodically fetch subscribed feeds and post new items to their chat."""
    while True:
        try:
            await asyncio.sleep(300)
            docs = await get_db().rss.find({}).to_list(500)
            for d in docs:
                xml = await _get_text(d["url"], timeout=10)
                if not xml:
                    continue
                try:
                    root = ET.fromstring(xml)
                except Exception:
                    continue
                item = root.find(".//item")
                if item is None:
                    continue
                title = item.findtext("title", "")
                link = item.findtext("link", "")
                key = f"{title}|{link}"
                if d.get("last") == key:
                    continue
                await get_db().rss.update_one(
                    {"chat_id": d["chat_id"], "url": d["url"]},
                    {"$set": {"last": key}}
                )
                try:
                    await application.bot.send_message(
                        d["chat_id"],
                        f"📰 <b>{title}</b>\n{link}",
                        parse_mode="HTML", disable_web_page_preview=True
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"rss_check_loop error: {e}")
            await asyncio.sleep(60)
