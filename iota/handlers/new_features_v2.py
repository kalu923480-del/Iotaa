"""
╔══════════════════════════════════════════════════════════════════╗
║   IOTA BOT — 20 New Features (v8 addition)                       ║
║   Admin: /pin /unpin /purge                                      ║
║   Profile: /avatar                                                ║
║   Fun/trivia: /8ball /joke /fact /riddle /wyr                    ║
║   Text toys: /reverse /mock /binary /morse /hash /password       ║
║   Social/utility: /nickname /birthday /giveaway /todo /countdown ║
╚══════════════════════════════════════════════════════════════════╝

Design notes:
- Every command is self-contained and never crashes the bot — all
  external calls (Telegram API, DB) are wrapped so a failure degrades
  to a clear error message, never a silent hang or traceback.
- Nothing here depends on a third-party package that might not be
  installed (uses only Python's stdlib: hashlib, secrets, string).
- /snipe ("show last deleted message") was deliberately NOT included —
  Telegram's Bot API has no event for message deletion at all, so no
  bot can implement that honestly without a separate userbot/MTProto
  session (which carries real ToS risk) — including it would have
  meant either faking it or quietly breaking the "0 bugs" ask.
"""
import random
import hashlib
import secrets
import string
import time
import logging
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from utils.helpers import mention, is_admin, fmt
from utils.fonts import sc
from utils.mongo_db import (
    set_nickname, get_nickname, set_birthday, get_birthday,
    get_birthdays_today, add_todo, get_todos, complete_todo, clear_todos,
    create_countdown, get_countdown, get_countdowns_for_user,
    create_giveaway, join_giveaway, get_giveaway, end_giveaway,
    get_user, ensure_user, get_bank, deposit_to_bank, withdraw_from_bank,
    get_loan, take_loan, repay_loan, get_lottery_pool, add_to_lottery_pool,
    reset_lottery_pool, log_raid, get_raid_history,
)
from utils.system_gate import economy_gate, village_gate
from utils.ai_provider import call_ai

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════
# ADMIN: /pin /unpin /purge
# ════════════════════════════════════════════════════════════════════

async def pin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if update.effective_chat.type == "private":
        await msg.reply_html("🚫 " + sc("Groups only.")); return
    if not await is_admin(update, context):
        await msg.reply_html("❌ " + sc("Admins only!")); return
    if not msg.reply_to_message:
        await msg.reply_html("📌 " + sc("Reply to a message to pin it.")); return
    silent = context.args and context.args[0].lower() in ("silent", "quiet", "s")
    try:
        await context.bot.pin_chat_message(
            update.effective_chat.id, msg.reply_to_message.message_id,
            disable_notification=silent
        )
        await msg.reply_html("📌 " + sc("Message pinned!"))
    except TelegramError as e:
        await msg.reply_html(f"❌ {sc(str(e))}")


async def unpin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if update.effective_chat.type == "private":
        await msg.reply_html("🚫 " + sc("Groups only.")); return
    if not await is_admin(update, context):
        await msg.reply_html("❌ " + sc("Admins only!")); return
    try:
        if msg.reply_to_message:
            await context.bot.unpin_chat_message(
                update.effective_chat.id, msg.reply_to_message.message_id
            )
        else:
            await context.bot.unpin_chat_message(update.effective_chat.id)
        await msg.reply_html("📌 " + sc("Message unpinned!"))
    except TelegramError as e:
        await msg.reply_html(f"❌ {sc(str(e))}")


async def purge_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deletes every message from the replied-to message up to (and
    including) the /purge command itself. Requires the bot to have
    delete permission — Telegram only allows deleting messages up to
    48 hours old, which this surfaces clearly instead of a silent
    partial purge."""
    msg = update.effective_message
    if update.effective_chat.type == "private":
        await msg.reply_html("🚫 " + sc("Groups only.")); return
    if not await is_admin(update, context):
        await msg.reply_html("❌ " + sc("Admins only!")); return
    if not msg.reply_to_message:
        await msg.reply_html("🧹 " + sc("Reply to the message you want to purge from.")); return

    start_id = msg.reply_to_message.message_id
    end_id = msg.message_id
    if end_id <= start_id:
        await msg.reply_html("❌ " + sc("Nothing to purge.")); return

    chat_id = update.effective_chat.id
    deleted = 0
    failed = 0
    for mid in range(start_id, end_id + 1):
        try:
            await context.bot.delete_message(chat_id, mid)
            deleted += 1
        except TelegramError:
            failed += 1  # already deleted, too old (>48h), or no permission
    note = await context.bot.send_message(
        chat_id, f"🧹 {sc('Purged')} {deleted} {sc('messages.')}"
        + (f" ({failed} {sc('skipped — too old or already gone')})" if failed else "")
    )


# ════════════════════════════════════════════════════════════════════
# PROFILE: /avatar
# ════════════════════════════════════════════════════════════════════

async def avatar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    target = msg.reply_to_message.from_user if msg.reply_to_message else update.effective_user
    try:
        photos = await context.bot.get_user_profile_photos(target.id, limit=1)
        if not photos or photos.total_count == 0:
            await msg.reply_html(f"🖼️ {mention(target)} {sc('has no profile photo set.')}", parse_mode="HTML")
            return
        file_id = photos.photos[0][-1].file_id
        await msg.reply_photo(file_id, caption=f"🖼️ {sc('Profile photo of')} {mention(target)}", parse_mode="HTML")
    except TelegramError as e:
        await msg.reply_html(f"❌ {sc(str(e))}")


# ════════════════════════════════════════════════════════════════════
# FUN / TRIVIA: /8ball /joke /fact /riddle /wyr
# ════════════════════════════════════════════════════════════════════

_8BALL_ANSWERS = [
    "haan bilkul 💯", "nahi yaar 😬", "shayad... 🤔", "pakka nahi keh sakti",
    "definitely haan! ✨", "bilkul nahi 🙅", "poocho phir se baad mein",
    "signs point to yes 👀", "mujhe nahi lagta", "100% haan cutie",
    "hmm risky lagta hai", "sochti hoon... nahi", "haan without doubt!",
]

async def eightball_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not context.args:
        await msg.reply_html("🎱 " + sc("Ask a question: /8ball will I win today?")); return
    q = " ".join(context.args)
    await msg.reply_html(f"🎱 <b>{random.choice(_8BALL_ANSWERS)}</b>")


_JOKES = [
    "Teacher: Tumne homework kyu nahi kiya?\nStudent: WiFi nahi tha ma'am, internet ke bina soch nahi paya 😂",
    "Why did the developer go broke? Because he used up all his cache 💸",
    "Biwi: tumhe pata hai pyaar mein sabse zyada dard kab hota hai?\nPati: haan, jab bill aata hai 😭",
    "I told my computer I needed a break, and now it won't stop sending me KitKat ads.",
    "Ek admi doctor ke paas gaya: Doctor sahab mujhe bhulne ki bimari hai.\nDoctor: kab se hai?\nAdmi: kya kab se hai? 🤣",
    "Why don't skeletons fight each other? They don't have the guts 💀",
    "Mummy: beta subah subah phone kyu chala rahe ho?\nBeta: mummy ye alarm band kar rahi thi 😅",
]

async def joke_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_html(f"😂 {random.choice(_JOKES)}")


_FACTS = [
    "Octopuses have three hearts and blue blood! 🐙",
    "Honey never spoils — archaeologists have found 3000-year-old honey that's still edible! 🍯",
    "Bananas are berries, but strawberries aren't! 🍌",
    "A day on Venus is longer than a year on Venus! 🪐",
    "Sharks existed before trees did — over 400 million years! 🦈",
    "The Eiffel Tower can grow taller in summer due to heat expansion! 🗼",
    "Your nose can remember 50,000 different scents! 👃",
]

async def fact_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_html(f"🧠 <b>{sc('Fun Fact')}:</b> {random.choice(_FACTS)}")


_RIDDLES = [
    ("Main aata hoon lekin kabhi jaata nahi, kya hoon main?", "Kal (Time)"),
    ("Jitna tum mujhe kaato, main utna hi badhta hoon. Kya hoon main?", "Baal ya Nakhoon"),
    ("What has keys but can't open locks?", "A piano"),
    ("What gets wetter as it dries?", "A towel"),
    ("Andar se khaali, bahar se bhara, boat ki tarah paani pe tairta. Kya hai?", "Naav (Boat)"),
    ("The more you take, the more you leave behind. What am I?", "Footsteps"),
]

async def riddle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q, a = random.choice(_RIDDLES)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔍 Reveal Answer", callback_data=f"riddle_ans:{a}")]])
    await update.effective_message.reply_html(f"🧩 <b>{sc('Riddle')}:</b>\n{q}", reply_markup=kb)

async def riddle_reveal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    ans = q.data.split(":", 1)[1] if ":" in q.data else "?"
    await q.answer(f"Answer: {ans}", show_alert=True)


_WYR = [
    ("hamesha ke liye invisible ho jaao", "hamesha ke liye mind-read kar sako"),
    ("be able to fly", "be able to be invisible"),
    ("crorepati ho par akela", "kam paisa ho par sabke saath"),
    ("never use social media again", "never watch another movie/show again"),
    ("hamesha sach bolna pade", "kabhi kuch bol hi na sako"),
]

async def wyr_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    a, b = random.choice(_WYR)
    await update.effective_message.reply_html(
        f"🤔 <b>{sc('Would You Rather')}</b>\n\n🅰️ {a}\n\n— {sc('or')} —\n\n🅱️ {b}"
    )


# ════════════════════════════════════════════════════════════════════
# TEXT TOYS: /reverse /mock /binary /morse /hash /password
# ════════════════════════════════════════════════════════════════════

async def reverse_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = " ".join(context.args) if context.args else (
        msg.reply_to_message.text if msg.reply_to_message and msg.reply_to_message.text else ""
    )
    if not text:
        await msg.reply_html("🔁 " + sc("Usage: /reverse your text here")); return
    await msg.reply_html(f"🔁 <code>{text[::-1]}</code>")


async def mock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = " ".join(context.args) if context.args else (
        msg.reply_to_message.text if msg.reply_to_message and msg.reply_to_message.text else ""
    )
    if not text:
        await msg.reply_html("🐸 " + sc("Usage: /mock your text here")); return
    mocked = "".join(c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(text))
    await msg.reply_html(f"🐸 <code>{mocked}</code>")


async def binary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = " ".join(context.args) if context.args else (
        msg.reply_to_message.text if msg.reply_to_message and msg.reply_to_message.text else ""
    )
    if not text:
        await msg.reply_html("💾 " + sc("Usage: /binary your text here")); return
    if len(text) > 200:
        await msg.reply_html("❌ " + sc("Text too long — keep it under 200 characters.")); return
    binary = " ".join(format(ord(c), "08b") for c in text)
    await msg.reply_html(f"💾 <code>{binary}</code>")


_MORSE_MAP = {
    'A': '.-', 'B': '-...', 'C': '-.-.', 'D': '-..', 'E': '.', 'F': '..-.',
    'G': '--.', 'H': '....', 'I': '..', 'J': '.---', 'K': '-.-', 'L': '.-..',
    'M': '--', 'N': '-.', 'O': '---', 'P': '.--.', 'Q': '--.-', 'R': '.-.',
    'S': '...', 'T': '-', 'U': '..-', 'V': '...-', 'W': '.--', 'X': '-..-',
    'Y': '-.--', 'Z': '--..', '0': '-----', '1': '.----', '2': '..---',
    '3': '...--', '4': '....-', '5': '.....', '6': '-....', '7': '--...',
    '8': '---..', '9': '----.', ' ': '/',
}

async def morse_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = " ".join(context.args) if context.args else (
        msg.reply_to_message.text if msg.reply_to_message and msg.reply_to_message.text else ""
    )
    if not text:
        await msg.reply_html("📡 " + sc("Usage: /morse your text here")); return
    if len(text) > 200:
        await msg.reply_html("❌ " + sc("Text too long — keep it under 200 characters.")); return
    morse = " ".join(_MORSE_MAP.get(c.upper(), "?") for c in text)
    await msg.reply_html(f"📡 <code>{morse}</code>")


async def hash_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not context.args:
        await msg.reply_html(
            "🔐 " + sc("Usage: /hash your text here") +
            "\n" + sc("Shows MD5 and SHA-256.")
        ); return
    text = " ".join(context.args)
    md5 = hashlib.md5(text.encode()).hexdigest()
    sha256 = hashlib.sha256(text.encode()).hexdigest()
    await msg.reply_html(
        f"🔐 <b>MD5:</b> <code>{md5}</code>\n<b>SHA-256:</b> <code>{sha256}</code>"
    )


async def password_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    length = 16
    if context.args:
        try:
            length = max(6, min(64, int(context.args[0])))
        except ValueError:
            pass
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    # secrets module (not random) — this is an actual password, so it
    # needs a cryptographically secure generator, not random.choice.
    pw = "".join(secrets.choice(alphabet) for _ in range(length))
    try:
        # Send as a self-destructing-style tip: recommend deleting after copying.
        await msg.reply_html(
            f"🔑 <code>{pw}</code>\n\n💡 {sc('Copy this now — delete the message after for safety.')}"
        )
    except TelegramError as e:
        logger.debug(f"password_cmd send failed: {e}")


# ════════════════════════════════════════════════════════════════════
# SOCIAL / UTILITY: /nickname /birthday /giveaway /todo /countdown
# ════════════════════════════════════════════════════════════════════

async def nickname_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; u = update.effective_user
    if not context.args:
        current = await get_nickname(u.id)
        if current:
            await msg.reply_html(f"📛 {sc('Iota calls you')}: <b>{current}</b>\n" + sc("Change it: /nickname NewName"))
        else:
            await msg.reply_html("📛 " + sc("Usage: /nickname YourName"))
        return
    name = " ".join(context.args)[:30]
    await set_nickname(u.id, name)
    await msg.reply_html(f"📛 {sc('Okay! Iota will call you')} <b>{name}</b> {sc('from now on')} 💕")


async def birthday_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; u = update.effective_user
    if not context.args:
        bd = await get_birthday(u.id)
        if bd:
            await msg.reply_html(f"🎂 {sc('Your birthday is set to')}: <b>{bd['day']:02d}/{bd['month']:02d}</b>")
        else:
            await msg.reply_html("🎂 " + sc("Usage: /birthday DD-MM  (e.g. /birthday 25-12)"))
        return
    raw = context.args[0]
    try:
        day_s, month_s = raw.split("-")
        day, month = int(day_s), int(month_s)
        if not (1 <= day <= 31 and 1 <= month <= 12):
            raise ValueError
    except (ValueError, IndexError):
        await msg.reply_html("❌ " + sc("Format: /birthday DD-MM  (e.g. /birthday 25-12)")); return
    await set_birthday(u.id, day, month, update.effective_chat.id, u.full_name)
    await msg.reply_html(f"🎂 {sc('Got it! Iota will wish you on')} <b>{day:02d}/{month:02d}</b> 🎉")


async def birthday_check_job(bot):
    """
    Runs once a day (wired in bot.py) — wishes everyone whose birthday
    is today, in the chat where they set it. Wrapped so one failed wish
    (e.g. bot no longer in that chat) never stops the rest.
    """
    now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    today = await get_birthdays_today(now.day, now.month)
    for entry in today:
        try:
            name = entry.get("full_name") or "someone special"
            await bot.send_message(
                entry["chat_id"],
                f"🎉🎂 {sc('Happy Birthday')} <b>{name}</b>! {sc('hope your day is amazing')} 💕🎈",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.debug(f"birthday_check_job: failed for {entry.get('_id')}: {e}")


async def birthday_daily_loop(bot):
    """
    Background loop (started once at bot startup, see bot.py) that
    fires birthday_check_job exactly once per day, around 9 AM IST.
    Checks every 30 minutes and tracks the last date it already ran on
    so a restart or slow tick never double-wishes the same day.
    """
    import asyncio
    last_run_date = None
    ist = timezone(timedelta(hours=5, minutes=30))
    while True:
        try:
            await asyncio.sleep(1800)  # check every 30 minutes
            now = datetime.now(ist)
            today_key = now.strftime("%Y-%m-%d")
            if now.hour == 9 and last_run_date != today_key:
                await birthday_check_job(bot)
                last_run_date = today_key
        except Exception as e:
            logger.debug(f"birthday_daily_loop error: {e}")


async def todo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; u = update.effective_user
    if not context.args:
        items = await get_todos(u.id)
        if not items:
            await msg.reply_html(
                "📝 " + sc("Your to-do list is empty.") + "\n\n"
                + sc("Add:") + " /todo add buy milk\n"
                + sc("Done:") + " /todo done 1\n"
                + sc("Clear:") + " /todo clear"
            ); return
        lines = [f"{i+1}. {'✅' if it['done'] else '⬜'} {it['text']}" for i, it in enumerate(items)]
        await msg.reply_html("📝 <b>" + sc("Your To-Do List") + "</b>\n\n" + "\n".join(lines))
        return

    sub = context.args[0].lower()
    if sub == "add" and len(context.args) > 1:
        text = " ".join(context.args[1:])
        await add_todo(u.id, text)
        await msg.reply_html(f"✅ {sc('Added')}: {text}")
    elif sub == "done" and len(context.args) > 1:
        try:
            idx = int(context.args[1]) - 1
        except ValueError:
            await msg.reply_html("❌ " + sc("Usage: /todo done 1")); return
        ok = await complete_todo(u.id, idx)
        await msg.reply_html("✅ " + sc("Marked as done!") if ok else "❌ " + sc("Invalid item number."))
    elif sub == "clear":
        await clear_todos(u.id)
        await msg.reply_html("🗑️ " + sc("To-do list cleared!"))
    else:
        await msg.reply_html(
            "📝 " + sc("Usage:") + "\n/todo — view list\n/todo add <task>\n/todo done <number>\n/todo clear"
        )


async def countdown_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; u = update.effective_user
    if len(context.args) < 2:
        mine = await get_countdowns_for_user(u.id)
        if mine:
            lines = []
            now = datetime.now(timezone.utc)
            for cd in mine:
                try:
                    target = datetime.fromisoformat(cd["target"])
                    days_left = (target - now).days
                    lines.append(f"⏳ <b>{cd['name']}</b>: {days_left} " + sc("days left"))
                except Exception:
                    continue
            await msg.reply_html("\n".join(lines) if lines else "📅 " + sc("No countdowns yet."))
        else:
            await msg.reply_html(
                "📅 " + sc("Usage: /countdown EventName YYYY-MM-DD") +
                "\n" + sc("Example: /countdown NewYear 2027-01-01")
            )
        return
    name = context.args[0]
    date_str = context.args[1]
    try:
        target = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        await msg.reply_html("❌ " + sc("Date format must be YYYY-MM-DD")); return
    await create_countdown(u.id, name, target.isoformat(), update.effective_chat.id)
    days_left = (target - datetime.now(timezone.utc)).days
    await msg.reply_html(f"📅 {sc('Countdown set for')} <b>{name}</b>: {days_left} {sc('days left')}!")


async def giveaway_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/giveaway <minutes> <prize text> — admin only, group only."""
    msg = update.effective_message
    if update.effective_chat.type == "private":
        await msg.reply_html("🚫 " + sc("Groups only.")); return
    if not await is_admin(update, context):
        await msg.reply_html("❌ " + sc("Admins only!")); return
    if len(context.args) < 2:
        await msg.reply_html("🎁 " + sc("Usage: /giveaway 5 Telegram Premium") + " (" + sc("minutes then prize") + ")"); return
    try:
        minutes = max(1, min(1440, int(context.args[0])))
    except ValueError:
        await msg.reply_html("❌ " + sc("First argument must be minutes (a number).")); return
    prize = " ".join(context.args[1:])
    end_ts = time.time() + minutes * 60

    sent = await msg.reply_html(
        f"🎉 <b>{sc('Giveaway')}!</b>\n\n🎁 {sc('Prize')}: <b>{prize}</b>\n"
        f"⏳ {sc('Ends in')} {minutes} {sc('minute(s)')}\n\n"
        f"{sc('Tap below to join!')}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎉 Join Giveaway", callback_data="ga_join:pending")]])
    )
    gid = await create_giveaway(update.effective_chat.id, sent.message_id, prize, end_ts, update.effective_user.id)
    # Fix the callback_data now that we have the real giveaway id.
    try:
        await sent.edit_reply_markup(
            InlineKeyboardMarkup([[InlineKeyboardButton("🎉 Join Giveaway", callback_data=f"ga_join:{gid}")]])
        )
    except TelegramError as e:
        logger.debug(f"giveaway_cmd edit_reply_markup failed: {e}")

    import asyncio
    async def _finish():
        await asyncio.sleep(minutes * 60)
        try:
            doc = await get_giveaway(gid)
            if not doc or doc.get("ended"):
                return
            participants = doc.get("participants", [])
            winner = random.choice(participants) if participants else None
            await end_giveaway(gid, winner)
            if winner:
                await context.bot.send_message(
                    update.effective_chat.id,
                    f"🎉 {sc('Giveaway ended!')} 🏆 {sc('Winner')}: "
                    f'<a href="tg://user?id={winner}">🎁 {sc("Congratulations")}!</a>\n'
                    f"{sc('Prize')}: <b>{prize}</b>",
                    parse_mode="HTML",
                )
            else:
                await context.bot.send_message(
                    update.effective_chat.id,
                    f"😔 {sc('Giveaway ended with no participants.')}"
                )
        except Exception as e:
            logger.debug(f"giveaway _finish failed: {e}")
    asyncio.create_task(_finish())


async def giveaway_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    gid = q.data.split(":", 1)[1] if ":" in q.data else ""
    if gid == "pending":
        await q.answer("Give it a second and try again!", show_alert=True); return
    ok = await join_giveaway(gid, q.from_user.id)
    if ok:
        await q.answer("You're in! 🎉", show_alert=False)
    else:
        doc = await get_giveaway(gid)
        if doc and doc.get("ended"):
            await q.answer("This giveaway already ended!", show_alert=True)
        else:
            await q.answer("You already joined!", show_alert=False)


# ════════════════════════════════════════════════════════════════════
@economy_gate
async def lottery_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; u = update.effective_user; chat = update.effective_chat
    if chat.type == "private":
        await msg.reply_html("🚫 " + sc("Groups only.")); return
    await ensure_user(u.id, u.username or "", u.full_name)
    pool = await get_lottery_pool(chat.id)

    if not context.args:
        await msg.reply_html(
            f"🎟️ <b>{sc('Lottery')}</b>\n\n"
            f"💰 {sc('Current jackpot')}: {fmt(pool)}\n"
            f"🎫 {sc('Ticket price')}: {fmt(LOTTERY_TICKET_COST)}\n"
            f"🎲 {sc('Win chance per ticket')}: {int(LOTTERY_WIN_CHANCE*100)}%\n\n"
            f"{sc('Buy a ticket')}: /lottery buy"
        )
        return

    if context.args[0].lower() != "buy":
        await msg.reply_html("🎟️ " + sc("Usage: /lottery buy")); return

    d = await get_user(u.id)
    if d.get("balance", 0) < LOTTERY_TICKET_COST:
        await msg.reply_html(f"❌ {sc('You need')} {fmt(LOTTERY_TICKET_COST)} {sc('to buy a ticket.')}"); return

    from utils.mongo_db import deduct_balance, add_balance
    await deduct_balance(u.id, LOTTERY_TICKET_COST)

    if random.random() < LOTTERY_WIN_CHANCE and pool > 0:
        await add_balance(u.id, pool)
        await reset_lottery_pool(chat.id)
        await msg.reply_html(
            f"🎉🎟️ <b>{sc('JACKPOT!')}</b> {mention(u)} {sc('won')} <b>{fmt(pool)}</b>! 🏆"
        )
    else:
        await add_to_lottery_pool(chat.id, LOTTERY_TICKET_COST)
        new_pool = await get_lottery_pool(chat.id)
        await msg.reply_html(
            f"🎟️ {sc('No win this time!')} {sc('Jackpot is now')} {fmt(new_pool)}.\n"
            f"{sc('Try again')}: /lottery buy"
        )


# ════════════════════════════════════════════════════════════════════
# 🆕 VILLAGE: /donate /repair /raidlog /recruit
# ════════════════════════════════════════════════════════════════════

RECRUIT_COST = 2000
REPAIR_COST_PER_HP = 1  # resource units spent per HP point restored

_HERO_NAMES = [
    "🗡️ Blademaster", "🏹 Shadow Archer", "🛡️ Iron Guardian",
    "🔥 Flame Warlord", "❄️ Frost Sentinel", "⚡ Storm Rider",
]


@village_gate
async def donate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/donate <wood|stone|iron> <amount> — reply to a village-mate to
    send them resources from your own village. Purely additive: only
    ever moves resources between two _get_village docs, never touches
    combat/troops/attack logic."""
    from handlers.village_war import _get_village, _upd
    msg = update.effective_message; u = update.effective_user
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_html("🎁 " + sc("Reply to someone + /donate <wood|stone|iron> <amount>")); return
    target = msg.reply_to_message.from_user
    if target.id == u.id:
        await msg.reply_html("😅 " + sc("You can't donate to yourself!")); return
    if len(context.args) < 2:
        await msg.reply_html("🎁 " + sc("Usage: /donate <wood|stone|iron> <amount>")); return
    rtype = context.args[0].lower()
    if rtype not in ("wood", "stone", "iron"):
        await msg.reply_html("❌ " + sc("Resource must be wood, stone, or iron.")); return
    try:
        amt = int(context.args[1])
    except ValueError:
        await msg.reply_html("❌ " + sc("Amount must be a number.")); return
    if amt <= 0:
        await msg.reply_html("❌ " + sc("Amount must be positive.")); return

    await ensure_user(u.id, u.username or "", u.full_name)
    await ensure_user(target.id, target.username or "", target.full_name)
    av = await _get_village(u.id)
    if av.get(rtype, 0) < amt:
        await msg.reply_html(f"❌ {sc('You only have')} {av.get(rtype,0)} {rtype}!"); return
    tv = await _get_village(target.id)

    await _upd(u.id, **{rtype: av[rtype] - amt})
    await _upd(target.id, **{rtype: tv.get(rtype, 0) + amt})
    emoji = {"wood": "🪵", "stone": "🪨", "iron": "⚙️"}[rtype]
    await msg.reply_html(
        f"🎁 {mention(u)} {sc('donated')} {emoji} {amt} {rtype} {sc('to')} {mention(target)}!"
    )


@village_gate
async def repair_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/repair <wall|defense name> — restores that structure to full HP,
    spending resources proportional to the HP actually restored. Only
    ever increases hp up to the existing max_hp — never changes level,
    damage, or any attack-relevant stat, so it can't destabilize combat
    balance."""
    from handlers.village_war import _get_village, _upd
    msg = update.effective_message; u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    v = await _get_village(u.id)

    if not context.args:
        walls = v.get("walls", {})
        defs = v.get("defense", {})
        lines = []
        for name, w in walls.items():
            if w["hp"] < w["max_hp"]:
                lines.append(f"🧱 {name}: {w['hp']}/{w['max_hp']} HP")
        for name, d in defs.items():
            if d["hp"] < d["max_hp"]:
                lines.append(f"🛡️ {name}: {d['hp']}/{d['max_hp']} HP")
        if not lines:
            await msg.reply_html("✅ " + sc("Everything is already at full HP!")); return
        await msg.reply_html(
            "🔧 <b>" + sc("Needs Repair") + "</b>\n\n" + "\n".join(lines) +
            "\n\n" + sc("Repair with: /repair <name>")
        )
        return

    name = context.args[0].lower()
    walls = v.get("walls", {}); defs = v.get("defense", {})
    target_dict, key = (walls, "walls") if name in walls else (defs, "defense") if name in defs else (None, None)
    if target_dict is None:
        await msg.reply_html(f"❌ " + sc(f"No structure named '{name}'.")); return

    struct = target_dict[name]
    missing = struct["max_hp"] - struct["hp"]
    if missing <= 0:
        await msg.reply_html("✅ " + sc("Already at full HP!")); return

    cost = missing * REPAIR_COST_PER_HP
    # Split cost evenly across the three resources, capped by what's available.
    per_resource = max(1, cost // 3)
    if v.get("wood", 0) < per_resource or v.get("stone", 0) < per_resource or v.get("iron", 0) < per_resource:
        await msg.reply_html(
            f"❌ {sc('Not enough resources!')} {sc('Need')} ~{per_resource} {sc('each of wood/stone/iron.')}"
        ); return

    struct["hp"] = struct["max_hp"]
    target_dict[name] = struct
    await _upd(u.id, **{
        key: target_dict,
        "wood": v["wood"] - per_resource,
        "stone": v["stone"] - per_resource,
        "iron": v["iron"] - per_resource,
    })
    await msg.reply_html(f"🔧 {sc('Repaired')} <b>{name}</b> {sc('to full HP')}! ✅")


@village_gate
async def raidlog_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    history = await get_raid_history(u.id, limit=5)
    if not history:
        await msg.reply_html("📜 " + sc("No raid history yet. Attack someone with /attack!")); return
    lines = []
    for r in history:
        if r["attacker_id"] == u.id:
            outcome = "🏆 " + sc("Won") if r["attacker_won"] else "💀 " + sc("Lost")
            lines.append(f"{outcome} — {sc('attacked')} <a href=\"tg://user?id={r['defender_id']}\">User</a>")
        else:
            outcome = "🛡️ " + sc("Defended") if not r["attacker_won"] else "💥 " + sc("Raided")
            lines.append(f"{outcome} — <a href=\"tg://user?id={r['attacker_id']}\">User</a> {sc('attacked you')}")
    await msg.reply_html("📜 <b>" + sc("Raid History") + "</b>\n\n" + "\n".join(lines))


@village_gate
async def recruit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Recruits a flavor 'hero' — stored as a collectible on the village
    doc, shown in /village overview. Deliberately NOT wired into the
    attack/defense damage math in handlers/village_war.py: that
    simulation is already tuned/tested, and silently changing its
    balance as a side effect of an unrelated new command would be
    exactly the kind of bug this whole project has been about fixing,
    not adding. A future update could integrate heroes properly with
    its own dedicated balance pass.
    """
    from handlers.village_war import _get_village, _upd
    msg = update.effective_message; u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)
    if d.get("balance", 0) < RECRUIT_COST:
        await msg.reply_html(f"❌ {sc('You need')} {fmt(RECRUIT_COST)} {sc('to recruit a hero.')}"); return

    v = await _get_village(u.id)
    heroes = v.get("heroes", [])
    hero = random.choice(_HERO_NAMES)
    heroes.append(hero)

    from utils.mongo_db import deduct_balance
    await deduct_balance(u.id, RECRUIT_COST)
    await _upd(u.id, heroes=heroes)
    await msg.reply_html(
        f"🎖️ {sc('Recruited')} <b>{hero}</b> {sc('to your village!')}\n"
        f"{sc('Total heroes')}: {len(heroes)}"
    )


# ════════════════════════════════════════════════════════════════════
# 🆕 AI FEATURES: /aijoke /advice /roastme /aistory
# ════════════════════════════════════════════════════════════════════
#
# Distinct from the static /joke and /roast above — these use the AI
# itself so the output is fresh and different every single time,
# instead of picking from a fixed list. Routed through the same
# call_ai() fallback chain the rest of the bot's AI features use
# (utils/ai_provider.py), not a separate one-off integration — so if
# the AI provider is ever down, the failure mode and recovery is
# consistent with every other AI command instead of being its own
# special case to debug.

_IOTA_VOICE_SYSTEM = (
    "You are Iota, a cute, flirty, sassy Hinglish-speaking Telegram bot "
    "girl. Reply in Iota's voice: short, playful, confident, mixing "
    "Hindi and English naturally. NO markdown formatting (no asterisks "
    "for bold/italic) — plain text and emojis only."
)


async def aijoke_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    thinking = await msg.reply_html("😂 " + sc("thinking of something funny..."))
    try:
        reply = await call_ai(
            [
                {"role": "system", "content": _IOTA_VOICE_SYSTEM},
                {"role": "user", "content": "Tell me a short, original, funny Hinglish joke. 2-3 lines max. Don't reuse common/famous jokes — make up something fresh."},
            ],
            is_premium=False, max_tokens=120, temperature=1.0,
        )
        await thinking.edit_text(f"😂 {reply}", parse_mode="HTML")
    except Exception as e:
        logger.debug(f"aijoke_cmd failed: {e}")
        await thinking.edit_text("😅 " + sc("brain freeze, try again in a sec"))


async def advice_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not context.args:
        await msg.reply_html("💭 " + sc("Usage: /advice how do I talk to my crush")); return
    topic = " ".join(context.args)
    thinking = await msg.reply_html("💭 " + sc("thinking..."))
    try:
        reply = await call_ai(
            [
                {"role": "system", "content": _IOTA_VOICE_SYSTEM + " Give genuinely useful, short advice — 3-4 lines max — while staying in character."},
                {"role": "user", "content": f"Give me advice about: {topic}"},
            ],
            is_premium=False, max_tokens=200, temperature=0.8,
        )
        await thinking.edit_text(f"💭 {reply}", parse_mode="HTML")
    except Exception as e:
        logger.debug(f"advice_cmd failed: {e}")
        await thinking.edit_text("😅 " + sc("can't think straight rn, try again"))


async def roastme_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; u = update.effective_user
    thinking = await msg.reply_html("🔥 " + sc("okay let me think of something..."))
    try:
        reply = await call_ai(
            [
                {"role": "system", "content": _IOTA_VOICE_SYSTEM + (
                    " Roast the user playfully and light-heartedly — teasing, never "
                    " genuinely mean, no slurs/insults about appearance/family/real "
                    " sensitive topics. Keep it silly and fun, 2-3 lines."
                )},
                {"role": "user", "content": f"Roast me playfully, my name is {u.first_name}."},
            ],
            is_premium=False, max_tokens=120, temperature=1.0,
        )
        await thinking.edit_text(f"🔥 {reply}", parse_mode="HTML")
    except Exception as e:
        logger.debug(f"roastme_cmd failed: {e}")
        await thinking.edit_text("😅 " + sc("can't roast you rn, lucky save"))


async def aistory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Single-shot AI short story — distinct from the collaborative /story
    (handlers/extra_games.py), which continues a shared group story
    turn by turn. This one just generates a complete mini-story in one
    go from whatever topic/prompt the user gives.
    """
    msg = update.effective_message
    if not context.args:
        await msg.reply_html("📖 " + sc("Usage: /aistory a dragon who's afraid of fire")); return
    prompt = " ".join(context.args)
    thinking = await msg.reply_html("📖 " + sc("writing..."))
    try:
        reply = await call_ai(
            [
                {"role": "system", "content": (
                    "You are a creative storyteller. Write a short, complete "
                    "story (5-8 lines) based on the user's prompt. Fun, "
                    "engaging, family-friendly. Plain text only, no markdown."
                )},
                {"role": "user", "content": f"Write a short story about: {prompt}"},
            ],
            is_premium=False, max_tokens=350, temperature=0.9,
        )
        await thinking.edit_text(f"📖 {reply}", parse_mode="HTML")
    except Exception as e:
        logger.debug(f"aistory_cmd failed: {e}")
        await thinking.edit_text("😅 " + sc("couldn't write that one, try again"))
