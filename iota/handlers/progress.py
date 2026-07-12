"""
Iota Bot — Progress Commands (Achievements / Daily / Quests / Stats / Leaders)

Commands:
  /achievements  — your badge shelf (PNG medal art when possible)
  /dailyquest    — today's daily challenge + 3 quests + claim button
  /claimquest    — claim daily-challenge reward
  /stats         — your lifetime stats profile
  /gleaders      — cross-group achievement leaderboard
"""
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from utils.mongo_db import ensure_user, add_balance, get_user
from utils.helpers import mention, fmt
from utils.fonts import sc
from utils.game_art import send_game_art as _send_art, render_leaderboard as _render_leaderboard
from utils.game_ui import nav_bar

logger = logging.getLogger(__name__)


async def achievements_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    from utils.progress import achievements_list, recheck_achievements
    newly = await recheck_achievements(u.id)
    rows = await achievements_list(u.id)
    unlocked = sum(1 for r in rows if r[4])
    total = len(rows)

    lines = [f"🏅 <b>{mention(u)} — Aᴄʜɪᴇᴠᴇᴍᴇɴᴛs</b> ({unlocked}/{total})", "━" * 18]
    for key, icon, name, desc, is_unlocked, ts in rows:
        mark = "✅" if is_unlocked else "🔒"
        lines.append(f"{mark} {icon} <b>{name}</b> — {desc}")
    if newly:
        just = ", ".join(k for k in newly)
        lines.append(f"\n🎉 ɴᴇᴡ: {just}")
    lines.append("\n💡 Games khelo aur aur badges unlock karo!")

    # PNG medal board (top unlocked shown)
    board_rows = [(f"{icon} {name}", "★" if is_unlocked else "—")
                  for key, icon, name, desc, is_unlocked, ts in rows]
    try:
        await _send_art(context, update.effective_chat.id,
                       lambda: _render_leaderboard(
                           [(i + 1, f"{icon} {name}",
                             "Unlocked" if is_unlocked else "Locked")
                            for i, (key, icon, name, desc, is_unlocked, ts)
                            in enumerate(rows)]),
                        caption="🏅 ᴀᴄʜɪᴇᴠᴇᴍᴇɴᴛ ʙᴏᴀʀᴅ")
    except Exception:
        pass
    await update.message.reply_html("\n".join(lines), reply_markup=nav_bar())


async def dailyquest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    from utils.progress import get_or_init_daily, daily_progress_text, DAILY_REWARD
    doc = await get_or_init_daily(u.id)
    d = doc["daily"]
    ch_text = await daily_progress_text(u.id)

    quests = doc["quests"]["items"]
    q_lines = []
    for i, q in enumerate(quests):
        mark = "✅" if q["done"] else "⏳"
        qname = {"games2": "2 games khelo", "wager1k": "1k coins wager karo",
                 "quiz3": "3 quiz sahi karo", "bomb1": "1 bomb game khelo",
                 "daily_spin": "1 baar /wheel spin karo",
                 "roulette1": "1 roulette game khelo"}.get(q["id"], q["id"])
        q_lines.append(f"{mark} {qname}  (💰{q['reward']})")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🎁 Claim Daily", callback_data="pq_claim")
    ]]) if not d.get("claimed") else InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Claimed", callback_data="pq_none")]])
    kb = InlineKeyboardMarkup(list(kb.inline_keyboard) + [[
        InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="gh_home")]])

    await update.message.reply_html(
        f"📅 <b>Dᴀɪʟʏ Cʜᴀʟʟᴇɴɢᴇ & Qᴜᴇsᴛs</b>\n\n"
        f"🎯 <b>Daily:</b>\n{ch_text}\n\n"
        f"📋 <b>Quests:</b>\n" + "\n".join(q_lines) + "\n\n"
        f"🎁 Daily reward: {fmt(DAILY_REWARD)}",
        reply_markup=kb
    )


async def claimquest_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "pq_none":
        return
    u = q.from_user
    from utils.progress import claim_daily
    already, amount = await claim_daily(u.id)
    if already:
        await q.edit_message_text("✅ Tumne aaj claim kar liya hai!")
        return
    if amount <= 0:
        await q.edit_message_text("❌ Pehle daily challenge complete karo!")
        return
    await add_balance(u.id, amount)
    await q.edit_message_text(
        f"🎉 Daily challenge claim kiya! 💰 +{fmt(amount)}"
    )


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    from utils.progress import get_stats, recheck_achievements
    await recheck_achievements(u.id)
    s = await get_stats(u.id)
    d = await get_user(u.id)
    net = (d.get("balance", 0) or 0) + (d.get("wallet", 0) or 0) \
          + (d.get("bank", 0) or 0)
    wr = (s["games_won"] / s["games_played"] * 100) if s["games_played"] else 0
    await update.message.reply_html(
        f"📊 <b>Sᴛᴀᴛs Pʀᴏғɪʟᴇ — {mention(u)}</b>\n"
        f"━" * 18 + "\n"
        f"🎮 Games: {s['games_played']} | 🏆 Wins: {s['games_won']} "
        f"({wr:.0f}%)\n"
        f"🔥 Best streak: {s['best_streak']}\n"
        f"💰 Wagered: {fmt(s['total_wagered'])} | Top bet: {fmt(s['max_single_bet'])}\n"
        f"🧠 Quiz: {s['quiz_correct']} | 💍 Marriages: {s['marriages']}\n"
        f"🎁 Items: {s['items_owned']} | 🏅 Achievements: {s['achievements']}\n"
        f"💼 Net worth: {fmt(net)}",
        reply_markup=nav_bar()
    )


async def gleaders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.progress import global_achievement_leaders
    rows = await global_achievement_leaders(10)
    if not rows:
        await update.message.reply_html("📊 Abhi koi achievements leaderboard mein nahi!")
        return
    lines = ["🏆 <b>Gʟᴏʙᴀʟ Aᴄʜɪᴇᴠᴇᴍᴇɴᴛ Lᴇᴀᴅᴇʀʙᴏᴀʀᴅ</b>", "━" * 18]
    medals = ["🥇", "🥈", "🥉"]
    board = []
    for i, (uid, count) in enumerate(rows):
        u = await get_user(uid)
        name = u.get("full_name") or u.get("username") or f"User {uid}"
        badge = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{badge} {name} — 🏅 {count}")
        board.append((i + 1, name, f"🏅 {count}"))
    try:
        await _send_art(context, update.effective_chat.id,
                       lambda: _render_leaderboard(board),
                       caption="🏆 ɢʟᴏʙᴀʟ ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ")
    except Exception:
        pass
    await update.message.reply_html("\n".join(lines), reply_markup=nav_bar())
