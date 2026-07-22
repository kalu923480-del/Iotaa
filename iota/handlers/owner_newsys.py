"""
╔════════════════════════════════════════════════════════════════════╗
║  IOTA BOT — Owner Systems (NEW powerful subsystems)                 ║
║                                                                      ║
║  Every command here is wrapped in the SAME @owner_only decorator    ║
║  as handlers/owner_panel.py, so each one is:                        ║
║    • owner/sudo/transfer-gated                                      ║
║    • crash-proof (any exception is reported, never silent)           ║
║    • fully logged                                                   ║
║                                                                      ║
║  These are NET-NEW systems that did not exist before:               ║
║    • Global Shield (lockdown / slowmode / media lock)               ║
║    • Anti-Abuse (mass bans, bot-gate, watchlist, sus-list)          ║
║    • Scheduling engine (broadcast / group msg / remind-all)         ║
║    • Global auto-reply + global blacklist words                     ║
║    • Analytics (growth / retention / latency / health / ping)        ║
║    • Staff (sudo / transfer / where-is / common groups)              ║
║    • Economy oversight (stats / richest / rain / reset)             ║
║    • Data (db stats / csv export / backup / vacuum / indexes)        ║
║    • AI & content (persona / default welcome / bio / menu)          ║
║    • Notifications (log chat / notify / alert)                      ║
╚════════════════════════════════════════════════════════════════════╝
"""
import asyncio
import time
import json
import csv
import io
import re
import logging
from uuid import uuid4

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions
from telegram.ext import ContextTypes
from telegram.error import (RetryAfter, Forbidden, BadRequest, TelegramError)

from handlers.owner_panel import owner_only
from utils.mongo_db import (
    get_db, get_all_groups, get_broadcastable_users, ensure_user, get_user,
    update_user, add_balance, total_users,
    list_sudo, add_sudo, remove_sudo, get_owner_override, set_owner_override,
    get_shield, set_shield, add_scheduled_job, list_scheduled_jobs,
    get_due_jobs, mark_job_done, cancel_scheduled_job, add_autoreply,
    list_autoreplies, del_autoreply, add_blackword, list_blackwords,
    del_blackword, add_watch, remove_watch, list_watch, is_watched,
    list_allowed_bots, set_allowed_bots, get_botgate_mode, set_botgate_mode,
    get_log_chat, set_log_chat, get_notify, set_notify, get_persona,
    set_persona, get_default_welcome, set_default_welcome, bump_command_stat,
    top_commands, log_error_entry, recent_errors, total_balance_in_circulation,
)
from utils.helpers import mention_owner, fmt, mention_id
from utils.safe_html import safe_html, placeholder
from utils.telegram_safe import safe_call

logger = logging.getLogger(__name__)


# ── Passive-handler caches (avoid a DB hit on every group message) ───────
_BW_CACHE = {"t": 0.0, "data": None}
_AR_CACHE = {"t": 0.0, "data": None}


async def _cached(cache, fetcher, ttl: float = 60.0):
    now = time.time()
    if cache["data"] is not None and now - cache["t"] < ttl:
        return cache["data"]
    data = await fetcher()
    cache["data"] = data
    cache["t"] = now
    return data


def _invalidate_caches():
    _BW_CACHE["data"] = None
    _AR_CACHE["data"] = None


# ── Shared helpers ────────────────────────────────────────────────────────

async def _reply(update, text, **kw):
    return await safe_call(
        lambda: update.effective_message.reply_html(text, **kw),
        label="ownersys.reply",
    )


def _parse_when(s: str):
    """Parse a scheduling time into a unix epoch. Supports:
      +30m / 2h / 3d / 45s   (relative)
      18:30                  (today, or tomorrow if already passed)
      1710000000             (absolute unix timestamp)
    Returns int epoch or None."""
    if not s:
        return None
    s = s.strip()
    now = int(time.time())
    m = re.match(r'^(\d+)([smhd])$', s.lstrip("+"))
    if m:
        v, u = int(m.group(1)), m.group(2)
        return now + v * {"s": 1, "m": 60, "h": 3600, "d": 86400}[u]
    m = re.match(r'^(\d{1,2}):(\d{2})$', s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if not (0 <= h <= 23 and 0 <= mi <= 59):
            return None
        t = time.localtime(now)
        cand = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, h, mi, 0, 0, 0, t.tm_isdst))
        if cand <= now:
            cand += 86400
        return int(cand)
    if s.isdigit() and len(s) >= 9:
        return int(s)
    return None


def _fmt_time(epoch: int) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch))
    except Exception:
        return str(epoch)


def _parse_target(token: str):
    """Parse a user id or @username into an int id (best-effort)."""
    if not token:
        return None
    token = token.lstrip("@")
    if token.isdigit():
        return int(token)
    return token  # caller may need to resolve via DB/bot


async def _resolve_uid(context, token):
    tok = _parse_target(token)
    if isinstance(tok, int):
        return tok
    # @username → try bot lookup, then our own DB
    try:
        ch = await context.bot.get_chat(f"@{tok}")
        return ch.id
    except Exception:
        pass
    from utils.mongo_db import get_user_by_username
    u = await get_user_by_username(tok)
    return u["_id"] if u else None


# ═════════════════════════════════════════════════════════════════════════
# SYSTEM 1 — GLOBAL SHIELD (lockdown / slowmode / media lock)
# ═════════════════════════════════════════════════════════════════════════

_OPEN = ChatPermissions(
    can_send_messages=True, can_send_polls=True,
    can_send_other_messages=True, can_add_web_page_previews=True,
    can_change_info=True, can_invite_users=True, can_pin_messages=True,
    can_send_audios=True, can_send_documents=True, can_send_photos=True,
    can_send_videos=True, can_send_video_notes=True, can_send_voice_notes=True,
)
_LOCKED = ChatPermissions(
    can_send_messages=False, can_send_polls=False,
    can_send_other_messages=False, can_add_web_page_previews=False,
    can_change_info=False, can_invite_users=False, can_pin_messages=False,
    can_send_audios=False, can_send_documents=False, can_send_photos=False,
    can_send_videos=False, can_send_video_notes=False, can_send_voice_notes=False,
)
_MEDIA_LOCKED = ChatPermissions(
    can_send_messages=True, can_send_polls=False,
    can_send_other_messages=False, can_add_web_page_previews=False,
    can_change_info=True, can_invite_users=True, can_pin_messages=True,
    can_send_audios=False, can_send_documents=False, can_send_photos=False,
    can_send_videos=False, can_send_video_notes=False, can_send_voice_notes=False,
)


async def _apply_to_admin_groups(context, perms=None, slow=None):
    """Apply chat permissions / slowmode to every group where the bot is an
    admin. Returns (ok, skipped) counts. Fail-soft per group."""
    groups = await get_all_groups()
    ok = skipped = 0
    for g in groups:
        gid = g["_id"]
        try:
            if perms is not None:
                await context.bot.set_chat_permissions(gid, perms)
            if slow is not None:
                await context.bot.set_chat_slow_mode_delay(gid, slow)
            ok += 1
        except (Forbidden, BadRequest):
            skipped += 1
        except Exception:
            skipped += 1
        await asyncio.sleep(0.05)
    return ok, skipped


@owner_only
async def lockdown_cmd(update, context):
    """Globally lock every group the bot administers (no posting)."""
    reason = " ".join(context.args) or "Owner-initiated lockdown"
    ok, skipped = await _apply_to_admin_groups(context, perms=_LOCKED)
    await set_shield(lockdown=True, reason=reason, since=int(time.time()))
    await _reply(update,
        f"🔒 <b>Global lockdown ENGAGED.</b>\n\n"
        f"Reason: {safe_html(reason)}\n"
        f"Groups locked: <b>{ok}</b> · skipped (no admin): <b>{skipped}</b>\n\n"
        f"Use /globalunlock to release."
    )


@owner_only
async def global_unlock_cmd(update, context):
    """Release the global lockdown."""
    ok, skipped = await _apply_to_admin_groups(context, perms=_OPEN)
    await set_shield(lockdown=False, reason="")
    await _reply(update,
        f"🔓 <b>Global lockdown RELEASED.</b>\n"
        f"Groups reopened: <b>{ok}</b> · skipped: <b>{skipped}</b>"
    )


@owner_only
async def slowall_cmd(update, context):
    """Set slowmode in every group the bot administers. /slowall <seconds|off>"""
    if not context.args:
        await _reply(update, "Usage: /slowall <seconds|off>")
        return
    if context.args[0].lower() == "off":
        ok, skipped = await _apply_to_admin_groups(context, slow=0)
        await set_shield(slowmode=0)
        await _reply(update, f"⏱️ Slowmode <b>off</b> in {ok} groups (skipped {skipped}).")
        return
    if not context.args[0].isdigit():
        await _reply(update, "❌ Seconds must be a number, or 'off'.")
        return
    sec = int(context.args[0])
    ok, skipped = await _apply_to_admin_groups(context, slow=sec)
    await set_shield(slowmode=sec)
    await _reply(update, f"⏱️ Slowmode set to <b>{sec}s</b> in {ok} groups (skipped {skipped}).")


@owner_only
async def lockall_cmd(update, context):
    """Lock media/stickers/links in every admin group (text still allowed)."""
    ok, skipped = await _apply_to_admin_groups(context, perms=_MEDIA_LOCKED)
    await set_shield(media_locked=True)
    await _reply(update,
        f"🚫 <b>Media locked</b> in {ok} groups (skipped {skipped}). "
        f"Text still allowed. /unlockall to revert."
    )


@owner_only
async def unlockall_cmd(update, context):
    """Restore media permissions in every admin group."""
    ok, skipped = await _apply_to_admin_groups(context, perms=_OPEN)
    await set_shield(media_locked=False)
    await _reply(update, f"✅ Media <b>unlocked</b> in {ok} groups (skipped {skipped}).")


@owner_only
async def shieldstatus_cmd(update, context):
    """Show the current global shield state."""
    s = await get_shield()
    lines = [
        f"🛡️ <b>Global Shield Status</b>",
        f"Lockdown: {'🔒 ON' if s.get('lockdown') else '🔓 off'}",
    ]
    if s.get("lockdown"):
        lines.append(f"  └ reason: {safe_html(s.get('reason',''))}")
    lines.append(f"Slowmode: {s.get('slowmode', 0)}s")
    lines.append(f"Media lock: {'on' if s.get('media_locked') else 'off'}")
    await _reply(update, "\n".join(lines))


# ═════════════════════════════════════════════════════════════════════════
# SYSTEM 2 — ANTI-ABUSE (mass bans, bot-gate, watchlist, sus-list)
# ═════════════════════════════════════════════════════════════════════════

@owner_only
async def massban_cmd(update, context):
    """Ban one or more users from EVERY group the bot administers.
    /massban <id/@user> [id2 ...] [reason]"""
    if not context.args:
        await _reply(update, "Usage: /massban <id/@user> [id2 ...] [reason]")
        return
    ids, reason = [], []
    for a in context.args:
        if a.startswith("@"):
            uid = await _resolve_uid(context, a)
            if uid:
                ids.append(uid)
        elif a.lstrip("-").isdigit():
            ids.append(int(a))
        else:
            reason.append(a)
    if not ids:
        await _reply(update, "❌ No valid user ids supplied.")
        return
    groups = await get_all_groups()
    done = 0
    for uid in ids:
        for g in groups:
            try:
                await context.bot.ban_chat_member(g["_id"], uid)
                done += 1
            except Exception:
                pass
            await asyncio.sleep(0.03)
    await _reply(update,
        f"🔨 Banned <b>{len(ids)}</b> user(s) across groups.\n"
        f"Total ban actions: <b>{done}</b>\n"
        f"Reason: {safe_html(' '.join(reason) or 'n/a')}"
    )


@owner_only
async def massunban_cmd(update, context):
    """Unban one or more users from EVERY group. /massunban <id> [id2 ...]"""
    ids = [int(a) for a in context.args if a.lstrip('-').isdigit()]
    if not ids:
        await _reply(update, "Usage: /massunban <id> [id2 ...]")
        return
    groups = await get_all_groups()
    done = 0
    for uid in ids:
        for g in groups:
            try:
                await context.bot.unban_chat_member(g["_id"], uid, only_if_banned=True)
                done += 1
            except Exception:
                pass
            await asyncio.sleep(0.03)
    await _reply(update, f"✅ Unbanned <b>{len(ids)}</b> user(s). Actions: <b>{done}</b>.")


@owner_only
async def banfrom_cmd(update, context):
    """Ban a user from ONE specific group. /banfrom <group_id> <user_id> [reason]"""
    if len(context.args) < 2:
        await _reply(update, "Usage: /banfrom <group_id> <user_id> [reason]")
        return
    gid = int(context.args[0])
    uid = await _resolve_uid(context, context.args[1])
    if not uid:
        await _reply(update, "❌ Could not resolve the user.")
        return
    reason = " ".join(context.args[2:]) or "owner ban"
    try:
        await context.bot.ban_chat_member(gid, uid)
        await _reply(update, f"🔨 Banned {mention_id(uid, str(uid))} from group <code>{gid}</code>.")
    except Exception as e:
        await _reply(update, f"❌ Failed: {safe_html(str(e))}")


@owner_only
async def unbanfrom_cmd(update, context):
    """Unban a user from ONE specific group. /unbanfrom <group_id> <user_id>"""
    if len(context.args) < 2:
        await _reply(update, "Usage: /unbanfrom <group_id> <user_id>")
        return
    gid = int(context.args[0])
    uid = int(context.args[1]) if context.args[1].lstrip('-').isdigit() else \
        await _resolve_uid(context, context.args[1])
    if not uid:
        await _reply(update, "❌ Could not resolve the user.")
        return
    try:
        await context.bot.unban_chat_member(gid, uid, only_if_banned=True)
        await _reply(update, f"✅ Unbanned from group <code>{gid}</code>.")
    except Exception as e:
        await _reply(update, f"❌ Failed: {safe_html(str(e))}")


@owner_only
async def cleanbots_cmd(update, context):
    """Kick every bot (except Iota and allowed bots) from a group.
    /cleanbots <group_id>"""
    if not context.args:
        await _reply(update, "Usage: /cleanbots <group_id>")
        return
    gid = int(context.args[0])
    allowed = set(await list_allowed_bots())
    try:
        members = await context.bot.get_chat_administrators(gid)
    except Exception as e:
        await _reply(update, f"❌ Cannot list admins: {safe_html(str(e))}")
        return
    me = await context.bot.get_me()
    kicked = 0
    for m in members:
        u = m.user
        if not u.is_bot:
            continue
        if u.id == me.id:
            continue
        uname = (u.username or "").lower()
        if uname in allowed:
            continue
        try:
            await context.bot.ban_chat_member(gid, u.id)
            await context.bot.unban_chat_member(gid, u.id, only_if_banned=True)
            kicked += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    await _reply(update, f"🤖 Kicked <b>{kicked}</b> bot(s) from group <code>{gid}</code>.")


@owner_only
async def botgate_cmd(update, context):
    """Control the bot-gate (auto-kick bots that join groups).
    /botgate <off|allow|deny>  — allow = only allowlisted bots may stay,
    deny = kick ALL bots that join, off = disabled."""
    if not context.args:
        mode = await get_botgate_mode()
        allowed = ", ".join(await list_allowed_bots()) or "(none)"
        await _reply(update,
            f"🚪 <b>Bot-gate:</b> {mode}\nAllowed bots: {safe_html(allowed)}\n\n"
            f"Usage: /botgate <off|allow|deny>\n"
            f"  off   → don't auto-kick joining bots\n"
            f"  allow → only allowlisted bots may stay (kick others)\n"
            f"  deny  → kick every bot that joins"
        )
        return
    mode = context.args[0].lower()
    if mode not in ("off", "allow", "deny"):
        await _reply(update, "❌ Mode must be off|allow|deny.")
        return
    await set_botgate_mode(mode)
    await _reply(update, f"🚪 Bot-gate set to <b>{mode}</b>.")


@owner_only
async def allowedbots_cmd(update, context):
    """Manage the bot-gate allowlist. /allowedbots <add|remove|list> [@bot]"""
    if not context.args or context.args[0].lower() == "list":
        al = await list_allowed_bots()
        await _reply(update, "✅ Allowed bots:\n" + ("\n".join('@'+x for x in al) or "(none)"))
        return
    act = context.args[0].lower()
    if act not in ("add", "remove"):
        await _reply(update, "Usage: /allowedbots <add|remove|list> [@bot]")
        return
    if len(context.args) < 2:
        await _reply(update, "❌ Provide a @bot username.")
        return
    name = context.args[1].lstrip("@").lower()
    cur = set(await list_allowed_bots())
    if act == "add":
        cur.add(name)
    else:
        cur.discard(name)
    await set_allowed_bots(list(cur))
    await _reply(update, f"✅ Allowlist now: {safe_html(', '.join('@'+x for x in cur) or '(empty)')}")


@owner_only
async def watchuser_cmd(update, context):
    """Add a user to the watchlist (their activity is tracked).
    /watchuser <id/@user> [note]"""
    if not context.args:
        await _reply(update, "Usage: /watchuser <id/@user> [note]")
        return
    uid = await _resolve_uid(context, context.args[0])
    if not uid:
        await _reply(update, "❌ Could not resolve user.")
        return
    note = " ".join(context.args[1:])
    await add_watch(uid, note)
    await _reply(update, f"👁️ Now watching {mention_id(uid, str(uid))}." +
                (f"\nNote: {safe_html(note)}" if note else ""))


@owner_only
async def unwatch_cmd(update, context):
    """Remove a user from the watchlist. /unwatch <id>"""
    if not context.args:
        await _reply(update, "Usage: /unwatch <id>")
        return
    uid = int(context.args[0]) if context.args[0].lstrip('-').isdigit() else \
        await _resolve_uid(context, context.args[0])
    if not uid:
        await _reply(update, "❌ Invalid id.")
        return
    ok = await remove_watch(uid)
    await _reply(update, f"{'✅ Removed from' if ok else 'ℹ️ Not in'} watchlist.")


@owner_only
async def watchlist_cmd(update, context):
    """List all watched users and their last seen activity."""
    w = await list_watch()
    if not w:
        await _reply(update, "📭 Watchlist is empty. Use /watchuser <id>.")
        return
    lines = ["👁️ <b>Watchlist</b>"]
    for x in w[:50]:
        uid = x["_id"]
        la = x.get("last_active")
        la_s = _fmt_time(la) if la else "never seen"
        note = x.get("note", "")
        lines.append(f"• {mention_id(uid, str(uid))} — last: {la_s}" +
                     (f" · {safe_html(note)}" if note else ""))
    await _reply(update, "\n".join(lines))


@owner_only
async def suslist_cmd(update, context):
    """List accounts created in the last 48h (potential spam/alt accounts)."""
    since = int(time.time()) - 2 * 86400
    db = get_db()
    rows = await db.users.find(
        {"created_at": {"$gte": since}},
        sort=[("created_at", -1)], limit=50
    ).to_list(50)
    if not rows:
        await _reply(update, "✅ No accounts created in the last 48 hours.")
        return
    lines = [f"🚩 <b>Suspicious new accounts (last 48h): {len(rows)}</b>"]
    for u in rows:
        uid = u["_id"]
        nm = u.get("full_name") or u.get("username") or "?"
        lines.append(f"• {mention_id(uid, safe_html(str(nm)))} — "
                     f"@{safe_html(u.get('username','') or '?')}")
    await _reply(update, "\n".join(lines))


# ═════════════════════════════════════════════════════════════════════════
# SYSTEM 3 — SCHEDULING ENGINE
# ═════════════════════════════════════════════════════════════════════════

@owner_only
async def schedbroadcast_cmd(update, context):
    """Schedule a broadcast. /schedbroadcast <when> <message>
    <when> = +30m | 2h | 3d | HH:MM | unix_ts"""
    if len(context.args) < 2:
        await _reply(update, "Usage: /schedbroadcast <when> <message>\n"
                             "when: +30m, 2h, 3d, 18:30, 1710000000")
        return
    when = _parse_when(context.args[0])
    if not when:
        await _reply(update, "❌ Invalid time. Try +30m, 2h, 3d, HH:MM or a unix timestamp.")
        return
    text = " ".join(context.args[1:])
    jid = await add_scheduled_job("broadcast", when, {"text": text})
    await _reply(update, f"⏰ Broadcast scheduled!\n🆔 <code>{jid}</code>\n"
                         f"🕒 Runs: {_fmt_time(when)}\n📝 {safe_html(text)}")


@owner_only
async def schedmsg_cmd(update, context):
    """Schedule a message to one group. /schedmsg <group_id> <when> <message>"""
    if len(context.args) < 3:
        await _reply(update, "Usage: /schedmsg <group_id> <when> <message>")
        return
    try:
        gid = int(context.args[0])
    except ValueError:
        await _reply(update, "❌ group_id must be a number.")
        return
    when = _parse_when(context.args[1])
    if not when:
        await _reply(update, "❌ Invalid time.")
        return
    text = " ".join(context.args[2:])
    jid = await add_scheduled_job("group_msg", when, {"gid": gid, "text": text})
    await _reply(update, f"⏰ Group message scheduled!\n🆔 <code>{jid}</code>\n"
                         f"📍 Group: <code>{gid}</code>\n🕒 {_fmt_time(when)}")


@owner_only
async def remindall_cmd(update, context):
    """Schedule a message to ALL groups. /remindall <when> <message>"""
    if len(context.args) < 2:
        await _reply(update, "Usage: /remindall <when> <message>")
        return
    when = _parse_when(context.args[0])
    if not when:
        await _reply(update, "❌ Invalid time.")
        return
    text = " ".join(context.args[1:])
    jid = await add_scheduled_job("greetall", when, {"text": text})
    await _reply(update, f"⏰ Reminder to all groups scheduled!\n🆔 <code>{jid}</code>\n"
                         f"🕒 {_fmt_time(when)}")


@owner_only
async def scheds_cmd(update, context):
    """List pending scheduled jobs."""
    jobs = await list_scheduled_jobs()
    if not jobs:
        await _reply(update, "📭 No scheduled jobs. Use /schedbroadcast, /schedmsg, /remindall.")
        return
    lines = ["⏰ <b>Pending scheduled jobs</b>"]
    for j in jobs:
        p = j.get("payload", {})
        preview = safe_html(str(p.get("text", ""))[:60])
        tgt = f" → grp {p['gid']}" if j["kind"] == "group_msg" else ""
        lines.append(f"🆔 <code>{j['_id']}</code> · {j['kind']}{tgt}\n"
                     f"   🕒 {_fmt_time(j['run_at'])} · {preview}")
    await _reply(update, "\n".join(lines))


@owner_only
async def cancelsched_cmd(update, context):
    """Cancel a scheduled job. /cancelsched <job_id>"""
    if not context.args:
        await _reply(update, "Usage: /cancelsched <job_id>")
        return
    ok = await cancel_scheduled_job(context.args[0])
    await _reply(update, f"{'✅ Cancelled' if ok else '❌ Job not found'}.")


# ═════════════════════════════════════════════════════════════════════════
# SYSTEM 4 — GLOBAL AUTO-REPLY
# ═════════════════════════════════════════════════════════════════════════

@owner_only
async def autoreply_cmd(update, context):
    """Add a global auto-reply. /autoreply <trigger> => <response>"""
    if not context.args or "=>" not in " ".join(context.args):
        await _reply(update, "Usage: /autoreply <trigger> => <response>\n"
                             "Example: /autoreply price => Use /shop to buy items")
        return
    text = " ".join(context.args)
    trig, resp = text.split("=>", 1)
    trig, resp = trig.strip(), resp.strip()
    if not trig or not resp:
        await _reply(update, "❌ Both trigger and response are required.")
        return
    rid = await add_autoreply(trig, resp)
    _invalidate_caches()
    await _reply(update, f"✅ Auto-reply added! 🆔 <code>{rid}</code>\n"
                         f"🔑 Trigger: {safe_html(trig)}\n💬 {safe_html(resp)}")


@owner_only
async def autoreplies_cmd(update, context):
    """List all global auto-reply rules."""
    rules = await list_autoreplies()
    if not rules:
        await _reply(update, "📭 No auto-replies. Use /autoreply <trigger> => <response>.")
        return
    lines = ["🤖 <b>Global auto-replies</b>"]
    for r in rules[:50]:
        lines.append(f"🆔 <code>{r['_id']}</code> · {safe_html(r['trigger'])} "
                     f"→ {safe_html(r['response'][:40])}")
    await _reply(update, "\n".join(lines))


@owner_only
async def delautoreply_cmd(update, context):
    """Delete an auto-reply rule. /delautoreply <id>"""
    if not context.args:
        await _reply(update, "Usage: /delautoreply <id>")
        return
    ok = await del_autoreply(context.args[0])
    _invalidate_caches()
    await _reply(update, f"{'✅ Deleted' if ok else '❌ Not found'}.")


# ═════════════════════════════════════════════════════════════════════════
# SYSTEM 5 — GLOBAL BLACKLIST WORDS
# ═════════════════════════════════════════════════════════════════════════

@owner_only
async def blackword_cmd(update, context):
    """Add a global blacklist word (auto-deleted in groups). /blackword <word>"""
    if not context.args:
        await _reply(update, "Usage: /blackword <word>")
        return
    w = context.args[0]
    await add_blackword(w)
    _invalidate_caches()
    await _reply(update, f"🚫 Added blacklist word: {safe_html(w)}")


@owner_only
async def blackwords_cmd(update, context):
    """List all global blacklist words."""
    ws = await list_blackwords()
    if not ws:
        await _reply(update, "📭 No blacklist words. Use /blackword <word>.")
        return
    await _reply(update, "🚫 <b>Blacklist words:</b>\n" +
                "\n".join(f"• {safe_html(x['word'])}" for x in ws[:100]))


@owner_only
async def delblackword_cmd(update, context):
    """Remove a blacklist word. /delblackword <word>"""
    if not context.args:
        await _reply(update, "Usage: /delblackword <word>")
        return
    ok = await del_blackword(context.args[0])
    _invalidate_caches()
    await _reply(update, f"{'✅ Removed' if ok else '❌ Not found'}.")


# ═════════════════════════════════════════════════════════════════════════
# SYSTEM 6 — ANALYTICS & MONITORING
# ═════════════════════════════════════════════════════════════════════════

@owner_only
async def growth_cmd(update, context):
    """Show user growth over the last 7 and 30 days."""
    db = get_db()
    now = int(time.time())
    d7, d30 = now - 7 * 86400, now - 30 * 86400
    n7 = await db.users.count_documents({"created_at": {"$gte": d7}})
    n30 = await db.users.count_documents({"created_at": {"$gte": d30}})
    total = await total_users()
    await _reply(update,
        f"📈 <b>User Growth</b>\n\n"
        f"👥 Total users: <b>{total}</b>\n"
        f"➕ Last 7 days: <b>{n7}</b>\n"
        f"➕ Last 30 days: <b>{n30}</b>"
    )


@owner_only
async def retention_cmd(update, context):
    """Estimate active users (last 1/7/30 days) via last_seen."""
    db = get_db()
    now = int(time.time())
    def cnt(d):
        return db.users.count_documents({"last_seen": {"$gte": now - d}})
    d1, d7, d30 = await cnt(86400), await cnt(7 * 86400), await cnt(30 * 86400)
    total = await total_users()
    await _reply(update,
        f"🔁 <b>Retention (active by last_seen)</b>\n\n"
        f"🟢 Daily (24h): <b>{d1}</b>\n"
        f"🟡 Weekly (7d): <b>{d7}</b>\n"
        f"🔵 Monthly (30d): <b>{d30}</b>\n"
        f"👥 Total: <b>{total}</b>"
    )


@owner_only
async def latency_cmd(update, context):
    """Measure Telegram API round-trip latency."""
    t0 = time.time()
    try:
        me = await context.bot.get_me()
        dt = (time.time() - t0) * 1000
        await _reply(update, f"⚡ API latency: <b>{dt:.0f} ms</b>\n🤖 @{me.username}")
    except Exception as e:
        await _reply(update, f"❌ API error: {safe_html(str(e))}")


@owner_only
async def health_cmd(update, context):
    """Full system health check (DB, counts, latency, shield)."""
    t0 = time.time()
    db_ok = False
    try:
        await get_db().command("ping")
        db_ok = True
    except Exception:
        pass
    dt = (time.time() - t0) * 1000
    tu = await total_users()
    groups = await get_all_groups()
    shield = await get_shield()
    status = "🟢 HEALTHY" if db_ok else "🔴 DATABASE DOWN"
    await _reply(update,
        f"🩺 <b>System Health: {status}</b>\n\n"
        f"🗄️ MongoDB: {'✅ up' if db_ok else '❌ DOWN'}\n"
        f"⚡ API latency: <b>{dt:.0f} ms</b>\n"
        f"👥 Users: <b>{tu}</b>\n"
        f"💬 Groups tracked: <b>{len(groups)}</b>\n"
        f"🛡️ Lockdown: {'on' if shield.get('lockdown') else 'off'}"
    )


@owner_only
async def pingall_cmd(update, context):
    """Ping every tracked group and report how many are reachable."""
    groups = await get_all_groups()
    ok = fail = 0
    for g in groups:
        try:
            await context.bot.get_chat(g["_id"])
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.03)
    await _reply(update,
        f"📡 <b>Ping all groups</b>\n✅ Reachable: <b>{ok}</b>\n"
        f"❌ Unreachable/kicked: <b>{fail}</b>"
    )


@owner_only
async def deadgroups_cmd(update, context):
    """List groups the bot has left / been kicked from."""
    db = get_db()
    docs = await db.group_settings.find(
        {"active": False}, {"title": 1}
    ).to_list(200)
    if not docs:
        await _reply(update, "✅ No inactive groups recorded.")
        return
    lines = [f"🪦 <b>Inactive groups ({len(docs)}):</b>"]
    for d in docs[:100]:
        lines.append(f"• <code>{d['_id']}</code> — {safe_html(d.get('title','?'))}")
    await _reply(update, "\n".join(lines))


@owner_only
async def online_cmd(update, context):
    """Estimate how many users were active in the last 5 minutes."""
    db = get_db()
    n = await db.users.count_documents({"last_seen": {"$gte": int(time.time()) - 300}})
    await _reply(update, f"🟢 ~<b>{n}</b> users active in the last 5 minutes.")


@owner_only
async def commandstats_cmd(update, context):
    """Show the most-used commands."""
    rows = await top_commands(20)
    if not rows:
        await _reply(update, "📊 No command usage recorded yet.")
        return
    lines = ["📊 <b>Top commands</b>"]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. /{safe_html(r['_id'])} — <b>{r.get('count',0)}</b>")
    await _reply(update, "\n".join(lines))


@owner_only
async def errorlog_cmd(update, context):
    """Show the most recent bot errors."""
    rows = await recent_errors(20)
    if not rows:
        await _reply(update, "🧼 No recent errors logged.")
        return
    lines = ["🐞 <b>Recent errors</b>"]
    for e in rows:
        lines.append(f"• {_fmt_time(e.get('t',0))} — {safe_html(str(e.get('msg',''))[:120])}")
    await _reply(update, "\n".join(lines))


# ═════════════════════════════════════════════════════════════════════════
# SYSTEM 7 — STAFF MANAGEMENT
# ═════════════════════════════════════════════════════════════════════════

@owner_only
async def sudoadd_cmd(update, context):
    """Promote a user to sudo staff (can use the owner panel).
    /sudoadd <id/@user>"""
    if not context.args:
        await _reply(update, "Usage: /sudoadd <id/@user>")
        return
    uid = await _resolve_uid(context, context.args[0])
    if not uid:
        await _reply(update, "❌ Could not resolve user.")
        return
    await add_sudo(uid)
    await _reply(update, f"🛡️ Added {mention_id(uid, str(uid))} to sudo staff.")


@owner_only
async def sudoremove_cmd(update, context):
    """Remove a user from sudo staff. /sudoremove <id>"""
    if not context.args:
        await _reply(update, "Usage: /sudoremove <id>")
        return
    uid = int(context.args[0]) if context.args[0].lstrip('-').isdigit() else \
        await _resolve_uid(context, context.args[0])
    if not uid:
        await _reply(update, "❌ Invalid id.")
        return
    await remove_sudo(uid)
    await _reply(update, f"🗑️ Removed {mention_id(uid, str(uid))} from sudo staff.")


@owner_only
async def stafflist_cmd(update, context):
    """List owner + sudo staff."""
    ov = await get_owner_override()
    owner_id = ov or int(__import__("config").OWNER_ID)
    su = await list_sudo()
    lines = [f"🛡️ <b>Staff</b>\n👑 Owner: {mention_id(owner_id, str(owner_id))}"]
    if su:
        lines.append("🧑‍💼 Sudo:")
        for s in su:
            lines.append(f"  • {mention_id(s, str(s))}")
    else:
        lines.append("🧑‍💼 Sudo: (none)")
    await _reply(update, "\n".join(lines))


@owner_only
async def handover_cmd(update, context):
    """Transfer bot ownership to another user. /handover <id> confirm"""
    if len(context.args) < 2 or context.args[1].lower() != "confirm":
        await _reply(update,
            "⚠️ <b>This transfers FULL ownership.</b>\n"
            "To confirm: /transfer <id> confirm")
        return
    uid = await _resolve_uid(context, context.args[0])
    if not uid:
        await _reply(update, "❌ Could not resolve user.")
        return
    await set_owner_override(uid)
    await _reply(update, f"👑 Ownership transferred to {mention_id(uid, str(uid))}.")


@owner_only
async def whereis_cmd(update, context):
    """Find which tracked groups a user is in. /whereis <id/@user>"""
    if not context.args:
        await _reply(update, "Usage: /whereis <id/@user>")
        return
    uid = await _resolve_uid(context, context.args[0])
    if not uid:
        await _reply(update, "❌ Could not resolve user.")
        return
    groups = await get_all_groups()
    found = []
    for g in groups[:150]:
        try:
            m = await context.bot.get_chat_member(g["_id"], uid)
            if m.status in ("member", "administrator", "creator"):
                found.append(g["_id"])
        except Exception:
            pass
        await asyncio.sleep(0.04)
    if not found:
        await _reply(update, f"🔍 {mention_id(uid, str(uid))} not found in any tracked group.")
        return
    await _reply(update, f"🔍 {mention_id(uid, str(uid))} is in {len(found)} group(s):\n" +
                "\n".join(f"• <code>{x}</code>" for x in found[:100]))


@owner_only
async def common_cmd(update, context):
    """List groups two users have in common. /common <id1> <id2>"""
    if len(context.args) < 2:
        await _reply(update, "Usage: /common <id1> <id2>")
        return
    u1 = await _resolve_uid(context, context.args[0])
    u2 = await _resolve_uid(context, context.args[1])
    if not u1 or not u2:
        await _reply(update, "❌ Could not resolve both users.")
        return
    groups = await get_all_groups()
    common = []
    for g in groups[:150]:
        try:
            a = await context.bot.get_chat_member(g["_id"], u1)
            b = await context.bot.get_chat_member(g["_id"], u2)
            if a.status in ("member", "administrator", "creator") and \
               b.status in ("member", "administrator", "creator"):
                common.append(g["_id"])
        except Exception:
            pass
        await asyncio.sleep(0.04)
    if not common:
        await _reply(update, "🔍 No common groups found.")
        return
    await _reply(update, f"🔗 Common groups ({len(common)}):\n" +
                "\n".join(f"• <code>{x}</code>" for x in common[:100]))


# ═════════════════════════════════════════════════════════════════════════
# SYSTEM 8 — ECONOMY OVERSIGHT
# ═════════════════════════════════════════════════════════════════════════

@owner_only
async def economystats_cmd(update, context):
    """Overview of the in-circulation economy."""
    t = await total_balance_in_circulation()
    tu = await total_users()
    await _reply(update,
        f"💰 <b>Economy Overview</b>\n\n"
        f"👥 Users: <b>{tu}</b>\n"
        f"🪙 Coins (balance): <b>{fmt(t.get('bal',0))}</b>\n"
        f"👛 Coins (wallet): <b>{fmt(t.get('wal',0))}</b>\n"
        f"💎 Gems: <b>{t.get('gem',0)}</b>"
    )


@owner_only
async def rain_cmd(update, context):
    """Rain coins to N random active users. /rain <amount> [count=20]"""
    if not context.args or not context.args[0].isdigit():
        await _reply(update, "Usage: /rain <amount> [count=20]")
        return
    amount = int(context.args[0])
    count = 20
    if len(context.args) > 1 and context.args[1].isdigit():
        count = max(1, min(200, int(context.args[1])))
    db = get_db()
    users = await db.users.aggregate([
        {"$match": {"is_banned": {"$ne": True}, "balance": {"$gte": 0}}},
        {"$sample": {"size": count}},
    ]).to_list(count)
    if not users:
        await _reply(update, "❌ No users to rain on.")
        return
    per = amount // len(users) or 1
    for u in users:
        try:
            await add_balance(u["_id"], per)
        except Exception:
            pass
    await _reply(update, f"🌧️ Rained <b>{fmt(amount)}</b> coins → "
                         f"<b>{len(users)}</b> users (~{fmt(per)} each).")


@owner_only
async def reseteco_cmd(update, context):
    """Reset a user's economy. /reseteco <id/@user>"""
    if not context.args:
        await _reply(update, "Usage: /reseteco <id/@user>")
        return
    uid = await _resolve_uid(context, context.args[0])
    if not uid:
        await _reply(update, "❌ Could not resolve user.")
        return
    await update_user(uid, balance=0, wallet=0, gems=0)
    await _reply(update, f"♻️ Reset economy for {mention_id(uid, str(uid))}.")


# ═════════════════════════════════════════════════════════════════════════
# SYSTEM 9 — DATA & BACKUP
# ═════════════════════════════════════════════════════════════════════════

@owner_only
async def dbstats_cmd(update, context):
    """Show collection sizes / document counts."""
    db = get_db()
    names = await db.list_collection_names()
    lines = ["🗄️ <b>Database stats</b>"]
    for n in sorted(names):
        try:
            c = await db[n].count_documents({})
        except Exception:
            c = -1
        lines.append(f"• {safe_html(n)}: <b>{c}</b>")
    await _reply(update, "\n".join(lines))


@owner_only
async def exportcsv_cmd(update, context):
    if not await require_dm(update, context, "/exportcsv", "panel"):
        return
    """Export all users to a CSV file and send it to you."""
    db = get_db()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["user_id", "username", "full_name", "balance", "gems",
                "is_premium", "is_banned", "created_at"])
    async for u in db.users.find({}):
        w.writerow([
            u.get("_id"), u.get("username", ""), u.get("full_name", ""),
            u.get("balance", 0), u.get("gems", 0),
            u.get("is_premium", False), u.get("is_banned", False),
            u.get("created_at", 0),
        ])
    data = buf.getvalue().encode("utf-8")
    bio = io.BytesIO(data)
    bio.name = "iota_users.csv"
    await safe_call(
        lambda: update.effective_message.reply_document(
            document=bio, filename="iota_users.csv",
            caption="📤 User export (CSV)"
        ),
        label="exportcsv",
    )


@owner_only
async def backup_cmd(update, context):
    if not await require_dm(update, context, "/backup", "panel"):
        return
    """Dump key collections to a JSON backup and send it to you."""
    db = get_db()
    dump = {}
    for col in ("users", "group_settings", "welcome_settings", "bot_config"):
        try:
            dump[col] = await db[col].find({}).to_list(2000)
        except Exception:
            dump[col] = []
    data = json.dumps(dump, default=str, indent=1).encode("utf-8")
    bio = io.BytesIO(data)
    bio.name = "iota_backup.json"
    await safe_call(
        lambda: update.effective_message.reply_document(
            document=bio, filename="iota_backup.json",
            caption="🗄️ Iota DB backup"
        ),
        label="backup",
    )


@owner_only
async def vacuum_cmd(update, context):
    """Clean up old done scheduled jobs and trim the error log."""
    db = get_db()
    now = int(time.time())
    r1 = await db.scheduled_jobs.delete_many({"done": True})
    excess = await db.error_log.find({}, {"_id": 1}).sort("t", 1).to_list(1000)
    total = len(excess)
    keep = 200
    if total > keep:
        ids = [e["_id"] for e in excess[: total - keep]]
        await db.error_log.delete_many({"_id": {"$in": ids}})
    await _reply(update, f"🧹 Vacuumed <b>{r1.deleted_count}</b> finished jobs "
                         f"and trimmed the error log.")


@owner_only
async def indexes_cmd(update, context):
    """Ensure MongoDB indexes are created."""
    try:
        from utils.mongo_db import create_indexes
        await create_indexes()
        await _reply(update, "✅ Indexes ensured.")
    except Exception as e:
        await _reply(update, f"❌ Index creation failed: {safe_html(str(e))}")


# ═════════════════════════════════════════════════════════════════════════
# SYSTEM 10 — AI & CONTENT
# ═════════════════════════════════════════════════════════════════════════

@owner_only
async def persona_cmd(update, context):
    """Set or view the bot's global persona. /persona <text> (empty = view)"""
    text = " ".join(context.args)
    if not text:
        cur = await get_persona()
        await _reply(update, f"🧠 <b>Current persona:</b>\n{safe_html(cur) or '(none set)'}")
        return
    await set_persona(text)
    await _reply(update, f"🧠 Persona set:\n{safe_html(text)}")


@owner_only
async def defaultwelcome_cmd(update, context):
    """Set a global default welcome applied to all groups. /defaultwelcome <msg>"""
    text = " ".join(context.args)
    if not text:
        cur = await get_default_welcome()
        await _reply(update, f"🌐 <b>Default welcome:</b>\n{safe_html(cur) or '(none)'}")
        return
    await set_default_welcome(text)
    db = get_db()
    # Keep global default text-only (GIF off) unless admins opt in per-group.
    await db.welcome_settings.update_many(
        {}, {"$set": {"custom_msg": text, "enabled": True, "send_gif": False}}
    )
    await _reply(update, f"🌐 Default welcome applied to all groups (text only):\n{safe_html(text)}")


@owner_only
async def forcewelcome_cmd(update, context):
    """Force-enable welcome messages in EVERY group. /forcewelcome <on|off>"""
    if not context.args or context.args[0].lower() not in ("on", "off"):
        await _reply(update, "Usage: /forcewelcome <on|off>")
        return
    on = context.args[0].lower() == "on"
    db = get_db()
    # Enabling welcome does not turn GIFs on — defaults stay text-only.
    r = await db.welcome_settings.update_many(
        {}, {"$set": {"enabled": on, "send_gif": False} if on else {"enabled": False}}
    )
    await _reply(update, f"✅ Welcome {'enabled (text only)' if on else 'disabled'} in "
                         f"<b>{r.modified_count}</b> groups.")


@owner_only
async def botbio_cmd(update, context):
    """Set the bot's bio / description. /botbio <text>"""
    text = " ".join(context.args)
    if not text:
        await _reply(update, "Usage: /setbio <text>")
        return
    try:
        await context.bot.set_my_description(description=text[:512])
        await context.bot.set_my_short_description(
            short_description=text[:120])
        await _reply(update, f"📝 Bio set:\n{safe_html(text)}")
    except Exception as e:
        await _reply(update, f"❌ Failed: {safe_html(str(e))}")


@owner_only
async def setmenu_cmd(update, context):
    """Set the bot's menu button to open a URL. /setmenu <url>"""
    if not context.args:
        await _reply(update, "Usage: /setmenu <url>")
        return
    url = context.args[0]
    if not url.startswith("http"):
        await _reply(update, "❌ URL must start with http(s)://")
        return
    try:
        from telegram import MenuButtonWebApp, WebAppInfo
        await context.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="🌐 Menu", web_app=WebAppInfo(url=url))
        )
        await _reply(update, f"📱 Menu button set to {safe_html(url)}")
    except Exception as e:
        await _reply(update, f"❌ Failed: {safe_html(str(e))}")


# ═════════════════════════════════════════════════════════════════════════
# SYSTEM 11 — NOTIFICATIONS
# ═════════════════════════════════════════════════════════════════════════

@owner_only
async def logchat_cmd(update, context):
    """Set the channel/group that receives owner event logs. /logchat <id>"""
    if not context.args or not context.args[0].lstrip('-').isdigit():
        cur = await get_log_chat()
        await _reply(update, f"📣 Current log chat: <code>{cur or '(none)'}</code>\n"
                             f"Usage: /logchat <chat_id>")
        return
    cid = int(context.args[0])
    await set_log_chat(cid)
    await _reply(update, f"📣 Log chat set to <code>{cid}</code>.")


@owner_only
async def notify_cmd(update, context):
    """Toggle owner event notifications. /notify <on|off>"""
    if not context.args or context.args[0].lower() not in ("on", "off"):
        cur = await get_notify()
        await _reply(update, f"🔔 Notifications: {'on' if cur else 'off'}\n"
                             f"Usage: /notify <on|off>")
        return
    on = context.args[0].lower() == "on"
    await set_notify(on)
    await _reply(update, f"🔔 Notifications {'enabled' if on else 'disabled'}.")


@owner_only
async def alert_cmd(update, context):
    """Send an owner alert to the configured log chat (or to you).
    /alert <message>"""
    text = " ".join(context.args)
    if not text:
        await _reply(update, "Usage: /alert <message>")
        return
    cid = await get_log_chat()
    target = cid or update.effective_chat.id
    sent = await safe_call(
        lambda: context.bot.send_message(
            target, f"🚨 <b>Owner Alert</b>\n\n{safe_html(text)}", parse_mode="HTML"),
        label="alert",
    )
    if sent:
        await _reply(update, "🚨 Alert sent.")
    else:
        await _reply(update, "❌ Failed to send alert (no log chat set & DM unavailable).")


@owner_only
async def ownersys_cmd(update, context):
    """Show the new owner-systems command index."""
    await _reply(update,
        "👑 <b>Owner Systems — NEW</b>\n\n"
        "🛡️ <b>Shield:</b> /lockdown /globalunlock /slowall /lockall /unlockall /shieldstatus\n"
        "🛡️ <b>Anti-Abuse:</b> /massban /massunban /banfrom /unbanfrom /cleanbots "
        "/botgate /allowedbots /watchuser /unwatch /watchlist /suslist\n"
        "⏰ <b>Schedule:</b> /schedbroadcast /schedmsg /remindall /scheds /cancelsched\n"
        "🤖 <b>Auto:</b> /autoreply /autoreplies /delautoreply /blackword /blackwords /delblackword\n"
        "📊 <b>Analytics:</b> /growth /retention /latency /health /pingall /deadgroups "
        "/online /commandstats /errorlog\n"
        "🧑‍💼 <b>Staff:</b> /sudoadd /sudoremove /stafflist /handover /whereis /common\n"
        "💰 <b>Economy:</b> /economystats /rain /reseteco\n"
        "🗄️ <b>Data:</b> /dbstats /exportcsv /backup /vacuum /indexes\n"
        "🧠 <b>AI/Content:</b> /persona /defaultwelcome /forcewelcome /botbio /setmenu\n"
        "🔔 <b>Notify:</b> /logchat /notify /alert"
    )


# ═════════════════════════════════════════════════════════════════════════
# PASSIVE HANDLERS (enforced automatically)
# ═════════════════════════════════════════════════════════════════════════

async def autoreply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-reply to messages matching a global rule (owner-configured)."""
    msg = update.effective_message
    if not msg or not msg.text:
        return
    try:
        rules = await _cached(_AR_CACHE, list_autoreplies, ttl=60)
        if not rules:
            return
        low = msg.text.lower()
        for r in rules:
            if r["trigger"].lower() in low:
                await safe_call(
                    lambda: msg.reply_html(r["response"]),
                    label="autoreply",
                )
                return
    except Exception:
        pass


async def blackword_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete group messages that contain a global blacklist word (needs admin)."""
    msg = update.effective_message
    if not msg or not msg.text or not msg.chat or msg.chat.type == "private":
        return
    try:
        ws = await _cached(_BW_CACHE, list_blackwords, ttl=60)
        if not ws:
            return
        low = msg.text.lower()
        hit = any(w["word"] in low for w in ws)
        if not hit:
            return
        me = await context.bot.get_me()
        m = await context.bot.get_chat_member(msg.chat.id, me.id)
        if m.status not in ("administrator", "creator"):
            return
        await safe_call(lambda: context.bot.delete_message(msg.chat.id, msg.message_id),
                        label="blackword.delete")
    except Exception:
        pass


async def botgate_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-kick bots joining groups based on the bot-gate policy."""
    msg = update.effective_message
    if not msg or not msg.new_chat_members:
        return
    mode = await get_botgate_mode()
    if mode == "off":
        return
    allowed = set(await list_allowed_bots())
    me = await context.bot.get_me()
    for member in msg.new_chat_members:
        if not member.is_bot or member.id == me.id:
            continue
        uname = (member.username or "").lower()
        try:
            if mode == "deny":
                await context.bot.ban_chat_member(msg.chat.id, member.id)
                await context.bot.unban_chat_member(
                    msg.chat.id, member.id, only_if_banned=True)
            elif mode == "allow" and uname not in allowed:
                await context.bot.ban_chat_member(msg.chat.id, member.id)
                await context.bot.unban_chat_member(
                    msg.chat.id, member.id, only_if_banned=True)
        except Exception:
            pass


# ── Scheduling runner (called as a background job from bot.py) ────────────

async def run_scheduler_iteration(bot):
    """Execute any due scheduled jobs. Safe to call on a loop."""
    try:
        jobs = await get_due_jobs()
        for j in jobs:
            try:
                p = j.get("payload", {})
                if j["kind"] == "broadcast":
                    users = await get_broadcastable_users()
                    for u in users:
                        try:
                            await bot.send_message(
                                u["_id"],
                                f"📢 <b>Broadcast</b>\n\n{safe_html(p.get('text',''))}",
                                parse_mode="HTML")
                            await asyncio.sleep(0.05)
                        except Exception:
                            pass
                elif j["kind"] == "group_msg":
                    await safe_call(
                        lambda: bot.send_message(
                            p["gid"], safe_html(p.get("text", "")), parse_mode="HTML"),
                        label="sched.group_msg")
                elif j["kind"] == "greetall":
                    groups = await get_all_groups()
                    for g in groups:
                        await safe_call(
                            lambda: bot.send_message(
                                g["_id"], safe_html(p.get("text", "")), parse_mode="HTML"),
                            label="sched.greetall")
                        await asyncio.sleep(0.08)
                await mark_job_done(j["_id"])
            except Exception as e:
                await log_error_entry(f"scheduler job {j.get('_id')}: {e}")
                try:
                    await mark_job_done(j["_id"])
                except Exception:
                    pass
    except Exception as e:
        await log_error_entry(f"scheduler loop: {e}")
