"""
Iota Group Extras — NEW unique group-admin systems.
All commands use resolve_target_chat for DM support where settings-based.
Admin checks via resolve_target_chat(need_admin=True) for moderation.
"""
import re, json, asyncio, time, random, logging
from datetime import datetime, timezone, timedelta
from telegram import Update, ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import TelegramError
from utils.mongo_db import (
    get_db, ensure_group_settings, get_group_settings,
    ensure_user, get_user,
    get_granks,
    log_mod_action, list_mod_actions,
    get_welcome_settings, set_welcome_settings,
)
from utils.helpers import (
    mention, ts, is_admin, resolve_target, resolve_target_chat,
    mention_id, parse_duration,
)
from utils.safe_html import safe_html

logger = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _db():
    return get_db()


def in_night_window(start: str, end: str, now_min: int | None = None) -> bool:
    """Pure helper: is `now_min` (minutes since midnight) inside [start, end)?
    Supports overnight windows (e.g. 23:00 → 07:00)."""
    if now_min is None:
        now = datetime.now(timezone(timedelta(hours=5, minutes=30)))  # IST
        now_min = now.hour * 60 + now.minute
    try:
        sh, sm = map(int, start.split(":"))
        eh, em = map(int, end.split(":"))
        start_min = sh * 60 + sm
        end_min = eh * 60 + em
    except Exception:
        return False
    if start_min > end_min:
        return now_min >= start_min or now_min < end_min
    return start_min <= now_min < end_min


async def _collect_candidate_uids(cid: int) -> set:
    """Async scan of known users for this chat (gusers / warnings)."""
    candidates: set = set()
    db = _db()
    try:
        async for doc in db.group_economy.find({"chat_id": cid}, {"user_id": 1}).limit(300):
            if doc.get("user_id"):
                candidates.add(int(doc["user_id"]))
    except Exception:
        pass
    try:
        async for doc in db.warnings.find({"chat_id": cid}, {"user_id": 1}).limit(300):
            if doc.get("user_id"):
                candidates.add(int(doc["user_id"]))
    except Exception:
        pass
    try:
        async for doc in db.mod_actions.find(
            {"chat_id": cid}, {"target_id": 1}
        ).sort("created_at", -1).limit(100):
            if doc.get("target_id"):
                candidates.add(int(doc["target_id"]))
    except Exception:
        pass
    return candidates


async def _is_admin_here(update, context, chat_id=None):
    from config import OWNER_ID
    uid = update.effective_user.id
    if int(uid) == int(OWNER_ID):
        return True
    chat = update.effective_chat
    cid = chat_id or chat.id
    try:
        m = await context.bot.get_chat_member(cid, uid)
        return m.status in ("administrator", "creator")
    except Exception:
        return False


async def _reply(update, text: str, parse_mode: str = "HTML", **kw):
    msg = update.effective_message
    if not msg:
        return
    try:
        await msg.reply_html(text, **kw) if parse_mode == "HTML" else await msg.reply_text(text, parse_mode=parse_mode, **kw)
    except Exception:
        try:
            await msg.reply_text(text)
        except Exception:
            pass


async def _ban(bot, chat_id, user_id):
    try:
        await bot.ban_chat_member(chat_id, user_id)
    except TelegramError:
        pass


async def _unban(bot, chat_id, user_id):
    try:
        await bot.unban_chat_member(chat_id, user_id)
    except TelegramError:
        pass


async def _kick(bot, chat_id, user_id):
    try:
        await bot.ban_chat_member(chat_id, user_id)
        await asyncio.sleep(1)
        await bot.unban_chat_member(chat_id, user_id)
    except TelegramError:
        pass


async def _mute(bot, chat_id, user_id, seconds: int = 300):
    try:
        until = datetime.fromtimestamp(ts() + seconds, tz=timezone.utc)
        await bot.restrict_chat_member(
            chat_id, user_id,
            ChatPermissions(can_send_messages=False),
            until_date=until,
        )
    except TelegramError:
        pass


async def _log(bot, log_channel, text):
    if not log_channel:
        return
    try:
        await bot.send_message(log_channel, text, parse_mode="HTML")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  A. UTILITY COMMANDS
# ─────────────────────────────────────────────────────────────────────────────

# ── /zombies ──────────────────────────────────────────────────────────────────

async def zombies_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        await _reply(update, "🚫 Use this in a group.")
        return
    if not await _is_admin_here(update, context):
        await _reply(update, "❌ Admins only.")
        return

    cid = chat.id
    bot = context.bot
    me = await bot.get_me()
    try:
        cm = await bot.get_chat_member(cid, me.id)
        can_ban = getattr(cm, "can_restrict_members", False)
    except Exception:
        can_ban = False

    args = context.args
    if args and args[0].lower() == "clean":
        if not can_ban:
            await _reply(update, "❌ I need ban rights to clean zombies.")
            return
        dry_run = False
        if len(args) > 1 and args[1].lower() == "dry":
            dry_run = True

        candidates = await _collect_candidate_uids(cid)
        zombies = []
        for uid in list(candidates)[:200]:
            try:
                m = await bot.get_chat_member(cid, uid)
                if m.status in ("left", "kicked"):
                    continue
                user = m.user
                if getattr(user, "is_deleted", False):
                    zombies.append(uid)
                else:
                    name = (user.first_name or "").strip()
                    uname = (user.username or "").strip()
                    if not name and not uname:
                        zombies.append(uid)
            except Exception:
                pass
            if len(zombies) >= 20:
                break

        if not zombies:
            await _reply(update, "✅ No zombies to clean.")
            return
        if dry_run:
            await _reply(update, f"🧪 Dry run: {len(zombies)} zombies would be kicked.")
            return

        kicked = 0
        for uid in zombies:
            try:
                await bot.ban_chat_member(cid, uid)
                await asyncio.sleep(0.4)
                await bot.unban_chat_member(cid, uid)
                kicked += 1
            except Exception:
                pass

        await log_mod_action(cid, update.effective_user.id, "zombie_clean", 0, f"kicked {kicked} zombies")
        await _reply(update, f"🧹 Cleaned <b>{kicked}</b> zombies from the group.")
        return

    # ── list mode ──────────────────────────────────────────────────────────────
    try:
        total = await bot.get_chat_member_count(cid)
    except Exception:
        total = "?"

    candidates = await _collect_candidate_uids(cid)
    zombies = []
    checked = 0
    for uid in list(candidates)[:200]:
        checked += 1
        try:
            m = await bot.get_chat_member(cid, uid)
            status = m.status
            if status in ("left", "kicked"):
                continue
            user = m.user
            if getattr(user, "is_deleted", False):
                zombies.append((uid, getattr(user, "first_name", "Deleted") or "Deleted Account", status))
            else:
                name = (user.first_name or "").strip()
                uname = (user.username or "").strip()
                if not name and not uname:
                    zombies.append((uid, "Unknown", status))
        except TelegramError:
            pass
        except Exception:
            pass
        if len(zombies) >= 20:
            break

    if not zombies:
        await _reply(update, f"✅ No zombies found (scanned {checked} users, total members: {total}).")
        return

    lines = [f"🧟 <b>Zombies Found</b> — checked {checked} users, total members: {total}\n"]
    for uid, name, st in zombies:
        lines.append(f"• <code>{uid}</code> — {safe_html(name)} [{st}]")
    lines.append(
        f"\n💡 Use <code>/zombies clean</code> to kick (max 20)."
        if can_ban else "\n⚠️ I need ban rights to clean zombies."
    )
    await _reply(update, "\n".join(lines))


# ── /staff ─────────────────────────────────────────────────────────────────────

async def staff_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        await _reply(update, "🚫 Use this in a group.")
        return

    cid = chat.id
    bot = context.bot
    try:
        admins = await bot.get_chat_administrators(cid)
    except TelegramError as e:
        await _reply(update, f"❌ Could not fetch admins: {e}")
        return

    owner_list = []
    admin_list = []
    for a in admins:
        u = a.user
        if u.is_bot:
            continue
        name = safe_html(u.full_name or u.first_name or "User")
        tag = f"@{u.username}" if u.username else str(u.id)
        entry = f"• {mention(u)} ({tag})"
        if a.status == "creator":
            owner_list.append(entry)
        else:
            admin_list.append(entry)

    lines = [f"👑 <b>Staff — {safe_html(chat.title or f'Group {cid}')}</b>\n"]
    if owner_list:
        lines.append("<b>Owner:</b>")
        lines.extend(owner_list)
    if admin_list:
        lines.append(f"\n<b>Admins ({len(admin_list)}):</b>")
        lines.extend(admin_list)
    if not owner_list and not admin_list:
        lines.append("No non-bot admins found.")
    await _reply(update, "\n".join(lines))


# ── /joindate ─────────────────────────────────────────────────────────────────

async def joindate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        await _reply(update, "🚫 Use this in a group.")
        return

    target_id, target_mention, _ = await resolve_target(update, context, [])
    if target_id is None:
        await _reply(update, "❌ Reply to a user or pass a user ID.")
        return

    cid = chat.id
    bot = context.bot
    try:
        m = await bot.get_chat_member(cid, target_id)
        user = m.user
        jd = getattr(m, "joined_date", None)
        status = m.status
        lines = [
            f"👤 <b>{mention_id(target_id, safe_html(user.full_name or user.first_name or 'User'))}</b>\n"
            f"Status: <b>{status}</b>",
        ]
        if jd:
            dt = datetime.fromtimestamp(jd, tz=timezone.utc)
            lines.append(f"Joined: <b>{dt.strftime('%Y-%m-%d %H:%M UTC')}</b>")
        else:
            lines.append("Joined date: <i>not available</i>")
        await _reply(update, "\n".join(lines))
    except TelegramError as e:
        await _reply(update, f"❌ Could not fetch member: {e}")


# ── /banlist ──────────────────────────────────────────────────────────────────

async def banlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await _reply(update, err)
        return

    limit = 20
    if context.args:
        try:
            limit = max(1, min(int(context.args[0]), 50))
        except Exception:
            pass

    rows = await list_mod_actions(cid, limit=limit)
    rows = [r for r in rows if r.get("action") in ("ban", "tban", "kick", "zombie_clean", "ghostban")]
    if not rows:
        await _reply(update, "📭 No recent ban actions in this group.")
        return

    lines = [f"🚫 <b>Ban List — {safe_html(title)}</b> (last {len(rows)})\n"]
    for r in rows:
        ts_val = r.get("created_at", 0)
        dt = datetime.fromtimestamp(ts_val, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if ts_val else "?"
        reason = safe_html(r.get("reason", ""))
        tid = r.get("target_id", 0)
        actor = r.get("actor_id", 0)
        lines.append(f"• [{r['action']}] target={tid} by {actor} | {dt} | {reason}")
    await _reply(update, "\n".join(lines))


# ── /kickme ───────────────────────────────────────────────────────────────────

async def kickme_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        await _reply(update, "🚫 Use this in a group.")
        return
    uid = update.effective_user.id
    cid = chat.id
    try:
        await _ban(context.bot, cid, uid)
        await asyncio.sleep(1)
        await _unban(context.bot, cid, uid)
        await _reply(update, "👋 You kicked yourself. Rejoin anytime!")
    except TelegramError as e:
        await _reply(update, f"❌ Could not kick: {e}")


# ── /tmute ────────────────────────────────────────────────────────────────────

async def tmute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        await _reply(update, "🚫 Use this in a group.")
        return
    if not await _is_admin_here(update, context):
        await _reply(update, "❌ Admins only.")
        return

    target_id, target_mention, args = await resolve_target(update, context, context.args)
    if target_id is None:
        await _reply(update, "❌ Reply to a user or pass a user ID.")
        return

    duration_s = 0
    reason = ""
    if args:
        duration_s = parse_duration(args[0]) if args else 0
        reason = " ".join(args[1:]) if len(args) > 1 else ""

    if duration_s <= 0:
        await _reply(update, "❌ Provide a duration. Usage: /tmute <1s/5m/1h/2d> [reason]")
        return

    cid = chat.id
    try:
        await _mute(context.bot, cid, target_id, duration_s)
    except Exception as e:
        await _reply(update, f"❌ Mute failed: {e}")
        return

    until = datetime.fromtimestamp(ts() + duration_s, tz=timezone.utc).strftime("%H:%M UTC")
    await log_mod_action(cid, update.effective_user.id, "tmute", target_id, reason)
    try:
        gs = await get_group_settings(cid)
        await _log(context.bot, gs.get("log_channel", 0),
                   f"🔇 TMute | {target_id} | by {update.effective_user.id} | until {until} | {safe_html(reason)}")
    except Exception:
        pass
    await _reply(update, f"🔇 {target_mention} muted for <b>{args[0]}</b> until {until}.\nReason: {safe_html(reason) if reason else '—'}")


# ── /tban ─────────────────────────────────────────────────────────────────────

async def tban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        await _reply(update, "🚫 Use this in a group.")
        return
    if not await _is_admin_here(update, context):
        await _reply(update, "❌ Admins only.")
        return

    target_id, target_mention, args = await resolve_target(update, context, context.args)
    if target_id is None:
        await _reply(update, "❌ Reply to a user or pass a user ID.")
        return

    duration_s = 0
    reason = ""
    if args:
        duration_s = parse_duration(args[0]) if args else 0
        reason = " ".join(args[1:]) if len(args) > 1 else ""

    if duration_s <= 0:
        await _reply(update, "❌ Provide a duration. Usage: /tban <1s/5m/1h/2d> [reason]")
        return

    cid = chat.id
    try:
        await _ban(context.bot, cid, target_id)
        context.job_queue.run_once(
            lambda c: asyncio.ensure_future(_unban(c.bot, cid, target_id)),
            duration_s,
        )
    except Exception as e:
        await _reply(update, f"❌ Ban failed: {e}")
        return

    await log_mod_action(cid, update.effective_user.id, "tban", target_id, reason, {"duration": duration_s})
    try:
        gs = await get_group_settings(cid)
        await _log(context.bot, gs.get("log_channel", 0),
                   f"⛔ TBan | {target_id} | by {update.effective_user.id} | {args[0]} | {safe_html(reason)}")
    except Exception:
        pass
    await _reply(update, f"⛔ {target_mention} temp-banned for <b>{args[0]}</b>.\nReason: {safe_html(reason) if reason else '—'}")


# ── /ghostban ─────────────────────────────────────────────────────────────────

async def ghostban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        await _reply(update, "🚫 Use this in a group.")
        return
    if not await _is_admin_here(update, context):
        await _reply(update, "❌ Admins only.")
        return

    target_id, target_mention, args = await resolve_target(update, context, context.args)
    if target_id is None:
        await _reply(update, "❌ Reply to a user or pass a user ID.")
        return

    reason = " ".join(args) if args else ""
    cid = chat.id
    try:
        await _ban(context.bot, cid, target_id)
    except Exception as e:
        await _reply(update, f"❌ Ban failed: {e}")
        return

    await log_mod_action(cid, update.effective_user.id, "ghostban", target_id, reason)
    try:
        gs = await get_group_settings(cid)
        await _log(context.bot, gs.get("log_channel", 0),
                   f"👻 Ghostban | {target_id} | by {update.effective_user.id} | {safe_html(reason)}")
    except Exception:
        pass
    await _reply(update, f"👻 {target_mention} has been silently banned.")


# ── /botlist ──────────────────────────────────────────────────────────────────

async def botlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        await _reply(update, "🚫 Use this in a group.")
        return

    cid = chat.id
    bot = context.bot
    try:
        admins = await bot.get_chat_administrators(cid)
    except TelegramError as e:
        await _reply(update, f"❌ Could not fetch admins: {e}")
        return

    bot_admins = []
    known_bots = set()
    for a in admins:
        u = a.user
        if u.is_bot:
            bot_admins.append(f"• {safe_html(u.first_name or u.username or 'Bot')} (@{u.username or u.id}) [admin]")
            known_bots.add(u.id)

    # Also scan group_economy for bot entries
    try:
        for doc in _db().group_economy.find({"chat_id": cid}).limit(500):
            uid = doc.get("user_id")
            if uid in known_bots:
                continue
            try:
                u = await bot.get_chat_member(cid, uid)
                if u.user.is_bot:
                    bot_admins.append(f"• {safe_html(u.user.first_name or u.user.username or 'Bot')} (@{u.user.username or uid})")
                    known_bots.add(uid)
            except Exception:
                pass
    except Exception:
        pass

    lines = [f"🤖 <b>Bot List — {safe_html(chat.title or f'Group {cid}')}</b>\n"]
    if bot_admins:
        lines.append(f"Found <b>{len(bot_admins)}</b> bot(s):")
        lines.extend(bot_admins)
    else:
        lines.append("No bots detected (only admins are visible without full member list).")
    lines.append("\n💡 Promote me fully for best results.")
    await _reply(update, "\n".join(lines))


# ── /kickbots (NEW name, not cleanbots) ───────────────────────────────────────

async def kickbots_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        await _reply(update, "🚫 Use this in a group.")
        return
    if not await _is_admin_here(update, context):
        await _reply(update, "❌ Admins only.")
        return

    cid = chat.id
    bot = context.bot
    me = await bot.get_me()
    try:
        cm = await bot.get_chat_member(cid, me.id)
        can_ban = getattr(cm, "can_restrict_members", False)
    except Exception:
        can_ban = False

    if not can_ban:
        await _reply(update, "❌ I need ban rights to kick bots.")
        return

    try:
        admins = await bot.get_chat_administrators(cid)
        admin_ids = {a.user.id for a in admins}
    except TelegramError as e:
        await _reply(update, f"❌ Could not fetch admins: {e}")
        return

    targets = set()
    for a in admins:
        u = a.user
        if u.is_bot and u.id != me.id:
            targets.add(u.id)

    try:
        for doc in _db().group_economy.find({"chat_id": cid}).limit(500):
            uid = doc.get("user_id")
            if uid in admin_ids or uid == me.id:
                continue
            try:
                m = await bot.get_chat_member(cid, uid)
                if m.user.is_bot:
                    targets.add(uid)
            except Exception:
                pass
    except Exception:
        pass

    if not targets:
        await _reply(update, "✅ No non-admin bots found to kick.")
        return

    kicked = 0
    for uid in list(targets)[:20]:
        try:
            await bot.ban_chat_member(cid, uid)
            await asyncio.sleep(0.4)
            await bot.unban_chat_member(cid, uid)
            kicked += 1
        except Exception:
            pass

    await log_mod_action(cid, update.effective_user.id, "kickbots", 0, f"kicked {kicked} bots")
    await _reply(update, f"🤖 Kicked <b>{kicked}</b> non-admin bot(s).")


# ─────────────────────────────────────────────────────────────────────────────
#  B. GROUP LOCKDOWN (silence / unsilence)
# ─────────────────────────────────────────────────────────────────────────────

async def silence_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        await _reply(update, "🚫 Use this in a group.")
        return
    if not await _is_admin_here(update, context):
        await _reply(update, "❌ Admins only.")
        return

    cid = chat.id
    bot = context.bot
    me = await bot.get_me()
    try:
        cm = await bot.get_chat_member(cid, me.id)
        can_restrict = getattr(cm, "can_restrict_members", False)
    except Exception:
        can_restrict = False

    if not can_restrict:
        await _reply(update, "❌ I need restrict members right to silence the chat.")
        return

    try:
        # Prefer modern PTB permission fields; fall back if older API rejects.
        try:
            perms = ChatPermissions(
                can_send_messages=False,
                can_send_audios=False,
                can_send_documents=False,
                can_send_photos=False,
                can_send_videos=False,
                can_send_video_notes=False,
                can_send_voice_notes=False,
                can_send_polls=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False,
                can_invite_users=True,
                can_pin_messages=False,
            )
        except TypeError:
            perms = ChatPermissions(can_send_messages=False)
        await bot.set_chat_permissions(cid, perms)
    except TelegramError as e:
        await _reply(update, f"❌ Silence failed: {safe_html(str(e))}")
        return

    await ensure_group_settings(cid)
    await _db().group_settings.update_one({"_id": cid}, {"$set": {"silence_mode": True}})
    await log_mod_action(cid, update.effective_user.id, "silence", 0, "chat muted")
    await _reply(update, "🔇 <b>Chat silenced.</b> Only admins can send messages now.")


async def unsilence_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        await _reply(update, "🚫 Use this in a group.")
        return
    if not await _is_admin_here(update, context):
        await _reply(update, "❌ Admins only.")
        return

    cid = chat.id
    try:
        try:
            perms = ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
                can_invite_users=True,
                can_pin_messages=True,
            )
        except TypeError:
            perms = ChatPermissions(can_send_messages=True)
        await context.bot.set_chat_permissions(cid, perms)
    except TelegramError as e:
        await _reply(update, f"❌ Unsilence failed: {safe_html(str(e))}")
        return

    await _db().group_settings.update_one({"_id": cid}, {"$set": {"silence_mode": False}})
    await log_mod_action(cid, update.effective_user.id, "unsilence", 0, "chat unmuted")
    await _reply(update, "🔊 <b>Chat unsilenced.</b> Members can talk again.")


# ─────────────────────────────────────────────────────────────────────────────
#  C. NIGHT MODE (DM-capable settings)
# ─────────────────────────────────────────────────────────────────────────────

async def nightmode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await _reply(update, err)
        return

    gs = await ensure_group_settings(cid)
    args = context.args

    if not args or args[0].lower() == "status":
        en = gs.get("nightmode_enabled", False)
        st = gs.get("nightmode_start", "23:00")
        ed = gs.get("nightmode_end", "07:00")
        state = "🟢 ON" if en else "🔴 OFF"
        await _reply(update,
            f"🌙 <b>Night Mode — {safe_html(title)}</b>\n\n"
            f"Status: {state}\n"
            f"Window: <b>{st}</b> → <b>{ed}</b> (IST)\n\n"
            f"Usage: /nightmode on <start> <end>\n"
            f"       /nightmode off"
        )
        return

    sub = args[0].lower()
    if sub == "off":
        await _db().group_settings.update_one({"_id": cid}, {"$set": {"nightmode_enabled": False}})
        await _reply(update, "🌙 Night mode <b>off</b>.")
        return

    if sub == "on":
        st = args[1] if len(args) > 1 else "23:00"
        ed = args[2] if len(args) > 2 else "07:00"
        if not re.match(r'^\d{1,2}:\d{2}$', st) or not re.match(r'^\d{1,2}:\d{2}$', ed):
            await _reply(update, "❌ Use HH:MM format. Example: /nightmode on 23:00 07:00")
            return
        await _db().group_settings.update_one(
            {"_id": cid},
            {"$set": {"nightmode_enabled": True, "nightmode_start": st, "nightmode_end": ed}},
        )
        await _reply(update, f"🌙 Night mode <b>on</b> ({st} → {ed}). Media/link/sticker locked for non-admins during this window.")
        return

    await _reply(update, "❌ Usage: /nightmode on <HH:MM> <HH:MM> | off | status")


# ─────────────────────────────────────────────────────────────────────────────
#  D. FORCE SUBSCRIBE (DM-capable settings)
# ─────────────────────────────────────────────────────────────────────────────

async def forcesub_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await _reply(update, err)
        return

    gs = await ensure_group_settings(cid)
    args = context.args

    if not args or args[0].lower() == "status":
        ch = gs.get("forcesub_channel", "")
        state = f"<code>{safe_html(ch)}</code>" if ch else "<i>Not set</i>"
        await _reply(update,
            f"📢 <b>Force Sub — {safe_html(title)}</b>\n\n"
            f"Channel: {state}\n\n"
            f"Usage: /forcesub @channel\n"
            f"       /forcesub off"
        )
        return

    sub = args[0].lower()
    if sub == "off":
        await _db().group_settings.update_one({"_id": cid}, {"$set": {"forcesub_channel": ""}})
        await _reply(update, "📢 Force subscribe <b>off</b>.")
        return

    channel = args[0].lstrip("@")
    if not channel:
        await _reply(update, "❌ Provide a channel username or ID. Example: /forcesub @mychannel")
        return

    await _db().group_settings.update_one(
        {"_id": cid},
        {"$set": {"forcesub_channel": channel}},
    )
    await _reply(update, f"📢 Force subscribe set to <code>@{safe_html(channel)}</code>.\nNon-subscribers will be warned on message.")


# ─────────────────────────────────────────────────────────────────────────────
#  E. WELCOME BUTTONS
# ─────────────────────────────────────────────────────────────────────────────

async def welcomebtn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await _reply(update, err)
        return

    args = context.args
    if not args or args[0].lower() == "list":
        ws = await get_welcome_settings(cid)
        buttons = ws.get("welcome_buttons", [])
        if not buttons:
            await _reply(update, "📋 No welcome buttons set.\nUsage: /welcomebtn add Rules|https://t.me/...")
            return
        lines = ["🔘 <b>Welcome Buttons:</b>\n"]
        for i, b in enumerate(buttons, 1):
            lines.append(f"{i}. {safe_html(b.get('text',''))} — {safe_html(b.get('url',''))}")
        await _reply(update, "\n".join(lines))
        return

    sub = args[0].lower()
    if sub == "clear":
        await set_welcome_settings(cid, welcome_buttons=[])
        await _reply(update, "🗑️ Welcome buttons cleared.")
        return

    if sub == "add":
        raw = " ".join(args[1:])
        if "|" not in raw:
            await _reply(update, "❌ Format: /welcomebtn add Button Text|https://url")
            return
        text, url = raw.split("|", 1)
        text = text.strip()
        url = url.strip()
        if not text or not url:
            await _reply(update, "❌ Both text and URL are required.")
            return
        ws = await get_welcome_settings(cid)
        buttons = ws.get("welcome_buttons", [])
        buttons.append({"text": text, "url": url})
        if len(buttons) > 8:
            buttons = buttons[-8:]
        await set_welcome_settings(cid, welcome_buttons=buttons)
        await _reply(update, f"✅ Button added: <b>{safe_html(text)}</b>")
        return

    await _reply(update, "❌ Usage: /welcomebtn add <text>|<url> | clear | list")


# ─────────────────────────────────────────────────────────────────────────────
#  F. CHAT ACTIVITY / RANK
# ─────────────────────────────────────────────────────────────────────────────

async def chatrank_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        await _reply(update, "🚫 Use this in a group.")
        return

    cid = chat.id
    try:
        rows = await get_granks(cid, 10)
    except Exception:
        rows = []

    if not rows:
        await _reply(update, "📊 No activity data yet. Start chatting!")
        return

    lines = [f"📊 <b>Top Chatters — {safe_html(chat.title or f'Group {cid}')}</b>\n"]
    medals = ["🥇", "🥈", "🥉"] + [f"<b>{i+4}.</b>" for i in range(7)]
    for i, r in enumerate(rows[:10]):
        uid = r.get("user_id", 0)
        bal = r.get("balance", 0)
        try:
            u = await context.bot.get_chat_member(cid, uid)
            name = safe_html(u.user.full_name or u.user.first_name or f"User {uid}")
        except Exception:
            name = f"User {uid}"
        lines.append(f"{medals[i]} {name} — <code>${fmt_int(bal)}</code>")
    await _reply(update, "\n".join(lines))


def fmt_int(n: int) -> str:
    if n >= 1_00_00_000: return f"{n/1_00_00_000:.1f}Cr"
    if n >= 1_00_000:    return f"{n/1_00_000:.1f}L"
    if n >= 1_000:       return f"{n/1_00_000:.1f}k" if n >= 1_00_000 else f"{n/1_000:.1f}k"
    return str(n)


# ─────────────────────────────────────────────────────────────────────────────
#  G. CONFIG EXPORT / IMPORT
# ─────────────────────────────────────────────────────────────────────────────

async def exportgconfig_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await _reply(update, err)
        return

    gs = await get_group_settings(cid) or {}
    ws = await get_welcome_settings(cid)
    prot = await get_db().group_protection.find_one({"_id": cid}) or {}

    payload = {
        "group_id": cid,
        "title": title,
        "group_settings": {k: gs.get(k) for k in gs if k != "_id"},
        "welcome_settings": {k: ws.get(k) for k in ws if k != "_id"},
        "protection": {k: prot.get(k) for k in prot if k != "_id"},
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        data = json.dumps(payload, indent=2, default=str).encode()
        fname = f"group_config_{cid}.json"
        await context.bot.send_document(
            chat_id=update.effective_user.id,
            document=data,
            filename=fname,
            caption=f"📦 Config export for {safe_html(title)}",
            parse_mode="HTML",
        )
        await _reply(update, "📦 Config exported to your DM!")
    except TelegramError:
        await _reply(update, "❌ Could not send DM. Start a chat with me first.")


async def importgconfig_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await _reply(update, err)
        return

    msg = update.message
    if not msg.reply_to_message or not msg.reply_to_message.document:
        await _reply(update, "❌ Reply to a JSON config file to import.")
        return

    doc = msg.reply_to_message.document
    if not doc.file_name.endswith(".json"):
        await _reply(update, "❌ Expected a .json file.")
        return

    try:
        f = await doc.get_file()
        raw = await f.download_as_bytearray()
        payload = json.loads(raw.decode())
    except Exception as e:
        await _reply(update, f"❌ Could not parse file: {e}")
        return

    imported = 0
    gs_updates = payload.get("group_settings", {})
    ws_updates = payload.get("welcome_settings", {})
    prot_updates = payload.get("protection", {})

    gs_updates.pop("_id", None)
    ws_updates.pop("_id", None)
    prot_updates.pop("_id", None)

    if gs_updates:
        await _db().group_settings.update_one({"_id": cid}, {"$set": gs_updates})
        imported += 1
    if ws_updates:
        await _db().welcome_settings.update_one({"_id": cid}, {"$set": ws_updates})
        imported += 1
    if prot_updates:
        await _db().group_protection.update_one({"_id": cid}, {"$set": prot_updates})
        imported += 1

    await log_mod_action(cid, update.effective_user.id, "import_config", 0, f"imported {imported} sections")
    await _reply(update, f"✅ Imported <b>{imported}</b> config sections for {safe_html(title)}.")


# ─────────────────────────────────────────────────────────────────────────────
#  H. MOD LOG
# ─────────────────────────────────────────────────────────────────────────────

async def modlog_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await _reply(update, err)
        return

    limit = 15
    if context.args:
        try:
            limit = max(1, min(int(context.args[0]), 50))
        except Exception:
            pass

    rows = await list_mod_actions(cid, limit)
    if not rows:
        await _reply(update, "📭 No moderation actions logged yet.")
        return

    lines = [f"📋 <b>Mod Log — {safe_html(title)}</b> (last {len(rows)})\n"]
    for r in rows:
        ts_val = r.get("created_at", 0)
        dt = datetime.fromtimestamp(ts_val, tz=timezone.utc).strftime("%m-%d %H:%M") if ts_val else "?"
        action = r.get("action", "?")
        tid = r.get("target_id", 0)
        actor = r.get("actor_id", 0)
        reason = safe_html(r.get("reason", ""))
        lines.append(f"• <b>{action}</b> → {tid} | by {actor} | {dt} | {reason}")
    await _reply(update, "\n".join(lines))


# ─────────────────────────────────────────────────────────────────────────────
#  I. NIGHTMODE GATE — message handler
# ─────────────────────────────────────────────────────────────────────────────

async def group_gates_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Gate checks run on every non-command group message (after flood).
    - Force-sub: delete + warn if user not subscribed to channel
    - Nightmode: delete media/links/stickers from non-admins during night window
    """
    msg = update.effective_message
    chat = update.effective_chat
    u = update.effective_user
    if not msg or not u or chat.type == "private":
        return
    if u.is_bot:
        return
    if await is_admin(update, context, u.id):
        return

    cid = chat.id
    try:
        gs = await get_group_settings(cid) or {}
    except Exception:
        return

    # ── Night mode ─────────────────────────────────────────────────────────────
    if gs.get("nightmode_enabled", False):
        start = gs.get("nightmode_start", "23:00")
        end = gs.get("nightmode_end", "07:00")
        in_night = in_night_window(start, end)

        if in_night:
            restricted_types = (
                msg.photo, msg.video, msg.audio,
                msg.document, msg.animation, msg.sticker,
                msg.voice, msg.video_note, msg.poll,
            )
            text = msg.text or msg.caption or ""
            has_link = bool(re.search(r'(https?://|t\.me/|@\w{5,})', text, re.I))
            if any(restricted_types) or has_link:
                try:
                    await msg.delete()
                except Exception:
                    pass
                try:
                    w = await context.bot.send_message(
                        cid,
                        f"🌙 Night mode active. {mention(u)} — media/links restricted.",
                        parse_mode="HTML",
                    )
                    asyncio.create_task(_auto_delete(context.bot, cid, w.message_id, 8))
                except Exception:
                    pass
                return

    # ── Force subscribe ────────────────────────────────────────────────────────
    fs_channel = (gs.get("forcesub_channel") or "").strip()
    if fs_channel:
        try:
            ch_ref = (
                fs_channel if str(fs_channel).startswith("-")
                else f"@{str(fs_channel).lstrip('@')}"
            )
            ch_username = str(fs_channel).lstrip("@")
            try:
                cm = await context.bot.get_chat_member(ch_ref, u.id)
            except TelegramError:
                cm = None
            if cm is None or cm.status in ("left", "kicked"):
                try:
                    await msg.delete()
                except Exception:
                    pass
                join_url = (
                    f"https://t.me/{ch_username}"
                    if not str(fs_channel).startswith("-")
                    else f"https://t.me/c/{str(fs_channel).replace('-100', '')}"
                )
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📢 Join Channel", url=join_url)],
                ])
                try:
                    w = await context.bot.send_message(
                        cid,
                        f"📢 {mention(u)} — Join the channel to chat!\n👉 {safe_html(ch_ref)}",
                        parse_mode="HTML",
                        reply_markup=kb,
                    )
                    asyncio.create_task(_auto_delete(context.bot, cid, w.message_id, 15))
                except Exception:
                    pass
                return
        except Exception:
            pass


async def _auto_delete(bot, chat_id: int, message_id: int, delay: int = 10):
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass



