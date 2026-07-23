"""
╔══════════════════════════════════════════════════╗
║   IOTA BOT — New Commands & Systems             ║
║   Weather | Poll | Calc | Leaderboard+          ║
║   Streak | Trivia | Confession | Marry          ║
╚══════════════════════════════════════════════════╝
"""

import random, asyncio, time, re
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, Poll
from telegram.ext import ContextTypes
from utils.mongo_db import ensure_user, get_user, update_user, add_balance, deduct_balance, get_db
from utils.helpers import mention, fmt, xp_level, rank_title
from utils.game_rules import bot_game_disabled_msg, PVP_GAMES_HINT

# ─────────────────────────────────────────────────────────────
#  /calc — Calculator
# ─────────────────────────────────────────────────────────────
async def calc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_html(
            "🧮 <b>Calculator</b>\n\n"
            "Usage: <code>/calc 2 + 2</code>\n"
            "Supports: + - * / ** % ()\n"
            "Example: <code>/calc (100 * 5) / 2 + 50</code>"
        ); return

    expr = " ".join(context.args)
    # Safety: only allow numbers and math operators
    safe = re.sub(r'[^0-9\s\+\-\*\/\%\(\)\.\*\*]', '', expr)
    if not safe.strip():
        await update.message.reply_html("❌ Invalid expression!"); return

    try:
        result = eval(safe, {"__builtins__": {}}, {})
        await update.message.reply_html(
            f"🧮 <b>Calculator</b>\n\n"
            f"📥 Input: <code>{expr}</code>\n"
            f"📤 Result: <b>{result}</b>"
        )
    except Exception as e:
        await update.message.reply_html(f"❌ Error: {e}")


# ─────────────────────────────────────────────────────────────
#  /poll — Create Quick Poll
# ─────────────────────────────────────────────────────────────
async def poll_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.message.reply_html(
            "📊 <b>Poll</b>\n\n"
            "Usage: <code>/poll Question | Option1 | Option2 | Option3</code>\n"
            "Example: <code>/poll Favorite color? | Red | Blue | Green</code>"
        ); return

    full = " ".join(context.args)
    parts = [p.strip() for p in full.split("|")]
    if len(parts) < 3:
        await update.message.reply_html("❌ Kam se kam 2 options do! Format: Question | Opt1 | Opt2"); return

    question = parts[0][:255]
    options  = [o[:100] for o in parts[1:10]]  # Max 10 options

    await update.message.reply_poll(
        question=question,
        options=options,
        is_anonymous=False,
        allows_multiple_answers=False
    )


# ─────────────────────────────────────────────────────────────
#  /marry — Marriage System
# ─────────────────────────────────────────────────────────────
_proposals: dict = {}  # user_id -> {to: user_id, msg_id: int, time: int}

async def marry_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u   = update.effective_user
    msg = update.effective_message

    if not msg.reply_to_message:
        await update.message.reply_html(
            "💍 <b>Marry System</b>\n\n"
            "Kisi ko propose karne ke liye unke message ko reply karo:\n"
            "<code>/marry</code> [reply karo]"
        ); return

    target = msg.reply_to_message.from_user
    if target.id == u.id:
        await update.message.reply_html("❌ Khud se shadi nahi kar sakte! 😂"); return
    if target.is_bot:
        await update.message.reply_html("❌ Bot se shadi? Pagal ho kya? 🤖"); return

    await ensure_user(u.id, u.username or "", u.full_name)
    await ensure_user(target.id, target.username or "", target.full_name)

    ud  = await get_user(u.id)
    td  = await get_user(target.id)

    if ud.get("married_to"):
        await update.message.reply_html(
            f"❌ Tum already shadi shuda ho! Pehle /divorce karo."
        ); return
    if td.get("married_to"):
        await update.message.reply_html(
            f"❌ {mention(target)} already shadi shuda hai!"
        ); return

    _proposals[target.id] = {"from": u.id, "from_name": u.first_name, "time": int(time.time())}

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("💍 Haan, Qabool Hai!", callback_data=f"marry_accept_{u.id}"),
        InlineKeyboardButton("💔 Na, Main Free Hoon!", callback_data=f"marry_reject_{u.id}"),
    ]])

    await update.message.reply_html(
        f"💍 <b>Shaadi Ka Rishta!</b>\n\n"
        f"{mention(u)} ne {mention(target)} ko propose kiya!\n\n"
        f"❤️ Kya {target.first_name} maan legi/lega?",
        reply_markup=kb
    )


async def marry_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    u = q.from_user
    await q.answer()

    if q.data.startswith("marry_accept_"):
        from_id = int(q.data[len("marry_accept_"):])
        prop    = _proposals.get(u.id)
        if not prop or prop["from"] != from_id:
            await q.answer("Yeh proposal tumhare liye nahi!", show_alert=True); return

        _proposals.pop(u.id, None)
        # Save marriage
        db = get_db()
        await db.users.update_one({"_id": from_id}, {"$set": {"married_to": u.id, "married_name": u.first_name}})
        await db.users.update_one({"_id": u.id},     {"$set": {"married_to": from_id, "married_name": prop["from_name"]}})
        # Bonus coins
        await add_balance(from_id, 1000)
        await add_balance(u.id,    1000)

        await q.edit_message_text(
            f"💒 <b>SHAADI HO GAYI!</b> 🎊\n\n"
            f"💍 {prop['from_name']} ❤️ {u.first_name}\n\n"
            f"Mubarak ho! Dono ko 1,000 💰 coins mili!\n"
            f"📋 Details: /couple",
            parse_mode="HTML"
        )

    elif q.data.startswith("marry_reject_"):
        from_id = int(q.data[len("marry_reject_"):])
        prop    = _proposals.get(u.id)
        if prop:
            _proposals.pop(u.id, None)

        await q.edit_message_text(
            f"💔 <b>Proposal Reject Ho Gaya!</b>\n\n"
            f"{u.first_name} ne mana kar diya... 😢\n"
            f"Better luck next time!",
            parse_mode="HTML"
        )


async def divorce_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)

    if not d.get("married_to"):
        await update.message.reply_html("❌ Tum shadi shuda nahi ho!"); return

    partner_id   = d["married_to"]
    partner_name = d.get("married_name", "Unknown")

    db = get_db()
    await db.users.update_one({"_id": u.id},        {"$unset": {"married_to": "", "married_name": ""}})
    await db.users.update_one({"_id": partner_id},  {"$unset": {"married_to": "", "married_name": ""}})

    await update.message.reply_html(
        f"💔 <b>Talaq Ho Gayi!</b>\n\n"
        f"{mention(u)} aur {partner_name} ka rishta khatam...\n"
        f"Sad times. 💔"
    )


async def couple_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)

    if not d.get("married_to"):
        await update.message.reply_html(
            f"💍 {mention(u)} abhi single hai!\n"
            f"Propose karne ke liye: /marry [reply]"
        ); return

    partner_id   = d["married_to"]
    partner_name = d.get("married_name", "Partner")

    await update.message.reply_html(
        f"💒 <b>Couple Info</b>\n\n"
        f"💍 {u.first_name} ❤️ {partner_name}\n\n"
        f"Bahut pyaara joda hai! 🌹\n"
        f"💔 Alag hona ho to: /divorce"
    )


# ─────────────────────────────────────────────────────────────
#  /streak — Daily Streak Tracker
# ─────────────────────────────────────────────────────────────
async def streak_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)

    streak     = d.get("daily_streak", 0)
    max_streak = d.get("max_streak", 0)
    last_daily = d.get("last_daily", 0)
    now        = int(time.time())

    # Check if streak is still active
    hours_since = (now - last_daily) / 3600
    streak_active = hours_since < 48  # Grace period

    if streak_active:
        status = "🔥 Active"
        bar = "🟩" * min(streak, 10) + "⬜" * max(0, 10 - streak)
    else:
        status = "❄️ Broken"
        bar = "⬜" * 10

    # Streak milestones
    milestones = {7: "🌟", 14: "💫", 30: "🏆", 60: "👑", 100: "💎"}
    badges = " ".join(v for k, v in milestones.items() if streak >= k) or "None yet"

    await update.message.reply_html(
        f"🔥 <b>Daily Streak — {mention(u)}</b>\n\n"
        f"{bar}\n\n"
        f"🎯 Current Streak: <b>{streak} days</b> ({status})\n"
        f"🏆 Best Streak: <b>{max_streak} days</b>\n"
        f"🎖️ Badges: {badges}\n\n"
        f"💡 Daily /daily karo streak maintain rakhne ke liye!\n"
        f"Streak bonuses:\n"
        f"  7 days → +500 coins\n"
        f"  14 days → +1,000 coins\n"
        f"  30 days → +5,000 coins"
    )


# ─────────────────────────────────────────────────────────────
#  /confession — Anonymous Confession
# ─────────────────────────────────────────────────────────────
async def confession_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_html(
            "🤫 <b>Anonymous Confession</b>\n\n"
            "Usage: <code>/confession Mujhe XYZ se pyaar hai...</code>\n"
            "Tumhara naam nahi bataya jaega!"
        ); return

    text    = " ".join(context.args)[:500]
    chat    = update.effective_chat
    u       = update.effective_user
    rand_id = random.randint(1000, 9999)

    await update.message.delete()  # delete user's command
    await context.bot.send_message(
        chat.id,
        f"🤫 <b>Anonymous Confession #{rand_id}</b>\n\n"
        f"💭 \"{text}\"\n\n"
        f"<i>Sent anonymously via Iota Bot</i>",
        parse_mode="HTML"
    )


# ─────────────────────────────────────────────────────────────
#  /trivia — Quick Trivia Questions
# ─────────────────────────────────────────────────────────────
TRIVIA_QS = [
    {"q": "Duniya ka sabse bada desh kaun sa hai?", "a": "Russia", "opts": ["India", "Russia", "USA", "China"]},
    {"q": "Python language kisne banai?", "a": "Guido van Rossum", "opts": ["Guido van Rossum", "Linus Torvalds", "Dennis Ritchie", "James Gosling"]},
    {"q": "Water ka chemical formula kya hai?", "a": "H2O", "opts": ["H2O", "CO2", "NaCl", "HCl"]},
    {"q": "Instagram kisne banaya?", "a": "Kevin Systrom", "opts": ["Mark Zuckerberg", "Kevin Systrom", "Jack Dorsey", "Larry Page"]},
    {"q": "Ek week mein kitne din hote hain?", "a": "7", "opts": ["5", "6", "7", "8"]},
    {"q": "Taj Mahal kahan hai?", "a": "Agra", "opts": ["Delhi", "Mumbai", "Agra", "Jaipur"]},
    {"q": "1 + 1 = ?", "a": "2", "opts": ["1", "2", "3", "11"]},
    {"q": "Telegram kab launch hua?", "a": "2013", "opts": ["2010", "2012", "2013", "2015"]},
    {"q": "CPU ka full form?", "a": "Central Processing Unit", "opts": ["Central Processing Unit", "Computer Power Unit", "Core Processing Unit", "Control Power Unit"]},
    {"q": "Konsa planet sabse bada hai?", "a": "Jupiter", "opts": ["Earth", "Saturn", "Jupiter", "Mars"]},
]

_trivia_active: dict = {}  # chat_id -> question dict

async def trivia_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    u    = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)

    if chat.id in _trivia_active:
        q = _trivia_active[chat.id]
        await update.message.reply_html(
            f"❓ Abhi bhi ek trivia chal rahi hai!\n"
            f"<b>{q['q']}</b>"
        ); return

    q        = random.choice(TRIVIA_QS).copy()
    reward   = random.randint(200, 500)
    q["reward"]  = reward
    q["asked_by"] = u.id
    q["time"]     = int(time.time())
    opts     = q["opts"].copy()
    random.shuffle(opts)
    q["shuffled"] = opts
    _trivia_active[chat.id] = q

    kb_rows = []
    for i, opt in enumerate(opts):
        kb_rows.append([InlineKeyboardButton(f"{'ABCD'[i]}) {opt}", callback_data=f"trivia_{i}")])

    await update.message.reply_html(
        f"❓ <b>Trivia Question!</b>\n"
        f"💰 Reward: {fmt(reward)}\n\n"
        f"<b>{q['q']}</b>",
        reply_markup=InlineKeyboardMarkup(kb_rows)
    )

    # Auto-close after 30s
    asyncio.create_task(_trivia_timeout(context, chat.id, 30))


async def trivia_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q_obj = update.callback_query
    u     = q_obj.from_user
    await q_obj.answer()

    chat = update.effective_chat
    data = q_obj.data

    if not data.startswith("trivia_"):
        return

    idx   = int(data[len("trivia_"):])
    trivia = _trivia_active.get(chat.id)
    if not trivia:
        await q_obj.answer("Trivia khatam ho gayi!", show_alert=True); return

    chosen = trivia["shuffled"][idx]
    await ensure_user(u.id, u.username or "", u.full_name)

    if chosen == trivia["a"]:
        _trivia_active.pop(chat.id, None)
        reward = trivia["reward"]
        await add_balance(u.id, reward)
        await q_obj.edit_message_text(
            f"✅ <b>Sahi Jawab!</b>\n\n"
            f"🎉 {mention(u)} ne sahi jawab diya!\n"
            f"Jawab: <b>{trivia['a']}</b>\n"
            f"💰 +{fmt(reward)} coins!",
            parse_mode="HTML"
        )
    else:
        await q_obj.answer(f"❌ Galat! Sahi: {trivia['a']}", show_alert=True)


async def _trivia_timeout(context, chat_id, secs):
    await asyncio.sleep(secs)
    trivia = _trivia_active.pop(chat_id, None)
    if trivia:
        try:
            await context.bot.send_message(
                chat_id,
                f"⏱️ Trivia khatam! Kisi ne jawab nahi diya.\n"
                f"Sahi jawab: <b>{trivia['a']}</b>",
                parse_mode="HTML"
            )
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
#  /afk — AFK System
# ─────────────────────────────────────────────────────────────
_afk_users: dict = {}  # user_id -> {reason, time}

async def afk_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u      = update.effective_user
    reason = " ".join(context.args) if context.args else "No reason"
    _afk_users[u.id] = {"reason": reason[:100], "time": int(time.time())}
    await update.message.reply_html(
        f"😴 <b>{mention(u)} AFK ho gaye!</b>\n"
        f"📝 Reason: {reason}"
    )


async def afk_check_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check if mentioned user is AFK."""
    u   = update.effective_user
    msg = update.effective_message
    if not msg or not msg.text:
        return

    # Check if AFK user came back
    if u.id in _afk_users:
        afk = _afk_users.pop(u.id)
        elapsed = int(time.time()) - afk["time"]
        h, m = divmod(elapsed // 60, 60)
        await msg.reply_html(
            f"👋 <b>{mention(u)} wapas aa gaye!</b>\n"
            f"⏱️ Baahar the: {h}h {m}m"
        )
        return

    # Check mentions
    if msg.reply_to_message:
        tu = msg.reply_to_message.from_user
        if tu and tu.id in _afk_users:
            afk = _afk_users[tu.id]
            elapsed = int(time.time()) - afk["time"]
            h, m = divmod(elapsed // 60, 60)
            await msg.reply_html(
                f"😴 <b>{tu.first_name} AFK hai!</b>\n"
                f"📝 Reason: {afk['reason']}\n"
                f"⏱️ {h}h {m}m se AFK"
            )


# ─────────────────────────────────────────────────────────────
#  /roll — Dice Roll (with bet)
# ─────────────────────────────────────────────────────────────
async def diceroll_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        bot_game_disabled_msg("Dice Roll (vs Iota)") + PVP_GAMES_HINT
    )


# ─────────────────────────────────────────────────────────────
#  /bio — User Bio System
# ─────────────────────────────────────────────────────────────
async def setbio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u   = update.effective_user
    bio = " ".join(context.args)[:200] if context.args else ""

    if not bio:
        await update.message.reply_html(
            "📝 Usage: <code>/setbio Tumhari bio yahan likho</code>"
        ); return

    await ensure_user(u.id, u.username or "", u.full_name)
    db = get_db()
    await db.users.update_one({"_id": u.id}, {"$set": {"bio": bio}})
    await update.message.reply_html(
        f"✅ Bio save ho gayi!\n\n📝 {bio}"
    )


async def bio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if msg.reply_to_message:
        tu = msg.reply_to_message.from_user
    else:
        tu = update.effective_user

    await ensure_user(tu.id, tu.username or "", tu.full_name)
    d   = await get_user(tu.id)
    bio = d.get("bio", "No bio set. Use /setbio to add one!")

    await update.message.reply_html(
        f"📝 <b>Bio — {mention(tu)}</b>\n\n{bio}"
    )


# ─────────────────────────────────────────────────────────────
#  /global_rank — Extended Global Leaderboard
# ─────────────────────────────────────────────────────────────
async def global_rank_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db      = get_db()
    medals  = ["🥇", "🥈", "🥉"] + [f"{i}️⃣" for i in range(4, 11)]

    category = (context.args[0].lower() if context.args else "coins")
    if category not in ("coins", "xp", "kills"):
        category = "coins"

    sort_field = {"coins": "balance", "xp": "xp", "kills": "kills"}.get(category, "balance")
    title_map  = {"coins": "💰 Richest", "xp": "⚡ Top XP", "kills": "💀 Top Killers"}

    top = await db.users.find(
        {"is_banned": {"$ne": True}},
        {
            "_id": 1, sort_field: 1, "full_name": 1, "username": 1,
            "premium_emoji": 1, "is_premium": 1,
        },
    ).sort(sort_field, -1).limit(10).to_list(10)

    from utils.helpers import user_icon, mention_id
    lines = [f"🏆 <b>IOTA Global Leaderboard — {title_map[category]}</b>\n"]
    for i, user in enumerate(top):
        val   = user.get(sort_field, 0)
        name  = (user.get("full_name") or user.get("username") or "User")[:20]
        medal = medals[i] if i < len(medals) else f"{i+1}."
        icon  = user_icon(user)
        lines.append(
            f"{medal} {icon} {mention_id(user['_id'], name)} — {fmt(val)}"
        )

    lines.append(f"\n📋 Categories: /global_rank coins | xp | kills")
    await update.message.reply_html("\n".join(lines))


# ─────────────────────────────────────────────────────────────
#  /ping — Bot Latency Check
# ─────────────────────────────────────────────────────────────
async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start = time.time()
    msg   = await update.message.reply_html("🏓 Pinging...")
    latency = round((time.time() - start) * 1000, 2)
    await msg.edit_text(
        f"🏓 <b>Pong!</b>\n"
        f"⚡ Latency: <b>{latency}ms</b>\n"
        f"🤖 Iota Bot is LIVE! 🚀",
        parse_mode="HTML"
    )


# ─────────────────────────────────────────────────────────────
#  /flip — Coin Flip (standalone, separate from card game)
# ─────────────────────────────────────────────────────────────
async def coinflip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        bot_game_disabled_msg("Coin Flip (vs Iota)") + PVP_GAMES_HINT
    )
