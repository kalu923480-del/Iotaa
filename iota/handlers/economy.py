"""Iota Economy — Iota-style fonts, free 1d protect, 600 coin revive, auto daily"""
import logging
import random, asyncio, time
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from utils.mongo_db import *
from utils.helpers import *
from utils.safe_html import safe_html
from utils.fonts import sc, bold_sc
from utils.system_gate import economy_gate
from config import *

logger = logging.getLogger(__name__)

# ── /daily ────────────────────────────────────────────────────────────────────
@economy_gate
async def daily_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manual daily claim. Exactly 24h cooldown for everyone (86400 seconds
    — a full day, not a moment less).

    Free users: this is the ONLY way to claim — must run /daily every 24h.
    Premium users: /daily still works as a manual claim (handy if you're
    around right when it resets), but they'll ALSO get it automatically
    credited by auto_daily_job() if they don't claim manually — see that
    function below for the premium-only auto-claim logic.
    """
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)
    if d.get("is_banned"):
        await update.message.reply_html(f"🚫 {sc('You are banned!')}"); return
    now = ts(); cd = 86400  # exactly 24 hours, no more, no less
    if now - d["last_daily"] < cd:
        rem = cd-(now-d["last_daily"]); h,m = divmod(rem//60,60)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("⏰ Remind Me", callback_data=f"remind_daily_{u.id}")
        ]])
        extra = ""
        if d.get("is_premium"):
            extra = f"\n💓 {sc('Premium perk: this will auto-claim for you if you forget!')}"
        await update.message.reply_html(
            f"⏳ {mention(u)}, {sc('Already Claimed!')}\n"
            f"{sc('Come Back In')} <b>{h}h {m}m</b>{extra}",
            reply_markup=kb
        ); return
    reward  = DAILY_PREMIUM if d.get("is_premium") else DAILY_NORMAL
    xp_gain = 100 if d.get("is_premium") else 50

    # 🔴 FIX: daily_streak was only ever READ (by /streak), never WRITTEN
    # anywhere in the codebase — every user's streak was permanently
    # stuck at 0 no matter how many days in a row they claimed. Now
    # properly maintained here: continues if claimed within the 48h
    # grace window /streak already expects, resets to 1 if that window
    # was missed, and tracks the best streak ever reached.
    prev_streak = d.get("daily_streak", 0)
    hours_since_last = (now - d["last_daily"]) / 3600 if d["last_daily"] > 0 else 999
    new_streak = prev_streak + 1 if hours_since_last < 48 else 1
    max_streak = max(d.get("max_streak", 0), new_streak)

    # Milestone bonus every 7-day streak — rewards consistency without
    # needing a whole new command/system; builds on the existing streak
    # tracking (which was previously dead code — see fix note above).
    streak_bonus = 0
    milestone_note = ""
    if new_streak > 0 and new_streak % 7 == 0:
        streak_bonus = DAILY_NORMAL * 2 if not d.get("is_premium") else DAILY_PREMIUM * 2
        milestone_note = f"\n🎉 <b>{new_streak}-day streak bonus!</b> +{fmt(streak_bonus)}"

    total_reward = reward + streak_bonus
    mult = 1.0
    try:
        from utils.events import event_multiplier
        mult = float(await event_multiplier("daily", 1.0))
        if mult != 1.0:
            total_reward = int(total_reward * mult)
    except Exception:
        mult = 1.0
    await update_user(u.id, balance=d["balance"]+total_reward, last_daily=now,
                      daily_kills=0, daily_robs=0,
                      last_kill_reset=now, last_rob_reset=now,
                      daily_streak=new_streak, max_streak=max_streak)
    from utils.xp import add_xp
    xp_result = await add_xp(u.id, xp_gain, "daily")
    lv = xp_result.get("level") or xp_level(d.get("xp", 0) + xp_gain)
    streak_fire = "🔥" if new_streak >= 3 else "📅"
    level_note = ""
    if xp_result.get("leveled_up"):
        level_note = (
            f"\n🎉 {sc('Level Up!')} → <b>{lv}</b> "
            f"(+{fmt(xp_result.get('reward', 0))} bonus)"
        )
    event_note = f"\n🎁 {sc('Event bonus')} x{mult:g}!" if mult != 1.0 else ""
    await update.message.reply_html(
        f"✅ {mention(u)} {sc('Claimed Daily Reward!')}\n"
        f"💰 +{fmt(total_reward)}  |  ⚡ +{xp_gain} XP\n"
        f"{streak_fire} {sc('Streak')}: <b>{new_streak} day{'s' if new_streak != 1 else ''}</b>"
        f"{milestone_note}{level_note}{event_note}\n"
        f"🎖️ {sc('Level')}: <b>{lv}</b>  |  🏅 {rank_title(lv)}\n"
        f"💼 {sc('Balance')}: <b>{fmt(d['balance']+total_reward + int(xp_result.get('reward', 0) or 0))}</b>"
    )

# ── /weekly ────────────────────────────────────────────────────────────────────
@economy_gate
async def weekly_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Claim a bigger weekly bonus — 7 day cooldown."""
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)
    if d.get("is_banned"):
        await update.message.reply_html(f"🚫 {sc('You are banned!')}"); return
    now = ts(); cd = 604800  # 7 days
    if now - d.get("last_weekly", 0) < cd:
        rem = cd - (now - d.get("last_weekly", 0)); h, m = divmod(rem // 60, 60); dd = rem // 86400
        await update.message.reply_html(
            f"⏳ {mention(u)}, {sc('Weekly Already Claimed!')}\n"
            f"{sc('Come Back In')} <b>{dd}d {h}h {m}m</b>"
        ); return
    reward = WEEKLY_PREMIUM if d.get("is_premium") else WEEKLY_NORMAL
    xp_gain = 300 if d.get("is_premium") else 150
    try:
        from utils.events import event_multiplier
        mult = await event_multiplier("weekly", 1.0)
        if mult != 1.0:
            reward = int(reward * mult)
    except Exception:
        pass
    await update_user(u.id, balance=d["balance"] + reward, last_weekly=now)
    from utils.xp import add_xp
    xp_result = await add_xp(u.id, xp_gain, "weekly")
    lv = xp_result["level"]
    await update.message.reply_html(
        f"📅 {mention(u)} {sc('Claimed Weekly Reward!')}\n"
        f"💰 +{fmt(reward)}  |  ⚡ +{xp_gain} XP\n"
        f"🎖️ {sc('Level')}: <b>{lv}</b>  |  🏅 {rank_title(lv)}\n"
        f"💼 {sc('Balance')}: <b>{fmt(d['balance'] + reward)}</b>"
    )


# ── /monthly ──────────────────────────────────────────────────────────────────
@economy_gate
async def monthly_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Claim the biggest monthly bonus — 30 day cooldown."""
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)
    if d.get("is_banned"):
        await update.message.reply_html(f"🚫 {sc('You are banned!')}"); return
    now = ts(); cd = 2592000  # 30 days
    if now - d.get("last_monthly", 0) < cd:
        rem = cd - (now - d.get("last_monthly", 0)); dd = rem // 86400; h = (rem % 86400) // 3600
        await update.message.reply_html(
            f"⏳ {mention(u)}, {sc('Monthly Already Claimed!')}\n"
            f"{sc('Come Back In')} <b>{dd}d {h}h</b>"
        ); return
    reward = MONTHLY_PREMIUM if d.get("is_premium") else MONTHLY_NORMAL
    xp_gain = 1000 if d.get("is_premium") else 500
    try:
        from utils.events import event_multiplier
        mult = await event_multiplier("monthly", 1.0)
        if mult != 1.0:
            reward = int(reward * mult)
    except Exception:
        pass
    await update_user(u.id, balance=d["balance"] + reward, last_monthly=now)
    from utils.xp import add_xp
    xp_result = await add_xp(u.id, xp_gain, "monthly")
    lv = xp_result["level"]
    await update.message.reply_html(
        f"🗓️ {mention(u)} {sc('Claimed Monthly Reward!')}\n"
        f"💰 +{fmt(reward)}  |  ⚡ +{xp_gain} XP\n"
        f"🎖️ {sc('Level')}: <b>{lv}</b>  |  🏅 {rank_title(lv)}\n"
        f"💼 {sc('Balance')}: <b>{fmt(d['balance'] + reward)}</b>"
    )


async def daily_remind_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; u = q.from_user; await q.answer()
    if f"remind_daily_{u.id}" != q.data: return
    d = await get_user(u.id); now = ts()
    rem = 86400-(now-d["last_daily"])
    if rem <= 0:
        await q.answer("Daily is ready! /daily", show_alert=True); return
    asyncio.create_task(_remind_daily(context.bot, u.id, rem))
    await q.answer(f"⏰ Reminder in {rem//3600}h {(rem%3600)//60}m!", show_alert=True)

async def _remind_daily(bot, uid, secs):
    await asyncio.sleep(secs)
    try:
        await bot.send_message(uid,
            f"⏰ {sc('Daily Reward Is Ready!')}\n💰 /daily {sc('To Claim!')}",
            parse_mode="HTML")
    except Exception: pass

# ── AUTO DAILY JOB (background, PREMIUM ONLY) ─────────────────────────────────
async def auto_daily_job(bot):
    """
    Every 10 minutes, auto-credit the daily reward for PREMIUM users whose
    24-hour cooldown has fully elapsed — this is the premium perk: they
    never have to remember to run /daily.

    Free users are deliberately EXCLUDED here (is_premium: True filter
    below) — for them, /daily must always be claimed manually, exactly
    as intended.

    🔴 TWO BUGS FIXED FROM THE PREVIOUS VERSION:
    1. The old query used a Python dict with the key "last_daily" TWICE:
           {"last_daily": {"$lt": ...}, "last_daily": {"$gt": 0}}
       In Python, a duplicate dict key silently keeps only the LAST
       value — so the "$lt" (has 24h actually passed?) condition was
       being thrown away entirely before the query even reached MongoDB.
       This meant the job would try to auto-pay ANY user who had ever
       claimed daily once, on every single run, regardless of whether a
       day had actually passed — a major source of "why is my daily
       timing all over the place" bugs. Fixed using $and to combine both
       conditions correctly.
    2. The old query had NO "is_premium" filter at all — it was
       auto-crediting every user, free and premium alike, completely
       contradicting "free = manual only". Fixed by scoping the query to
       is_premium: True.

    Runs every 10 minutes (not hourly) so a premium user's reward lands
    close to the actual 24h mark instead of up to an hour late.
    """
    while True:
        try:
            await asyncio.sleep(600)  # check every 10 minutes
            now = ts()
            db = get_db()
            users = await db.users.find({
                "is_banned": {"$ne": True},
                "is_premium": True,
                "$and": [
                    {"last_daily": {"$lt": now - 86400}},
                    {"last_daily": {"$gt": 0}},  # exclude users who've never claimed once
                ],
            }).to_list(10000)
            for u in users:
                uid = u["_id"]
                reward = DAILY_PREMIUM
                # Keep streak logic consistent with the manual /daily
                # claim above — an auto-claim should count toward (or
                # reset) the streak exactly the same way a manual one
                # would, so premium users don't see their streak behave
                # differently just because it was auto-credited.
                prev_streak = u.get("daily_streak", 0)
                hours_since_last = (now - u["last_daily"]) / 3600 if u.get("last_daily", 0) > 0 else 999
                new_streak = prev_streak + 1 if hours_since_last < 48 else 1
                max_streak = max(u.get("max_streak", 0), new_streak)
                await db.users.update_one({"_id": uid}, {
                    "$inc": {"balance": reward},
                    "$set": {"last_daily": now, "daily_kills": 0, "daily_robs": 0,
                             "daily_streak": new_streak, "max_streak": max_streak}
                })
                from utils.xp import add_xp
                await add_xp(uid, 100, "auto_daily")
                try:
                    await bot.send_message(
                        uid,
                        f"🎁 {sc('Auto Daily Claimed!')} +{fmt(reward)} 💓\n"
                        f"🔥 {sc('Streak')}: {new_streak} day{'s' if new_streak != 1 else ''}\n"
                        f"{sc('Your premium daily reward was auto-credited!')} 💰",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
        except Exception:
            logger.exception("auto_daily_job: unexpected error in loop")

# ── /bal ──────────────────────────────────────────────────────────────────────
@economy_gate
async def bal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; u = update.effective_user
    checking_other = bool(msg.reply_to_message and msg.reply_to_message.from_user
                           and msg.reply_to_message.from_user.id != u.id)
    if checking_other:
        tu = msg.reply_to_message.from_user
        await ensure_user(tu.id, tu.username or "", tu.full_name)
        d = await get_user(tu.id)
    else:
        await ensure_user(u.id, u.username or "", u.full_name)
        d = await get_user(u.id); tu = u
    rank = await get_user_rank(d["_id"])
    lv = d.get("level") or xp_level(d["xp"])
    xp_in_lv = d["xp"] % (lv * XP_PER_LEVEL)
    now  = ts()
    # Status shows a simple alive / dead indicator (no protection
    # countdown), matching the requested compact format.
    if d["dead_until"] > now:
        status_icon = "💀"
        status = sc("Dead")
    else:
        status_icon = "🔓"
        status = sc("Alive")

    await msg.reply_html(
        f"👤 {bold_sc('Name')}: {mention(tu)}\n"
        f"💰 {bold_sc('Balance')}: {fmt(d.get('balance', 0))}\n"
        f"🏆 {bold_sc('Global Rank')}: #{rank}\n"
        f"{status_icon} {bold_sc('Status')}: {status}\n"
        f"⚔️ {bold_sc('Kills')}: {d['kills']}\n"
        f"🟤 {bold_sc(f'Level {lv}')}: {xp_in_lv}/{lv*XP_PER_LEVEL}"
    )

# ── /rob ──────────────────────────────────────────────────────────────────────
@economy_gate
async def rob_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; msg = update.effective_message
    await ensure_user(u.id, u.username or "", u.full_name)
    robber = await get_user(u.id); now = ts()
    if robber.get("is_banned"): await msg.reply_html("🚫 Banned!"); return
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_html(f"⚠️ {sc('Usage')}: /rob <amount> (reply to user)"); return
    vu = msg.reply_to_message.from_user
    if vu.id == u.id or vu.is_bot: await msg.reply_html("❌ Invalid!"); return
    if msg.reply_to_message.date:
        age = now - int(msg.reply_to_message.date.timestamp())
        if age > 30*86400:
            await msg.reply_html(f"⏳ {sc('That Message Is Too Old (30+ Days)!')}"); return
    dr_max = DAILY_ROBS_PREMIUM if robber.get("is_premium") else DAILY_ROBS_NORMAL
    if now - robber.get("last_rob_reset",0) > 86400:
        await update_user(u.id, daily_robs=0, last_rob_reset=now); robber["daily_robs"]=0
    if robber.get("daily_robs",0) >= dr_max:
        await msg.reply_html(f"⛔ {sc('Daily Rob Limit Reached!')} ({dr_max}/day)"); return
    await ensure_user(vu.id, vu.username or "", vu.full_name)
    victim = await get_user(vu.id)
    if victim["protected_until"] > now:
        # 🔴 PRIVACY FIX: no longer reveals the exact remaining time to
        # the attacker — same class of leak as the old /bal bug. Use
        # /check (premium-only, sent via DM) to see an exact countdown.
        await msg.reply_html(f"🛡️ {mention(vu)} {sc('is protected right now — try again later!')}"); return
    if victim["dead_until"] > now:
        await msg.reply_html(f"💀 {mention(vu)} {sc('Is Already Dead!')}"); return
    max_rob = ROB_MAX_PREMIUM if robber.get("is_premium") else ROB_MAX_NORMAL
    tax     = TAX_PREMIUM     if robber.get("is_premium") else TAX_NORMAL

    # 🔴 FIX: /rob <amount> used to completely ignore the amount the user
    # typed — it always rolled a random number between 100 and max_rob
    # regardless of what was requested. "/rob 50" robbing $147, or
    # "/rob 100" reporting the victim had $0 (when they may well have
    # had exactly 100), was this bug: the number typed had NO effect on
    # the outcome at all. Now the requested amount is actually honored,
    # capped by the tier's max-per-rob limit and the victim's balance —
    # exactly what a player would reasonably expect "/rob 50" to mean.
    if context.args:
        try:
            requested = int(context.args[0])
            if requested <= 0:
                await msg.reply_html("❌ Amount must be a positive number!"); return
        except ValueError:
            await msg.reply_html(f"❌ {sc('Usage')}: /rob <amount> (reply to user)"); return
        rob_amt = min(requested, max_rob, victim["balance"])
    else:
        # No amount given — fall back to the original random-roll behaviour.
        rob_amt = min(random.randint(100, max_rob), victim["balance"])

    if rob_amt <= 0:
        await msg.reply_html(
            f"👤 {mention(u)} {sc('Tried To Rob')} {mention(vu)}\n"
            f"💸 {mention(vu)} {sc('Has No Money!')}"
        ); return
    fee = int(rob_amt * tax)
    try:
        from utils.events import event_multiplier
        tax_mult = float(await event_multiplier("rob_tax", 1.0))
        fee = int(fee * tax_mult)
    except Exception:
        pass
    fee = max(0, min(fee, rob_amt))
    net = rob_amt - fee
    xp_gain = max(1, rob_amt // 1000 * XP_ROB_PER_1K)
    await update_user(vu.id, balance=victim["balance"] - rob_amt)
    await update_user(u.id, balance=robber["balance"] + net, robs=robber["robs"] + 1,
                      daily_robs=robber.get("daily_robs", 0) + 1)
    from utils.xp import add_xp
    await add_xp(u.id, xp_gain, "rob")
    tax_pct = int(round((fee / rob_amt) * 100)) if rob_amt else 0
    await msg.reply_html(
        f"👤 {mention(u)} {sc('Rᴏʙʙᴇᴅ')} {fmt(rob_amt)} {sc('Fʀᴏᴍ')} {mention(vu)}\n"
        f"💰 {sc('Gᴀɪɴᴇᴅ')} {fmt(net)}, +{xp_gain} {sc('Xᴘ Aꜰᴛᴇʀ')} {tax_pct}% {sc('Dᴇᴅᴜᴄᴛɪᴏɴ')}."
    )

    # 🆕 High-value theft alert: if a significant amount (5000+) was
    # stolen, DM the victim directly so they find out even if they're
    # not actively watching the group chat right now — this was
    # explicitly requested as a new feature. Wrapped in try/except since
    # a DM can fail (victim never started the bot in DM, blocked it,
    # etc.) and that should never break the rob command itself.
    if rob_amt >= HIGH_VALUE_THEFT_THRESHOLD:
        try:
            await context.bot.send_message(
                vu.id,
                f"🚨 <b>Big Theft Alert!</b>\n\n"
                f"{mention(u)} robbed <b>{fmt(rob_amt)}</b> coins from you!\n"
                f"💼 Your new balance: {fmt(victim['balance'] - rob_amt)}\n\n"
                f"💡 Tip: use /protect to guard your coins from future robberies.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.debug(f"rob_cmd: high-value theft DM alert failed for {vu.id}: {e}")

# ── /kill ─────────────────────────────────────────────────────────────────────
@economy_gate
async def kill_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; msg = update.effective_message
    await ensure_user(u.id, u.username or "", u.full_name)
    killer = await get_user(u.id); now = ts()
    if killer.get("is_banned"): await msg.reply_html("🚫 Banned!"); return
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_html(f"⚠️ {sc('Reply To Someone To Kill Them.')}"); return
    vu = msg.reply_to_message.from_user
    if vu.id == u.id or vu.is_bot: await msg.reply_html("❌ Invalid!"); return
    if msg.reply_to_message.date:
        if now - int(msg.reply_to_message.date.timestamp()) > 30*86400:
            await msg.reply_html(f"⏳ {sc('Message Too Old!')}"); return
    dk_max = DAILY_KILLS_PREMIUM if killer.get("is_premium") else DAILY_KILLS_NORMAL
    if now - killer.get("last_kill_reset",0) > 86400:
        await update_user(u.id, daily_kills=0, last_kill_reset=now); killer["daily_kills"]=0
    if killer.get("daily_kills",0) >= dk_max:
        await msg.reply_html(f"⛔ {sc('Daily Kill Limit!')} ({dk_max}/day)"); return
    await ensure_user(vu.id, vu.username or "", vu.full_name)
    victim = await get_user(vu.id)
    if victim["protected_until"] > now:
        # 🔴 PRIVACY FIX: no longer reveals the exact remaining time to
        # the attacker — same class of leak as the old /bal bug. Use
        # /check (premium-only, sent via DM) to see an exact countdown.
        await msg.reply_html(f"🛡️ {mention(vu)} {sc('is protected right now — try again later!')}"); return
    if victim["dead_until"] > now:
        await msg.reply_html(f"💀 {mention(vu)} {sc('Is Already Dead!')}"); return
    rng  = KILL_REWARD_PREMIUM if killer.get("is_premium") else KILL_REWARD_NORMAL
    xrng = XP_KILL_PREMIUM     if killer.get("is_premium") else XP_KILL_NORMAL
    reward  = random.randint(*rng); xp_gain = random.randint(*xrng)
    await update_user(vu.id, dead_until=now+3600)
    await update_user(u.id, balance=killer["balance"]+reward, kills=killer["kills"]+1,
                      daily_kills=killer.get("daily_kills",0)+1)
    from utils.xp import add_xp
    await add_xp(u.id, xp_gain, "kill")
    await msg.reply_html(
        f"💀 <b>{sc('Kill!')}</b>\n⚔️ {mention(u)} {sc('Killed')} {mention(vu)}\n"
        f"💰 {sc('Reward')}: <b>{fmt(reward)}</b>  |  ⚡ +{xp_gain} XP\n"
        f"🕐 {mention(vu)} {sc('Dead For')} <b>1h</b>"
    )

# ── /revive — 600 coins cost ──────────────────────────────────────────────────
@economy_gate
async def revive_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; u = update.effective_user; now = ts()
    await ensure_user(u.id, u.username or "", u.full_name)
    payer = await get_user(u.id)
    if msg.reply_to_message and msg.reply_to_message.from_user:
        tu = msg.reply_to_message.from_user
        await ensure_user(tu.id, tu.username or "", tu.full_name)
        td = await get_user(tu.id)
    else:
        tu = u; td = payer
    if td["dead_until"] <= now:
        await msg.reply_html(f"✅ {mention(tu)} {sc('Is Already Alive.')}"); return
    cost = REVIVE_COST  # 600 coins
    if payer["balance"] < cost:
        await msg.reply_html(
            f"❌ {sc('Need')} {fmt(cost)} {sc('coins to revive!')}\n"
            f"{sc('Your balance')}: {fmt(payer['balance'])}"
        ); return
    await update_user(u.id, balance=payer["balance"]-cost)
    await update_user(tu.id, dead_until=0)
    if tu.id == u.id:
        await msg.reply_html(
            f"❤️ {sc('You Revived Yourself.')} -{fmt(cost)}"
        )
    else:
        await msg.reply_html(
            f"💚 {mention(u)} {sc('Revived')} {mention(tu)}! -{fmt(cost)}"
        )

# ── /protect — 1d = 400 coins, 2d premium ────────────────────────────────────
@economy_gate
async def protect_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; args = context.args
    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id); now = ts()
    # Already protected?
    if d["protected_until"] > now:
        rem = d["protected_until"]-now
        dy=rem//86400; hr=(rem%86400)//3600; mn=(rem%3600)//60; sc2=rem%60
        await update.message.reply_html(
            f"🛡️ {sc('You Are Already Protected.')}\n"
            f"⏳ {bold_sc('Remaining')}: {dy}d {hr}h {mn}m {sc2}s"
        ); return
    dm = {"1d":1,"2d":2}
    if not args or args[0].lower() not in dm:
        await update.message.reply_html(
            f"⚠️ {sc('Usage')}: /protect 1d/2d\n"
            f"🛡️ 1d = {fmt(PROTECT_1D_COST)} coins\n"
            f"💓 2d = Premium only ({fmt(PROTECT_2D_COST)} coins)"
        ); return
    days = dm[args[0].lower()]
    if days == 2:
        if not d.get("is_premium"):
            await update.message.reply_html(
                f"💓 2d {sc('Protection Is Premium Only!')}\n/pay"
            ); return
        cost = PROTECT_2D_COST
        if d["balance"] < cost:
            await update.message.reply_html(
                f"❌ {sc('Need')} {fmt(cost)}, {sc('you have')} {fmt(d['balance'])}"
            ); return
        await update_user(u.id, balance=d["balance"]-cost)
    else:
        cost = PROTECT_1D_COST  # now 400 coins (was free)
        if cost > 0:
            if d["balance"] < cost:
                await update.message.reply_html(
                    f"❌ {sc('Need')} {fmt(cost)}, {sc('you have')} {fmt(d['balance'])}"
                ); return
            await update_user(u.id, balance=d["balance"]-cost)
    until = now + days*86400
    await update_user(u.id, protected_until=until)
    dead_note = f"\n🔄 {sc('But Your Status Is Still Dead Until Revive.')}" if d["dead_until"] > now else ""
    await update.message.reply_html(
        f"🛡️ {sc('You Are Now Protected For')} <b>{days}d</b>"
        + (f" (-{fmt(cost)})" if cost else " 🆓")
        + dead_note
    )

# ── /give ─────────────────────────────────────────────────────────────────────
@economy_gate
async def give_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name); giver = await get_user(u.id)
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_html(f"💸 {sc('Reply To A User To Give Balance.')}"); return
    if not context.args: await msg.reply_html("❌ Usage: /give [reply] &lt;amount&gt;"); return
    try: amount = int(context.args[0])
    except Exception as e:
        logger.debug(f"Suppressed error in economy.py: {e}")
        await msg.reply_html("❌ Invalid amount!"); return
    if amount <= 0: await msg.reply_html("❌ Positive only!"); return
    tax = TAX_PREMIUM if giver.get("is_premium") else TAX_NORMAL
    total = amount + int(amount*tax)
    if giver["balance"] < total:
        await msg.reply_html(f"❌ {sc('Need')} {fmt(total)} (incl. {int(tax*100)}% tax)"); return
    ru = msg.reply_to_message.from_user
    if ru.id == u.id: await msg.reply_html("😂 Can't give to yourself!"); return
    await ensure_user(ru.id, ru.username or "", ru.full_name)
    await update_user(u.id, balance=giver["balance"]-total)
    await add_balance(ru.id, amount)
    await msg.reply_html(
        f"💸 {mention(u)} {sc('Gave')} {mention(ru)} <b>{fmt(amount)}</b>\n"
        f"({sc('Tax')}: {fmt(int(amount*tax))})"
    )

# ── /toprich ──────────────────────────────────────────────────────────────────
@economy_gate
async def toprich_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await get_top_rich(10)
    medals = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    text = f"🏆 <b>{sc('Top 10 Richest Users')}:</b>\n\n"
    for i, r in enumerate(rows):
        icon = r.get("premium_emoji") or ("💓" if r.get("is_premium") else "👤")
        name = r.get("full_name") or r.get("username") or "User"
        text += f"{medals[i]} {icon} {mention_id(r['_id'],name)}: <b>{fmt(r.get('tb', r.get('balance', 0)))}</b>\n"
    text += f"\n💓 = {sc('Premium')} • 👤 = {sc('Normal')}\n✅ {sc('Upgrade To Premium')}: /pay"
    await update.message.reply_html(text)

@economy_gate
async def topkill_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await get_top_kill(10)
    medals = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    text = f"💀 <b>{sc('Top 10 Killers')}:</b>\n\n"
    for i, r in enumerate(rows):
        icon = r.get("premium_emoji") or ("💓" if r.get("is_premium") else "👤")
        name = r.get("full_name") or r.get("username") or "User"
        text += f"{medals[i]} {icon} {mention_id(r['_id'],name)}: <b>{r['kills']} kills</b>\n"
    await update.message.reply_html(text)

# ── /wallet ───────────────────────────────────────────────────────────────────
@economy_gate
async def wallet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name); d = await get_user(u.id)
    if not d.get("is_premium"):
        await update.message.reply_html(
            f"❗️ {sc('Only Premium Users Can Use Wallet.')}\n/pay"
        ); return
    args = context.args
    if not args:
        await update.message.reply_html(
            f"💼 <b>{sc('Wallet')} — {mention(u)}</b>\n\n"
            f"💰 {sc('Balance')}: {fmt(d['balance'])}\n"
            f"🏦 {sc('Wallet')}: {fmt(d.get('wallet',0))}\n\n"
            "/wallet deposit &lt;amount&gt;\n/wallet withdraw &lt;amount&gt;"
        ); return
    action = args[0].lower()
    try: amount = int(args[1]) if len(args)>1 else 0
    except Exception as e:
        logger.debug(f"Suppressed error in economy.py: {e}")
        await update.message.reply_html("❌ Invalid!"); return
    if amount <= 0: await update.message.reply_html("❌ Positive only!"); return
    if action == "deposit":
        if d["balance"] < amount: await update.message.reply_html("❌ Not enough!"); return
        await update_user(u.id, balance=d["balance"]-amount, wallet=d.get("wallet",0)+amount)
        await update.message.reply_html(f"✅ {sc('Deposited')} {fmt(amount)} {sc('To Wallet!')} 🏦")
    elif action == "withdraw":
        if d.get("wallet",0) < amount: await update.message.reply_html("❌ Not enough in wallet!"); return
        await update_user(u.id, wallet=d.get("wallet",0)-amount, balance=d["balance"]+amount)
        await update.message.reply_html(f"✅ {sc('Withdrew')} {fmt(amount)} {sc('From Wallet!')} 💰")
    else:
        await update.message.reply_html("❌ Use: /wallet deposit/withdraw &lt;amount&gt;")

# ── /rank ─────────────────────────────────────────────────────────────────────
@economy_gate
async def rank_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    tu = msg.reply_to_message.from_user if (msg.reply_to_message and msg.reply_to_message.from_user) else update.effective_user
    await ensure_user(tu.id, tu.username or "", tu.full_name)
    cr = await get_card_rank(tu.id)
    pos, total = await get_card_rank_position(tu.id)
    await msg.reply_html(
        f"🃏 <b>{sc('Card Rank')}</b>\n\n"
        f"👤 {mention(tu)}\n"
        f"👉 {sc('Wins')}: {cr['wins']} || {sc('Losses')}: {cr['losses']}\n"
        f"💰 {sc('Amount Won')}: {fmt(cr['won_amount'])}\n"
        f"💸 {sc('Amount Lost')}: {fmt(cr['lost_amount'])}\n"
        f"🔥 {sc('Streak')}: {cr['streak']} / {cr['best_streak']}\n"
        f"🎖️ {sc('Rank')}: {pos} / {total}"
    )

@economy_gate
async def pfp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Shows the target user's actual Telegram profile picture together with
    their stats. Previously this command only printed stats text and
    never fetched any picture at all — that's why no PFP ever showed up.
    """
    msg = update.effective_message
    tu = msg.reply_to_message.from_user if (msg.reply_to_message and msg.reply_to_message.from_user) else update.effective_user
    await ensure_user(tu.id, tu.username or "", tu.full_name)
    d = await get_user(tu.id); now = ts()
    dk_max = DAILY_KILLS_PREMIUM if d.get("is_premium") else DAILY_KILLS_NORMAL
    dr_max = DAILY_ROBS_PREMIUM  if d.get("is_premium") else DAILY_ROBS_NORMAL
    caption = (
        f"📊 <b>{sc('Stats')} — {mention(tu)}</b>\n\n"
        f"☠️ {sc('Daily Kills')}: {d.get('daily_kills',0)}/{dk_max}\n"
        f"🔪 {sc('Daily Robs')}: {d.get('daily_robs',0)}/{dr_max}\n"
        f"💎 {sc('Gems')}: {d.get('gems',0)}\n"
        f"💼 {sc('Wallet')}: {fmt(d.get('wallet',0))}"
    )
    try:
        from handlers.gems_store import owned_perks_line
        perks = owned_perks_line(d)
        if perks:
            caption += f"\n\n✨ {perks}"
    except Exception:
        pass
    await send_profile_photo_or_text(msg, context, tu.id, caption)

@economy_gate
async def gems_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name); d = await get_user(u.id)
    prem = d.get("is_premium", False)
    status = f"💓 {sc('Premium')}" if prem else f"⚠️ {sc('Normal')}"
    if not prem:
        await update.message.reply_html(
            f"💎 <b>{sc('Iota Gems Store')}</b>\n\n"
            f"{sc('Only Premium Users Can Use Gems.')}\n\n"
            f"💎 1 {sc('Gem')} = {fmt(GEMS_PRICE_COINS)}\n"
            f"👑 {sc('Your Status')}: {status}\n"
            f"👉 {sc('Buy Premium')}: /pay | /fpay\n"
            f"👉 {sc('Check Your ID')}: /id"
        ); return
    await update.message.reply_html(
        f"💎 <b>{sc('Gems')} — {mention(u)}</b>\n\n"
        f"💎 {sc('Gems')}: <b>{d.get('gems',0)}</b> = {fmt(d.get('gems',0)*GEMS_PRICE_COINS)}\n"
        f"👑 {sc('Your Status')}: {status}\n"
        f"/fgems — {sc('Buy with Telegram Stars')}"
    )

@economy_gate
async def claim_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; u = update.effective_user
    if chat.type == "private":
        await update.message.reply_html(f"🚫 {sc('This Command Works Only In Groups.')}"); return
    await ensure_user(u.id, u.username or "", u.full_name)
    try: count = await context.bot.get_chat_member_count(chat.id)
    except Exception as e:
        logger.debug(f"Suppressed error in economy.py: {e}")
        count = 0
    if count < 500:
        await update.message.reply_html(
            f"❌ {sc('Group Needs 500+ Members!')} {sc('Currently')}: <b>{count}</b>"
        ); return
    reward = 10000 + (count//100)*500
    await add_balance(u.id, reward)
    await update.message.reply_html(
        f"✅ {mention(u)} {sc('Claimed')} {fmt(reward)}!\n"
        f"👥 {sc('Members')}: <b>{count}</b>"
    )

async def _coupon_reply(update, text: str):
    """
    Defensive reply helper for the coupon system. Always uses
    effective_message (update.message can be None for some update
    shapes), and never raises — a reply failure is logged, not bubbled
    up to the global error handler (which would show the generic
    "Kuch gadbad" message). This keeps /coupons 100% crash-free.
    """
    try:
        m = update.effective_message
        if m:
            await m.reply_html(text)
    except Exception as e:
        logger.warning(f"coupon reply failed: {e}")


async def _redeem_global(u, code, update) -> bool:
    """
    Redeem a GLOBAL coupon (owner-created, DB-backed, with a claim limit,
    OR a built-in promo code from GLOBAL_COUPONS). Returns True if `code`
    matched a global coupon (claimed or rejected), False if it wasn't a
    global coupon at all (so the caller can try the group coupon).
    """
    # 1) Built-in promo codes (unlimited, one claim per user)
    if code in GLOBAL_COUPONS:
        if not await use_global_coupon(u.id, code):
            await _coupon_reply(update, f"❌ {sc('Already Used!')}"); return True
        reward = GLOBAL_COUPONS[code]; await add_balance(u.id, reward)
        await _coupon_reply(update,
            f"🎟️ {mention(u)} {sc('Redeemed')} <b>{code}</b>!\n💰 +{fmt(reward)}"
        ); return True
    # 2) Owner global coupon (DB-backed, with a total claim limit)
    gc = await get_global_coupon(code)
    if gc:
        limit = gc.get("limit", 0) or 0
        claimed = await global_coupon_claim_count(code)
        if limit and claimed >= limit:
            await _coupon_reply(update,
                f"❌ {sc('Coupon Limit Reached!')} ({claimed}/{limit})"); return True
        if not await use_global_coupon(u.id, code):
            await _coupon_reply(update, f"❌ {sc('Already Used!')}"); return True
        await inc_global_coupon_claimed(code)
        reward = gc.get("amount", 0); await add_balance(u.id, reward)
        shown = f"{claimed+1}/{limit}" if limit else f"{claimed+1}/∞"
        await _coupon_reply(update,
            f"🎟️ {mention(u)} {sc('Redeemed')} <b>{code}</b>!\n"
            f"💰 +{fmt(reward)}\n🎟️ {shown} {sc('Claimed')}"
        ); return True
    return False


async def _redeem_group(u, chat, code, update) -> bool:
    """
    Redeem this group's coupon (one per group, keyed by chat_id). Returns
    True if the group has an active coupon (claimed or rejected), False if
    there's no group coupon (so the caller can fall back to global).
    """
    gc = await get_group_coupon(chat.id)
    if not gc:
        return False
    # If a specific code was given, it must match this group's coupon code.
    if code and gc.get("code") and gc["code"] != code:
        return False
    limit = gc.get("limit", 0) or 0
    claimed = await group_coupon_claim_count(chat.id)
    if limit and claimed >= limit:
        await _coupon_reply(update,
            f"❌ {sc('Coupon Limit Reached!')} ({claimed}/{limit})"); return True
    if not await use_group_coupon(u.id, chat.id):
        await _coupon_reply(update, f"❌ {sc('Already Claimed!')}"); return True
    await inc_group_coupon_claimed(chat.id)
    reward = gc.get("amount", 0); await add_balance(u.id, reward)
    shown = f"{claimed+1}/{limit}" if limit else f"{claimed+1}/∞"
    await _coupon_reply(update,
        f"🎟️ {mention(u)} {sc('Claimed')}!\n💰 +{fmt(reward)}\n"
        f"🎟️ {shown} {sc('Claimed')}"
    ); return True


async def coupons_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args:
        await _coupon_reply(update,
            f"🎟 <b>{sc('Iota Coupons Guide')}</b> 🎟\n\n"
            f"🔹 /create_coupon &lt;code&gt; &lt;amount&gt; &lt;limit&gt; — "
            f"{sc('Group coupon (admin, 1 per group)')}\n"
            f"🔹 /coupon &lt;code&gt; — {sc('Claim a coupon')}\n"
            f"🔹 /del_coupon — {sc('Delete your group coupon')}\n"
            f"🔹 /status — {sc('Group coupon status')}\n\n"
            f"🥀 {sc('One coupon per group.')}\n"
            f"🌐 {sc('Owner global coupons are separate — claimable by anyone, once each, up to their limit.')}\n\n"
            f"{sc('Redeem')}: /coupon &lt;code&gt;"
        ); return
    code = args[0].lower(); u = update.effective_user; chat = update.effective_chat
    try:
        await ensure_user(u.id, u.username or "", u.full_name)
        # In a group, a group coupon is preferred when its code matches;
        # otherwise try the global coupon. In private, global only.
        if chat.type != "private" and await _redeem_group(u, chat, code, update):
            return
        if await _redeem_global(u, code, update):
            return
        await _coupon_reply(update, f"❌ {sc('Invalid Coupon Code!')}")
    except Exception as e:
        logger.warning(f"coupons_cmd failed: {e}")
        await _coupon_reply(update, f"❌ {sc('Coupon system busy, try again later.')}")


async def coupon_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; u = update.effective_user
    try:
        await ensure_user(u.id, u.username or "", u.full_name)
        args = context.args or []
        if not args:
            if chat.type == "private":
                await _coupon_reply(update, f"🚫 {sc('Use In A Group Or')}: /coupon &lt;code&gt;"); return
            await _coupon_reply(update, f"🔹 {sc('Use')}: /coupon &lt;code&gt;"); return
        code = args[0].lower()
        if chat.type != "private" and await _redeem_group(u, chat, code, update):
            return
        if await _redeem_global(u, code, update):
            return
        await _coupon_reply(update, f"❌ {sc('Invalid Coupon Code!')}")
    except Exception as e:
        logger.warning(f"coupon_cmd failed: {e}")
        await _coupon_reply(update, f"❌ {sc('Coupon system busy, try again later.')}")


async def create_coupon_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; u = update.effective_user
    if chat.type == "private":
        await _coupon_reply(update, f"🚫 {sc('Group Only!')}"); return
    from utils.helpers import is_admin
    if not await is_admin(update, context):
        await _coupon_reply(update, "❌ Admins only!"); return
    args = context.args or []
    if len(args) < 3:
        await _coupon_reply(update,
            "Usage: /create_coupon &lt;code&gt; &lt;amount&gt; &lt;limit&gt;"); return
    if await get_group_coupon(chat.id):
        await _coupon_reply(update, f"❌ {sc('Already Have A Coupon!')} /del_coupon"); return
    code = args[0].lower()
    try:
        amount = int(args[1])
    except Exception as e:
        logger.debug(f"create_coupon bad amount: {e}")
        await _coupon_reply(update, "❌ Invalid amount!"); return
    try:
        limit = int(args[2])
    except Exception as e:
        logger.debug(f"create_coupon bad limit: {e}")
        await _coupon_reply(update, "❌ Invalid limit!"); return
    if limit < 1:
        await _coupon_reply(update, "❌ Limit must be at least 1!"); return
    try:
        await set_group_coupon(chat.id, code, amount, u.id, limit=limit)
        await _coupon_reply(update,
            f"🎟️ {sc('Coupon Created!')}\n"
            f"{sc('Code')}: <b>{code}</b> | {sc('Reward')}: {fmt(amount)} "
            f"| {sc('Limit')}: {limit}\n"
            f"{sc('Claim')}: /coupon {code}"
        )
    except Exception as e:
        logger.warning(f"create_coupon_cmd failed: {e}")
        await _coupon_reply(update, f"❌ {sc('Could not create coupon, try again.')}")


async def del_coupon_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        await _coupon_reply(update, f"🚫 {sc('Group Only!')}"); return
    from utils.helpers import is_admin
    if not await is_admin(update, context):
        await _coupon_reply(update, "❌ Admins only!"); return
    if not await get_group_coupon(chat.id):
        await _coupon_reply(update, f"❌ {sc('No Coupon!')}"); return
    try:
        await delete_group_coupon(chat.id)
        await _coupon_reply(update, f"✅ {sc('Coupon Deleted!')}")
    except Exception as e:
        logger.warning(f"del_coupon_cmd failed: {e}")
        await _coupon_reply(update, f"❌ {sc('Could not delete coupon, try again.')}")


async def coupon_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    try:
        gc = await get_group_coupon(chat.id)
        if not gc:
            await _coupon_reply(update, f"❌ {sc('No Active Coupon!')}"); return
        limit = gc.get("limit", 0) or 0
        claimed = await group_coupon_claim_count(chat.id)
        shown = f"{claimed}/{limit}" if limit else f"{claimed}/∞"
        await _coupon_reply(update,
            f"🎟️ {sc('Group Coupon')}\n"
            f"{sc('Code')}: <b>{gc.get('code', '')}</b> | "
            f"{sc('Reward')}: {fmt(gc.get('amount', 0))} | "
            f"{sc('Claimed')}: {shown}"
        )
    except Exception as e:
        logger.warning(f"coupon_status_cmd failed: {e}")
        await _coupon_reply(update, f"❌ {sc('Could not read coupon status.')}")

async def economy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📖 View Economy Guide", callback_data="eco_guide")]])
    await update.message.reply_html(
        f"🎮 {sc('Click The Button Below To Know About Iota Economy Game.')}",
        reply_markup=kb
    )

async def eco_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "eco_guide":
        await q.edit_message_text(
            f"💰 <b>{sc('Economy Guide')}</b>\n\n"
            f"👤 {sc('Normal')}: {sc('Daily')} {fmt(DAILY_NORMAL)} | {sc('Rob')} {fmt(ROB_MAX_NORMAL)} | {sc('Tax')} {int(TAX_NORMAL*100)}%\n"
            f"💓 {sc('Premium')}: {sc('Daily')} {fmt(DAILY_PREMIUM)} | {sc('Rob')} {fmt(ROB_MAX_PREMIUM)} | {sc('Tax')} {int(TAX_PREMIUM*100)}%\n\n"
            f"💀 {sc('Revive Cost')}: {fmt(REVIVE_COST)} coins\n"
            f"🛡️ 1d {sc('Protection')}: <b>FREE</b>\n"
            f"🛡️ 2d {sc('Protection')}: {fmt(PROTECT_2D_COST)} (Premium)\n"
            f"💎 1 {sc('Gem')} = {fmt(GEMS_PRICE_COINS)}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="eco_back")]])
        )
    elif q.data == "eco_back":
        await q.edit_message_text(
            f"🎮 {sc('Click The Button Below To Know About Iota Economy Game.')}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📖 View Economy Guide", callback_data="eco_guide")]])
        )

# ── Group economy stubs ───────────────────────────────────────────────────────
@economy_gate
async def gbal_cmd(update, context):
    u=update.effective_user; chat=update.effective_chat
    if chat.type=="private": await update.message.reply_html(f"🚫 {sc('Group Only!')}"); return
    await ensure_user(u.id); g=await get_guser(u.id,chat.id)
    await update.message.reply_html(
        f"🏘️ <b>{sc('Group Balance')} — {mention(u)}</b>\n"
        f"💰 {fmt(g['balance'])} | 💀 {g['kills']} | 🔫 {g['robs']}"
    )

@economy_gate
async def gkill_cmd(update, context):
    u=update.effective_user; msg=update.effective_message; chat=update.effective_chat; now=ts()
    if chat.type=="private": await msg.reply_html(f"🚫 {sc('Group Only!')}"); return
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_html(f"❌ {sc('Reply To Someone!')}"); return
    vu=msg.reply_to_message.from_user
    if vu.id==u.id or vu.is_bot: await msg.reply_html("❌ Invalid!"); return
    await ensure_user(u.id); await ensure_user(vu.id)
    gv=await get_guser(vu.id,chat.id); gk=await get_guser(u.id,chat.id)
    if gv["dead_until"]>now: await msg.reply_html(f"💀 {mention(vu)} {sc('Already Dead!')}"); return
    if gv["protected_until"]>now: await msg.reply_html(f"🛡️ {mention(vu)} {sc('Protected!')}"); return
    r=random.randint(100,300)
    await update_guser(vu.id,chat.id,dead_until=now+3600)
    await update_guser(u.id,chat.id,balance=gk["balance"]+r,kills=gk["kills"]+1)
    await msg.reply_html(f"💀 {mention(u)} {sc('Killed')} {mention(vu)}!\n💰 +{fmt(r)}")

@economy_gate
async def grob_cmd(update, context):
    u=update.effective_user; msg=update.effective_message; chat=update.effective_chat; now=ts()
    if chat.type=="private": await msg.reply_html(f"🚫 {sc('Group Only!')}"); return
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_html(f"❌ {sc('Reply To Someone!')}"); return
    vu=msg.reply_to_message.from_user
    if vu.id==u.id or vu.is_bot: await msg.reply_html("❌ Invalid!"); return
    await ensure_user(u.id); await ensure_user(vu.id)
    gv=await get_guser(vu.id,chat.id); gr=await get_guser(u.id,chat.id)
    if gv["protected_until"]>now: await msg.reply_html(f"🛡️ {sc('Protected!')}"); return
    if gv["dead_until"]>now: await msg.reply_html(f"💀 {sc('Dead!')}"); return
    amt=min(random.randint(100,5000),gv["balance"])
    if amt<=0: await msg.reply_html(f"💸 {sc('No Coins!')}"); return
    await update_guser(vu.id,chat.id,balance=gv["balance"]-amt)
    await update_guser(u.id,chat.id,balance=gr["balance"]+amt,robs=gr["robs"]+1)
    await msg.reply_html(f"🔫 {mention(u)} {sc('Robbed')} {mention(vu)}!\n💰 +{fmt(amt)}")

@economy_gate
async def grevive_cmd(update, context):
    u=update.effective_user; msg=update.effective_message; chat=update.effective_chat; now=ts()
    if chat.type=="private": await msg.reply_html(f"🚫 {sc('Group Only!')}"); return
    tu=msg.reply_to_message.from_user if (msg.reply_to_message and msg.reply_to_message.from_user) else u
    await ensure_user(tu.id); g=await get_guser(tu.id,chat.id)
    if g["dead_until"]<=now: await msg.reply_html(f"❓ {sc('Not Dead!')}"); return
    await update_guser(tu.id,chat.id,dead_until=0)
    await msg.reply_html(f"💚 {mention(u)} {sc('Revived')} {mention(tu)}!")

@economy_gate
async def gprotect_cmd(update, context):
    u=update.effective_user; chat=update.effective_chat; now=ts()
    if chat.type=="private": await update.message.reply_html(f"🚫 {sc('Group Only!')}"); return
    await ensure_user(u.id); g=await get_guser(u.id,chat.id)
    if g["balance"]<400: await update.message.reply_html(f"❌ {sc('Need 400 group coins!')}"); return
    await update_guser(u.id,chat.id,balance=g["balance"]-400,protected_until=now+86400)
    await update.message.reply_html(f"🛡️ {mention(u)} {sc('Protected In Group For 1 Day!')}")

@economy_gate
async def gcheck_cmd(update, context):
    msg=update.effective_message; chat=update.effective_chat; now=ts()
    tu=msg.reply_to_message.from_user if (msg.reply_to_message and msg.reply_to_message.from_user) else update.effective_user
    await ensure_user(tu.id); g=await get_guser(tu.id,chat.id)
    if g["protected_until"]>now:
        rem=g["protected_until"]-now
        await msg.reply_html(f"🛡️ {mention(tu)} {sc('Protected')} {rem//3600}h {(rem%3600)//60}m")
    else:
        await msg.reply_html(f"❌ {sc('No Group Protection')}")

@economy_gate
async def granks_cmd(update, context):
    chat=update.effective_chat
    if chat.type=="private": await update.message.reply_html(f"🚫 {sc('Group Only!')}"); return
    rows=await get_granks(chat.id)
    if not rows: await update.message.reply_html(f"📊 {sc('No Data Yet!')}"); return
    # Group-economy docs don't store names, so resolve them from the
    # users collection in a single batched lookup (same fix as the
    # "Unknown" names in /global_rank).
    ids=[r["user_id"] for r in rows]
    names={}
    if ids:
        cur=get_db().users.find({"_id":{"$in":ids}},{"full_name":1,"username":1})
        async for u in cur:
            names[u["_id"]]=u.get("full_name") or u.get("username") or "User"
    medals=["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    text=f"🏆 <b>{sc('Group Ranks')} — {safe_html(chat.title)}</b>\n\n"
    for i,r in enumerate(rows):
        name=names.get(r["user_id"],"User")
        text+=f"{medals[i]} {mention_id(r['user_id'],name)} — {fmt(r['balance'])}\n"
    await update.message.reply_html(text)

async def auto_delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg=update.effective_message
    if msg and update.effective_chat.type!="private":
        asyncio.create_task(delete_later(context.bot,msg.chat_id,msg.message_id,300))
