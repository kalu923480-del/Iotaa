"""
Iota Admin Handler
. or ! prefix commands
Iota-style promote levels with titles
"""
import re, time, logging
logger = logging.getLogger(__name__)
from telegram import Update, ChatPermissions, ChatAdministratorRights
from telegram.ext import ContextTypes
from telegram.error import TelegramError
from utils.mongo_db import (add_warning, get_warnings, remove_last_warning,
                             count_warnings, track_promotion,
                             remove_promotion,
                             get_user_by_username)
from utils.helpers import ts, mention, parse_duration, is_admin, promote_with_rights
from utils.safe_html import safe_html
from utils.fonts import sc
from config import OWNER_ID

PROMOTE_TITLES = {
    "1": ("🥉", "Junior Admin", {"can_delete_messages": True,
                                  "can_restrict_members": True}),
    "2": ("🥈", "Senior Admin", {"can_delete_messages": True,
                                  "can_restrict_members": True,
                                  "can_invite_users": True,
                                  "can_pin_messages": True,
                                  "can_change_info": True}),
    "3": ("🥇", "Full Admin",   {"can_delete_messages": True,
                                  "can_restrict_members": True,
                                  "can_invite_users": True,
                                  "can_pin_messages": True,
                                  "can_change_info": True,
                                  "can_promote_members": True,
                                  "can_manage_chat": True,
                                  "can_manage_video_chats": True}),
}

POWER_MAP = {
    "delete": "can_delete_messages", "restrict": "can_restrict_members",
    "invite": "can_invite_users",    "pin": "can_pin_messages",
    "info": "can_change_info",       "promote": "can_promote_members",
    "manage": "can_manage_chat",     "video": "can_manage_video_chats",
}

async def dot_admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    text = msg.text or ""
    m = re.match(r'^[.!](\w+)\s?(.*)', text, re.DOTALL)
    if not m: return
    cmd  = m.group(1).lower()
    rest = m.group(2).strip()
    if cmd == "help": await _help(update); return
    if cmd == "report":
        await _report(update, context, rest); return
    if cmd == "imute":
        if not await is_admin(update, context) and update.effective_user.id != OWNER_ID:
            await msg.reply_html("❌ Admins only!"); return
        await _imute(update, context, rest); return

    # 🆕 OWNER BYPASS: the bot owner can run ANY dot-admin command in ANY
    # group Iota is in — including .promote at any level — even if the
    # owner themselves isn't an admin there. This only works if Iota
    # herself already has admin rights with promote permission in that
    # group; if she doesn't, Telegram's own API will reject the actual
    # promote_chat_member call below with a clear TelegramError, so this
    # bypass can never grant more power than the bot actually has.
    is_owner_call = update.effective_user.id == OWNER_ID
    if not is_owner_call and not await is_admin(update, context):
        await msg.reply_html("❌ Admins only!"); return
    dispatch = {
        "warn":      _warn,
        "unwarn":    _unwarn,
        "warns":     _warns,
        "mute":      lambda u,c,r: _mute(u,c,r,False),
        "dmute":     lambda u,c,r: _mute(u,c,r,True),
        "unmute":    _unmute,
        "ban":       lambda u,c,r: _ban(u,c,r,False),
        "dban":      lambda u,c,r: _ban(u,c,r,True),
        "unban":     _unban,
        "kick":      _kick,
        "promote":   _promote,
        "demote":    _demote,
        "unpromote": _demote,          # 🆕 alias — ".unpromote" now works
        "demote_all":lambda u,c,r: _demote_all(u,c),
        "add":       _add_power,
        "remove":    _remove_power,
        "title":     _title,
        "pin":       lambda u,c,r: _pin(u,c),
        "unpin":     lambda u,c,r: _unpin(u,c),
        "d":         lambda u,c,r: _delete(u,c),
        # ── 🆕 10 New Admin Commands ─────────────────────────────────────────
        "adminlist": lambda u,c,r: _adminlist(u,c),
        "report":    _report,
        "clearwarn": _clearwarn,
        "warnlimit": _warnlimit,
        "tmute":     _tmute,
        "tban":      _tban,
        "note":      _note,
        "notes":     lambda u,c,r: _notes(u,c),
        "delnote":   _delnote,
        "clearnotes":lambda u,c,r: _clearnotes(u,c),
        "purge":     _purge,
    }
    fn = dispatch.get(cmd)
    if fn: await fn(update, context, rest)

async def _resolve(update, context, rest: str):
    """
    Resolve the target user from a reply or a "@username"/"id" argument.

    🔴 FIX: Previously, for a bare @username (not a reply), this ONLY
    tried context.bot.get_chat(f"@{t}") and gave up entirely if it
    failed — returning (None, None, extra), which every caller shows as
    "❌ Specify a user!". get_chat("@username") fails very often in
    practice (Telegram hasn't cached that username yet, the user has
    strict privacy settings, or it's simply a transient API hiccup),
    even though the user is completely real and known to this bot.
    This is exactly the bug behind ".unmute @Boobies_007" failing while
    a reply-based unmute works fine.

    Now falls back to the bot's own MongoDB user records (populated by
    ensure_user() on every message) when get_chat fails, so admin
    commands work reliably by @username even when Telegram's own
    lookup doesn't cooperate.
    """
    msg = update.effective_message; parts = rest.split()
    if msg.reply_to_message and msg.reply_to_message.from_user:
        u = msg.reply_to_message.from_user
        return u.id, mention(u), parts
    if not parts: return None, None, []
    t = parts[0].lstrip("@"); extra = parts[1:]
    if t.isdigit():
        uid = int(t)
        try:
            mb = await context.bot.get_chat_member(update.effective_chat.id, uid)
            return uid, mention(mb.user), extra
        except Exception:
            return uid, f"User {uid}", extra
    else:
        try:
            ch = await context.bot.get_chat(f"@{t}")
            return ch.id, f'<a href="tg://user?id={ch.id}">{ch.first_name}</a>', extra
        except Exception:
            pass
        # Fallback: look the username up in our own DB (works even when
        # Telegram's get_chat can't resolve it).
        try:
            u = await get_user_by_username(t)
            if u:
                uid = u["_id"]
                name = u.get("full_name") or u.get("username") or f"User {uid}"
                return uid, f'<a href="tg://user?id={uid}">{name}</a>', extra
        except Exception:
            pass
        return None, None, extra

async def _warn(update, context, rest):
    msg = update.effective_message; chat = update.effective_chat
    uid, uname, extra = await _resolve(update, context, rest)
    if not uid: await msg.reply_html("❌ Specify a user!"); return
    reason = " ".join(extra) or "No reason"
    await add_warning(uid, chat.id, reason, update.effective_user.id)
    count = await count_warnings(uid, chat.id)
    limit = await get_warn_limit(chat.id)
    if count >= limit:
        err = await _bot_perm_error(context, chat, "can_restrict_members")
        if err: await msg.reply_html(err); return
        bad = _is_target_invalid(uid, context, await _creator_of(context, chat))
        if bad: await msg.reply_html(bad); return
        try:
            await context.bot.ban_chat_member(chat.id, uid)
            await get_db().warnings.delete_many({"user_id": uid, "chat_id": chat.id})
            await msg.reply_html(f"⛔ {uname} <b>{sc('Banned')}!</b> ({count} {sc('warnings reached')})")
        except TelegramError as e:
            await msg.reply_html(_fmt_err(e))
    else:
        await msg.reply_html(f"⚠️ {uname} {sc('Warned!')} (<b>{count}/{limit}</b>)\n{sc('Reason')}: {safe_html(reason)}")

async def _unwarn(update, context, rest):
    msg = update.effective_message; chat = update.effective_chat
    uid, uname, _ = await _resolve(update, context, rest)
    if not uid: await msg.reply_html("❌ Specify a user!"); return
    if await remove_last_warning(uid, chat.id):
        c = await count_warnings(uid, chat.id)
        await msg.reply_html(f"✅ {sc('Warning Removed From')} {uname}. {sc('Now')}: <b>{c}/3</b>")
    else:
        await msg.reply_html(f"❓ {uname} {sc('has no warnings!')}")

async def _warns(update, context, rest):
    msg = update.effective_message; chat = update.effective_chat
    uid, uname, _ = await _resolve(update, context, rest)
    if not uid: await msg.reply_html("❌ Specify a user!"); return
    rows = await get_warnings(uid, chat.id)
    if not rows: await msg.reply_html(f"✅ {uname} {sc('has no warnings!')}"); return
    text = f"⚠️ <b>{sc('Warnings for')} {uname}:</b>\n\n"
    for i, w in enumerate(rows, 1):
        t = time.strftime("%d/%m/%Y", time.localtime(w.get("warned_at",0)))
        text += f"{i}. {safe_html(w.get('reason','?'))} ({t})\n"
    await msg.reply_html(text)

async def _mute(update, context, rest, delete=False):
    msg = update.effective_message; chat = update.effective_chat
    if delete and msg.reply_to_message:
        try: await msg.reply_to_message.delete()
        except Exception: pass
    uid, uname, extra = await _resolve(update, context, rest)
    if not uid: await msg.reply_html("❌ Specify a user!"); return
    err = await _bot_perm_error(context, chat, "can_restrict_members")
    if err: await msg.reply_html(err); return
    dur_str = extra[0] if extra else ""
    secs = parse_duration(dur_str)
    perms = ChatPermissions(can_send_messages=False)
    try:
        if secs:
            from datetime import datetime, timezone
            dt = datetime.fromtimestamp(ts()+secs, tz=timezone.utc)
            await context.bot.restrict_chat_member(chat.id, uid, perms, until_date=dt)
            await msg.reply_html(f"🔇 {uname} {sc('Muted For')} <b>{dur_str}</b>!")
        else:
            await context.bot.restrict_chat_member(chat.id, uid, perms)
            await msg.reply_html(f"🔇 {uname} {sc('Muted Permanently')}!")
    except TelegramError as e:
        await msg.reply_html(_fmt_err(e))

async def _imute(update, context, rest):
    """Mute media only (images, stickers, GIFs)."""
    msg = update.effective_message; chat = update.effective_chat
    uid, uname, extra = await _resolve(update, context, rest)
    if not uid: await msg.reply_html("❌ Specify a user!"); return
    err = await _bot_perm_error(context, chat, "can_restrict_members")
    if err: await msg.reply_html(err); return
    dur_str = extra[0] if extra else ""
    secs = parse_duration(dur_str)
    perms = ChatPermissions(
        can_send_messages=True,
        can_send_photos=False,
        can_send_videos=False,
        can_send_audios=False,
        can_send_documents=False,
        can_send_video_notes=False,
        can_send_voice_notes=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
    )
    try:
        if secs:
            from datetime import datetime, timezone
            dt = datetime.fromtimestamp(ts()+secs, tz=timezone.utc)
            await context.bot.restrict_chat_member(chat.id, uid, perms, until_date=dt)
            await msg.reply_html(f"🖼️ {uname} {sc('Media Muted For')} <b>{dur_str}</b>!")
        else:
            await context.bot.restrict_chat_member(chat.id, uid, perms)
            await msg.reply_html(f"🖼️ {uname} {sc('Media Muted Permanently')}!")
    except TelegramError as e:
        await msg.reply_html(_fmt_err(e))

async def _unmute(update, context, rest):
    msg = update.effective_message; chat = update.effective_chat
    uid, uname, _ = await _resolve(update, context, rest)
    if not uid: await msg.reply_html("❌ Specify a user!"); return
    err = await _bot_perm_error(context, chat, "can_restrict_members")
    if err: await msg.reply_html(err); return
    perms = ChatPermissions(
        can_send_messages=True,
        can_send_photos=True, can_send_videos=True, can_send_audios=True,
        can_send_documents=True, can_send_video_notes=True, can_send_voice_notes=True,
        can_send_polls=True, can_send_other_messages=True,
        can_add_web_page_previews=True, can_invite_users=True
    )
    try:
        await context.bot.restrict_chat_member(chat.id, uid, perms)
        await msg.reply_html(f"🔊 {uname} {sc('Unmuted')}!")
    except TelegramError as e:
        await msg.reply_html(_fmt_err(e))

async def _ban(update, context, rest, delete=False):
    msg = update.effective_message; chat = update.effective_chat
    if delete and msg.reply_to_message:
        try: await msg.reply_to_message.delete()
        except Exception: pass
    uid, uname, extra = await _resolve(update, context, rest)
    if not uid: await msg.reply_html("❌ Specify a user!"); return
    err = await _bot_perm_error(context, chat, "can_restrict_members")
    if err: await msg.reply_html(err); return
    bad = _is_target_invalid(uid, context, await _creator_of(context, chat))
    if bad: await msg.reply_html(bad); return
    reason = " ".join(extra) or "No reason"
    try:
        await context.bot.ban_chat_member(chat.id, uid)
        await msg.reply_html(f"⛔ {uname} <b>{sc('Banned')}!</b>\n{sc('Reason')}: {safe_html(reason)}")
    except TelegramError as e:
        await msg.reply_html(_fmt_err(e))

async def _unban(update, context, rest):
    msg = update.effective_message; chat = update.effective_chat
    uid, uname, _ = await _resolve(update, context, rest)
    if not uid: await msg.reply_html("❌ Specify a user!"); return
    err = await _bot_perm_error(context, chat, "can_restrict_members")
    if err: await msg.reply_html(err); return
    try:
        await context.bot.unban_chat_member(chat.id, uid, only_if_banned=True)
        await msg.reply_html(f"✅ {uname} <b>{sc('Unbanned')}!</b>")
    except TelegramError as e:
        await msg.reply_html(_fmt_err(e))

async def _kick(update, context, rest):
    msg = update.effective_message; chat = update.effective_chat
    uid, uname, _ = await _resolve(update, context, rest)
    if not uid: await msg.reply_html("❌ Specify a user!"); return
    err = await _bot_perm_error(context, chat, "can_restrict_members")
    if err: await msg.reply_html(err); return
    bad = _is_target_invalid(uid, context, await _creator_of(context, chat))
    if bad: await msg.reply_html(bad); return
    try:
        await context.bot.ban_chat_member(chat.id, uid)
        await context.bot.unban_chat_member(chat.id, uid)
        await msg.reply_html(f"👢 {uname} <b>{sc('Kicked')}!</b>")
    except TelegramError as e:
        await msg.reply_html(_fmt_err(e))

def _build_rights(**overrides) -> ChatAdministratorRights:
    """
    🔴 FIX: ChatAdministratorRights requires ALL of these fields to be
    explicitly passed (is_anonymous, can_manage_chat, can_delete_messages,
    can_manage_video_chats, can_restrict_members, can_promote_members,
    can_change_info, can_invite_users) — none of them have a default
    value in python-telegram-bot. Every single call site in this file
    was only passing a partial dict (e.g. just {"can_manage_chat": True}),
    which raises `TypeError: missing N required positional arguments`
    on EVERY .promote/.demote/.add/.remove call — an uncaught TypeError
    (not a TelegramError, so the existing `except TelegramError` never
    catches it), meaning the command just silently did nothing at all.
    This helper fills every required field with a safe False default,
    then applies whatever specific rights the caller wants on top.
    """
    base = dict(
        is_anonymous=False, can_manage_chat=False, can_delete_messages=False,
        can_manage_video_chats=False, can_restrict_members=False,
        can_promote_members=False, can_change_info=False, can_invite_users=False,
        can_post_messages=False, can_edit_messages=False, can_pin_messages=False,
        can_post_stories=False, can_edit_stories=False, can_delete_stories=False,
        can_manage_topics=False,
    )
    base.update(overrides)
    return ChatAdministratorRights(**base)


async def _bot_rights(context, chat):
    """Return the bot's own chat-member object (has can_* rights directly
    in this PTB version) so we can avoid granting rights it lacks."""
    try:
        return await context.bot.get_chat_member(chat.id, context.bot.id)
    except Exception:
        return None

_RIGHT_ATTRS = (
    "is_anonymous", "can_manage_chat", "can_delete_messages",
    "can_manage_video_chats", "can_restrict_members", "can_promote_members",
    "can_change_info", "can_invite_users", "can_post_messages",
    "can_edit_messages", "can_pin_messages", "can_post_stories",
    "can_edit_stories", "can_delete_stories", "can_manage_topics",
)

def _cap_rights(rights, my_rights):
    """
    Drop any right the BOT itself cannot grant. Telegram raises
    Right_forbidden if you try to promote someone with a permission the
    bot account doesn't have — e.g. Full Admin's can_promote_members, or
    Admin's can_manage_chat. Capping to the bot's actual powers avoids
    that hard error and still grants everything the bot is allowed to.
    Returns (new ChatAdministratorRights, dropped: bool).

    ChatAdministratorRights is immutable, so we build a NEW object.
    """
    if my_rights is None:
        return rights, False
    dropped = False
    kw = {}
    for a in _RIGHT_ATTRS:
        val = getattr(rights, a)
        if val and not getattr(my_rights, a, False):
            kw[a] = False
            dropped = True
        else:
            kw[a] = val
    return ChatAdministratorRights(**kw), dropped


async def _bot_member(context, chat):
    """Return the bot's own ChatMember in this chat, or None on failure."""
    try:
        return await context.bot.get_chat_member(chat.id, context.bot.id)
    except Exception:
        return None


async def _creator_of(context, chat):
    """Return the user id of the chat creator (owner), or None if unknown."""
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
    except Exception:
        return None
    for a in admins:
        if a.status == "creator":
            return a.user.id
    return None


async def _bot_perm_error(context, chat, *needed):
    """
    Return a friendly error string if the bot itself cannot perform an
    admin action in this chat, else None.

    This is what prevents the raw Telegram failures behind the reported
    bugs:
      - "Chat_admin_required"  -> the bot isn't an admin at all
      - "Right_forbidden"      -> bot is admin but lacks the needed right
    We surface a clear message instead of letting Telegram error out.
    """
    me = await _bot_member(context, chat)
    if me is None:
        return ("❌ I couldn't read my own status here. Add me to this "
                "group as an admin (with the 'Add Admins' right) and retry.")
    if me.status in ("left", "kicked"):
        return ("❌ I'm not in this group. Add me as an admin (with the "
                "'Add Admins' right) first, then I can manage admins.")
    if me.status not in ("administrator", "creator"):
        return ("❌ Make me an admin in THIS group first (turn ON the "
                "'Add Admins' right too) — I only work where I'm an admin "
                "with that right.")
    names = {
        "can_promote_members":   "'Add New Admins'",
        "can_change_info":       "'Change Info'",
        "can_pin_messages":      "'Pin Messages'",
        "can_delete_messages":   "'Delete Messages'",
        "can_restrict_members":  "'Restrict Users'",
        "can_invite_users":      "'Invite Users'",
        "can_manage_chat":       "'Manage Chat'",
        "can_manage_video_chats":"'Manage Video Chats'",
    }
    for right in needed:
        if not getattr(me, right, False):
            return (f"❌ I'm already an admin in THIS group, but the "
                    f"{names.get(right, right)} permission is OFF for me. "
                    f"Turn it ON in this group's admin settings.")
    return None


def _is_target_invalid(uid, context, creator):
    """Return a friendly error string if uid is a forbidden target
    (the group owner or the bot itself), else None."""
    if creator and uid == creator:
        return "❌ You can't promote or demote the group owner!"
    if uid == context.bot.id:
        return ("❌ You replied to ME (the bot). Reply to the actual member "
                "you want to promote/demote instead.")
    return None


def _fmt_err(e):
    """
    Convert a raw Telegram API error into a clean, actionable message so
    the bot never dumps cryptic strings like "Chat_admin_required" or
    "Can't remove chat owner" at the user. These raw strings are what the
    user was seeing; we now translate them into plain instructions.
    """
    raw = str(e).strip()
    low = raw.lower()
    if "chat_admin_required" in low:
        return ("❌ Make me an admin in this group first (give me the "
                "'Add Admins' right too), then I can manage admins.")
    if "owner" in low:
        return "❌ You can't promote or demote the group owner!"
    if "not_promoted_by_bot" in low or "usernotpromotedbybot" in low:
        return ("❌ That admin was added by someone else, so I can't manage "
                "them directly here. Ask a group admin with the 'Add Admins' "
                "right to let me handle them, or use .demote_all.")
    if "user_admin_invalid" in low:
        return "❌ I can't change that user's admin status."
    if "right_forbidden" in low or "not enough rights" in low:
        return ("❌ I'm missing a required permission — ask a group admin "
                "to grant me the needed right.")
    if "not in the chat" in low or "user not found" in low:
        return "❌ That user isn't in this chat."
    return f"❌ {safe_html(raw)}"


async def _promote(update, context, rest):
    msg = update.effective_message; chat = update.effective_chat
    is_owner = update.effective_user.id == OWNER_ID
    # ── Parse level (1/2/3) out of the args so ".promote 1" (as a reply)
    #    sets level 1 instead of being misread as target user id 1. ──
    tokens = rest.split()
    level = "1"
    for i, t in enumerate(tokens):
        if t in ("1", "2", "3"):
            level = t
            tokens.pop(i)
            break
    uid, uname, _ = await _resolve(update, context, " ".join(tokens))
    # ── Owner convenience: ".promote" with no target makes the OWNER an
    #    admin of this group (provided the bot has the Add-Admins right),
    #    even if the owner is only a normal member here. ──
    if not uid and is_owner:
        uid = update.effective_user.id
        uname = mention(update.effective_user)
    if not uid: await msg.reply_html("❌ Specify a user!"); return
    # ── Bot must be an admin with add-admins right (owner may still try
    #    directly — getChatMember can report stale data). ──
    err = await _bot_perm_error(context, chat, "can_promote_members")
    if err and not is_owner:
        await msg.reply_html(err); return
    # ── Can't promote the owner or the bot itself ──
    creator = await _creator_of(context, chat)
    # Owner self-promote (".promote" with no target) is intended; don't
    # block it on the "can't promote the group owner" rule even if the
    # owner is also the group creator (they're already an admin, so the
    # promote is a harmless idempotent no-op).
    if not (is_owner and uid == OWNER_ID):
        bad = _is_target_invalid(uid, context, creator)
        if bad: await msg.reply_html(bad); return
    # ── Target must not already be the owner; skip if already admin ──
    info = PROMOTE_TITLES.get(level, PROMOTE_TITLES["1"])
    medal, title_name, rights_kw = info
    try:
        my = await _bot_rights(context, chat)
        rights = _build_rights(**rights_kw)
        rights, dropped = _cap_rights(rights, my)
        await promote_with_rights(context.bot, chat.id, uid, rights)
        try:
            await context.bot.set_chat_administrator_custom_title(chat.id, uid, title_name)
        except Exception: pass
        await track_promotion(uid, chat.id, update.effective_user.id)
        out = f"{uname} {sc('Promoted To')} {medal} {sc(title_name)}."
        if dropped:
            out += f"\n⚠️ {sc('Some rights skipped — I lack permission')}"
        await msg.reply_html(out)
    except Exception as e:
        logger.exception("promote failed")
        await msg.reply_html(_fmt_err(e))


async def _demote(update, context, rest):
    msg = update.effective_message; chat = update.effective_chat
    is_owner = update.effective_user.id == OWNER_ID
    uid, uname, _ = await _resolve(update, context, rest)
    if not uid: await msg.reply_html("❌ Specify a user!"); return
    # ── Bot must be an admin with add-admins right (owner may still try
    #    directly — getChatMember can report stale data). ──
    err = await _bot_perm_error(context, chat, "can_promote_members")
    if err and not is_owner:
        await msg.reply_html(err); return
    # ── Can't demote the owner or the bot itself ──
    creator = await _creator_of(context, chat)
    bad = _is_target_invalid(uid, context, creator)
    if bad: await msg.reply_html(bad); return
    # ── Target must actually be an admin here ──
    try:
        cm = await context.bot.get_chat_member(chat.id, uid)
        if cm.status not in ("administrator", "creator"):
            await msg.reply_html(f"❌ {uname} {sc('is not an admin here!')}"); return
    except Exception as e:
        await msg.reply_html(_fmt_err(e)); return
    rights = _build_rights()
    try:
        await promote_with_rights(context.bot, chat.id, uid, rights)
        await remove_promotion(uid, chat.id)
        await msg.reply_html(f"⬇️ {uname} <b>{sc('Demoted')}!</b>")
    except Exception as e:
        logger.exception("demote failed")
        await msg.reply_html(_fmt_err(e))

async def _demote_all(update, context):
    """
    Demote EVERY admin in the group — not just the ones Iota herself
    promoted. We now read the live admin list from Telegram
    (get_chat_administrators) instead of the local promotion-tracking
    collection, so admins added by the group owner or by other admins are
    demoted too. The only members we never touch are the group creator
    (Telegram forbids demoting the owner) and Iota herself.
    """
    msg = update.effective_message; chat = update.effective_chat
    err = await _bot_perm_error(context, chat, "can_promote_members")
    if err: await msg.reply_html(err); return
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
    except Exception as e:
        await msg.reply_html(_fmt_err(e)); return
    rights = _build_rights()
    done = 0; skipped = 0
    for a in admins:
        uid = a.user.id
        if uid == context.bot.id:
            continue  # never demote Iota herself
        if a.status == "creator":
            continue  # the group owner can't be demoted (Telegram limit)
        try:
            await promote_with_rights(context.bot, chat.id, uid, rights)
            await remove_promotion(uid, chat.id)
            done += 1
        except Exception:
            skipped += 1
    out = f"✅ {sc('Demoted')} <b>{done}</b> {sc('admins')}."
    if skipped:
        out += f"\n⚠️ {skipped} {sc('could not be demoted')} (missing rights)."
    await msg.reply_html(out)

async def _add_power(update, context, rest):
    msg = update.effective_message; chat = update.effective_chat
    uid, uname, extra = await _resolve(update, context, rest)
    if not uid or not extra:
        await msg.reply_html(f"❌ {sc('Usage')}: .add [user] &lt;power&gt;\n{sc('Powers')}: {' | '.join(POWER_MAP.keys())}"); return
    perm = POWER_MAP.get(extra[0].lower())
    if not perm: await msg.reply_html(f"❌ {sc('Unknown power!')}"); return
    err = await _bot_perm_error(context, chat, "can_promote_members")
    if err: await msg.reply_html(err); return
    bad = _is_target_invalid(uid, context, await _creator_of(context, chat))
    if bad: await msg.reply_html(bad); return
    try:
        cm = await context.bot.get_chat_member(chat.id, uid)
        my = await _bot_rights(context, chat)
        rights = _build_rights(
            can_manage_chat=True,
            can_delete_messages=bool(cm.can_delete_messages),
            can_restrict_members=bool(cm.can_restrict_members),
            can_invite_users=bool(cm.can_invite_users),
            can_pin_messages=bool(cm.can_pin_messages),
            can_change_info=bool(cm.can_change_info),
            can_promote_members=bool(cm.can_promote_members),
            can_manage_video_chats=bool(cm.can_manage_video_chats),
            **{perm: True},
        )
        rights, dropped = _cap_rights(rights, my)
        await promote_with_rights(context.bot, chat.id, uid, rights)
        out = f"✅ {sc('Added')} <b>{extra[0]}</b> {sc('power to')} {uname}!"
        if dropped:
            out += f"\n⚠️ {sc('Some rights skipped — I lack permission')}"
        await msg.reply_html(out)
    except Exception as e:
        logger.exception("add_power failed")
        await msg.reply_html(_fmt_err(e))

async def _remove_power(update, context, rest):
    msg = update.effective_message; chat = update.effective_chat
    uid, uname, extra = await _resolve(update, context, rest)
    if not uid or not extra:
        await msg.reply_html(f"❌ {sc('Usage')}: .remove [user] &lt;power&gt;"); return
    perm = POWER_MAP.get(extra[0].lower())
    if not perm: await msg.reply_html(f"❌ {sc('Unknown power!')}"); return
    err = await _bot_perm_error(context, chat, "can_promote_members")
    if err: await msg.reply_html(err); return
    bad = _is_target_invalid(uid, context, await _creator_of(context, chat))
    if bad: await msg.reply_html(bad); return
    try:
        cm = await context.bot.get_chat_member(chat.id, uid)
        my = await _bot_rights(context, chat)
        rights = _build_rights(
            can_manage_chat=True,
            can_delete_messages=bool(cm.can_delete_messages),
            can_restrict_members=bool(cm.can_restrict_members),
            can_invite_users=bool(cm.can_invite_users),
            can_pin_messages=bool(cm.can_pin_messages),
            can_change_info=bool(cm.can_change_info),
            can_promote_members=bool(cm.can_promote_members),
            can_manage_video_chats=bool(cm.can_manage_video_chats),
            **{perm: False},
        )
        rights, dropped = _cap_rights(rights, my)
        await promote_with_rights(context.bot, chat.id, uid, rights)
        out = f"✅ {sc('Removed')} <b>{extra[0]}</b> {sc('from')} {uname}!"
        if dropped:
            out += f"\n⚠️ {sc('Some rights skipped — I lack permission')}"
        await msg.reply_html(out)
    except Exception as e:
        logger.exception("remove_power failed")
        await msg.reply_html(_fmt_err(e))

async def _title(update, context, rest):
    msg = update.effective_message; chat = update.effective_chat
    uid, uname, extra = await _resolve(update, context, rest)
    if not uid: await msg.reply_html("❌ Specify a user!"); return
    # Setting an admin title needs the bot to have the add-admins right.
    err = await _bot_perm_error(context, chat, "can_promote_members")
    if err: await msg.reply_html(err); return
    bad = _is_target_invalid(uid, context, await _creator_of(context, chat))
    if bad: await msg.reply_html(bad); return
    title = " ".join(extra)
    try:
        await context.bot.set_chat_administrator_custom_title(chat.id, uid, title)
        await msg.reply_html(f"🏷️ {uname}'s {sc('title')}: <b>{safe_html(title) or '(none)'}</b>")
    except Exception as e:
        logger.exception("title failed")
        await msg.reply_html(_fmt_err(e))

async def _pin(update, context):
    msg = update.effective_message; chat = update.effective_chat
    if not msg.reply_to_message:
        await msg.reply_html(f"❌ {sc('Reply to a message!')}"); return
    err = await _bot_perm_error(context, chat, "can_pin_messages")
    if err: await msg.reply_html(err); return
    try:
        await msg.reply_to_message.pin()
        await msg.reply_html(f"📌 {sc('Pinned!')}") 
    except TelegramError as e:
        await msg.reply_html(_fmt_err(e))

async def _unpin(update, context):
    msg = update.effective_message; chat = update.effective_chat
    err = await _bot_perm_error(context, chat, "can_pin_messages")
    if err: await msg.reply_html(err); return
    try:
        if msg.reply_to_message:
            await context.bot.unpin_chat_message(
                chat.id, msg.reply_to_message.message_id)
        else:
            await context.bot.unpin_chat_message(chat.id)
        await msg.reply_html(f"📌 {sc('Unpinned!')}")
    except TelegramError as e:
        await msg.reply_html(_fmt_err(e))

async def _delete(update, context):
    msg = update.effective_message
    try:
        await msg.delete()
        if msg.reply_to_message:
            await msg.reply_to_message.delete()
    except Exception: pass

async def _purge(update, context, rest):
    """Delete a run of recent messages. Reply to a message + .purge N
    deletes that message and the N messages before it. Without a reply,
    .purge N deletes the command message and the N before it."""
    msg = update.effective_message; chat = update.effective_chat
    parts = rest.strip().split()
    if not parts or not parts[0].isdigit():
        await msg.reply_html(f"❌ {sc('Usage')}: .purge &lt;count&gt;  ({sc('reply optional')})"); return
    n = int(parts[0])
    if n > 100:
        await msg.reply_html(f"❌ {sc('Max')} 100 {sc('messages per purge')}!"); return
    end_id = msg.reply_to_message.message_id if msg.reply_to_message else msg.message_id
    start_id = max(1, end_id - n + 1)
    deleted = 0
    for mid in range(start_id, end_id + 1):
        try:
            await context.bot.delete_message(chat.id, mid)
            deleted += 1
        except Exception:
            pass
    try:
        await msg.delete()
    except Exception:
        pass
    if deleted:
        try:
            await context.bot.send_message(
                chat.id, f"🧹 {sc('Purged')} {deleted} {sc('messages')}."
            )
        except Exception:
            pass


async def _help(update):
    await update.effective_message.reply_html(
        f"🛡️ <b>{sc('Admin Commands')} (. or !)</b>\n\n"
        f"<code>.warns</code> [user] — {sc('All warnings')}\n"
        f"<code>.warn</code> [user] [reason] — {sc('Warn')} (3={sc('ban')})\n"
        f"<code>.unwarn</code> [user] — {sc('Remove 1 warning')}\n"
        f"<code>.mute</code> [user] [30m/2h/1d] — {sc('Mute')}\n"
        f"<code>.imute</code> [user] [time] — {sc('Mute Media Only')}\n"
        f"<code>.dmute</code> [reply] [time] — {sc('Delete + Mute')}\n"
        f"<code>.unmute</code> [user] — {sc('Unmute')}\n"
        f"<code>.ban</code> [user] [reason] — {sc('Ban')}\n"
        f"<code>.dban</code> [reply] — {sc('Delete + Ban')}\n"
        f"<code>.unban</code> [user] — {sc('Unban')}\n"
        f"<code>.kick</code> [user] — {sc('Kick')}\n"
        f"<code>.promote</code> [user] 1/2/3\n"
        f"  1={sc('Junior')} 2={sc('Admin')} 3={sc('Full')}\n"
        f"<code>.demote</code> [user]\n"
        f"<code>.demote_all</code>\n"
        f"<code>.add</code> [user] power\n"
        f"<code>.remove</code> [user] power\n"
        f"<code>.title</code> [user] title\n"
        f"<code>.pin</code> [reply] — {sc('Pin')}\n"
        f"<code>.unpin</code> — {sc('Unpin')}\n"
        f"<code>.d</code> — {sc('Delete')}\n"
        f"<code>.purge</code> [count] — {sc('Delete a run of messages')}\n\n"
        f"🆕 <b>{sc('New Commands')}:</b>\n"
        f"<code>.adminlist</code> — {sc('List all admins')}\n"
        f"<code>.report</code> [reply] — {sc('Report a user to admins')}\n"
        f"<code>.clearwarn</code> [user] — {sc('Clear all warnings')}\n"
        f"<code>.warnlimit</code> N — {sc('Set warn limit (default 3)')}\n"
        f"<code>.tmute</code> [user] [time] — {sc('Timed mute with auto-unmute')}\n"
        f"<code>.tban</code> [user] [time] — {sc('Timed ban with auto-unban')}\n"
        f"<code>.note</code> [key] [text] — {sc('Save a note')}\n"
        f"<code>.notes</code> — {sc('List all saved notes')}\n"
        f"<code>.delnote</code> [key] — {sc('Delete a note')}\n"
        f"<code>.clearnotes</code> — {sc('Clear all notes')}\n\n"
        f"{sc('Powers')}: manage|delete|restrict|invite|pin|info|promote|video"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 🆕 10 New Powerful Admin Commands
# ══════════════════════════════════════════════════════════════════════════════

from utils.mongo_db import (
    set_note, get_note, delete_note, list_notes, clear_notes,
    get_warn_limit, set_warn_limit, get_db,
)


async def _adminlist(update, context):
    """Show all current admins with their titles."""
    chat = update.effective_chat
    msg = update.effective_message
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
    except TelegramError as e:
        await msg.reply_html(_fmt_err(e)); return

    lines = []
    for a in admins:
        u = a.user
        if u.is_bot:
            continue
        name = u.full_name
        title = ""
        if hasattr(a, "custom_title") and a.custom_title:
            title = f" — <i>{safe_html(a.custom_title)}</i>"
        icon = "👑" if a.status == "creator" else "⭐"
        lines.append(f"{icon} <a href='tg://user?id={u.id}'>{safe_html(name)}</a>{title}")

    await msg.reply_html(
        f"🛡️ <b>{sc('Admin List')} — {safe_html(chat.title or 'Group')}</b>\n\n"
        + "\n".join(lines) + f"\n\n<i>{sc('Total')}: {len(lines)}</i>"
    )


async def _report(update, context, rest):
    """Reply to a message + .report to notify all admins."""
    msg = update.effective_message; chat = update.effective_chat; u = update.effective_user
    if not msg.reply_to_message:
        await msg.reply_html("📢 " + sc("Reply to the message you want to report.")); return

    try:
        admins = await context.bot.get_chat_administrators(chat.id)
    except TelegramError:
        admins = []

    # Send a ping to each admin (Telegram bots can't @mention admin
    # lists, so we tag them all in one message as inline text links).
    admin_tags = " ".join(
        f'<a href="tg://user?id={a.user.id}">.</a>'
        for a in admins if not a.user.is_bot
    )
    reason = rest.strip() or sc("No reason given")
    reported = msg.reply_to_message.from_user
    await msg.reply_html(
        f"🚨 <b>{sc('Report')}</b>\n"
        f"{mention(u)} {sc('reported')} {mention(reported)}\n"
        f"📌 {sc('Reason')}: {safe_html(reason)}\n\n"
        f"{sc('Admins pinged')}: {admin_tags}"
    )


async def _clearwarn(update, context, rest):
    """Clear all warnings for a user."""
    msg = update.effective_message
    uid, uname, _ = await _resolve(update, context, rest)
    if not uid:
        await msg.reply_html("❌ Specify a user!"); return
    chat_id = update.effective_chat.id
    r = await get_db().warnings.delete_many({"user_id": uid, "chat_id": chat_id})
    await msg.reply_html(f"🧹 {sc('All warnings cleared for')} {uname}! ({r.deleted_count})")


async def _warnlimit(update, context, rest):
    """Set max warns before auto-ban. Default is 3."""
    msg = update.effective_message; chat = update.effective_chat
    parts = rest.strip().split()
    if not parts or not parts[0].isdigit():
        lim = await get_warn_limit(chat.id)
        await msg.reply_html(f"⚠️ {sc('Current warn limit')}: <b>{lim}</b>\n{sc('Set with')}: <code>.warnlimit 5</code>"); return
    lim = max(1, min(20, int(parts[0])))
    await set_warn_limit(chat.id, lim)
    await msg.reply_html(f"✅ {sc('Warn limit set to')} <b>{lim}</b> {sc('warns before auto-ban')}.")


async def _tmute(update, context, rest):
    """Timed mute — auto-unmutes via Telegram's until_date parameter."""
    msg = update.effective_message; chat = update.effective_chat
    uid, uname, extra = await _resolve(update, context, rest)
    if not uid:
        await msg.reply_html("❌ Specify a user!"); return
    err = await _bot_perm_error(context, chat, "can_restrict_members")
    if err: await msg.reply_html(err); return
    dur_str = extra[0] if extra else "1h"
    try:
        dur = parse_duration(dur_str)
    except Exception:
        await msg.reply_html("❌ " + sc("Invalid time. Use 30m, 2h, 1d")); return
    until = int(time.time() + dur)
    try:
        await context.bot.restrict_chat_member(
            chat.id, uid,
            ChatPermissions(can_send_messages=False),
            until_date=until,
        )
        await msg.reply_html(
            f"⏰ {uname} {sc('muted for')} <b>{dur_str}</b>. "
            f"{sc('Auto-unmutes at')} {time.strftime('%H:%M', time.localtime(until))}"
        )
    except TelegramError as e:
        await msg.reply_html(_fmt_err(e))


async def _tban(update, context, rest):
    """Timed ban — auto-unbans via Telegram's until_date parameter."""
    msg = update.effective_message; chat = update.effective_chat
    uid, uname, extra = await _resolve(update, context, rest)
    if not uid:
        await msg.reply_html("❌ Specify a user!"); return
    err = await _bot_perm_error(context, chat, "can_restrict_members")
    if err: await msg.reply_html(err); return
    bad = _is_target_invalid(uid, context, await _creator_of(context, chat))
    if bad: await msg.reply_html(bad); return
    dur_str = extra[0] if extra else "1h"
    try:
        dur = parse_duration(dur_str)
    except Exception:
        await msg.reply_html("❌ " + sc("Invalid time. Use 30m, 2h, 1d")); return
    until = int(time.time() + dur)
    try:
        await context.bot.ban_chat_member(chat.id, uid, until_date=until)
        await msg.reply_html(
            f"⏰ {uname} {sc('banned for')} <b>{dur_str}</b>. "
            f"{sc('Auto-unbans at')} {time.strftime('%H:%M', time.localtime(until))}"
        )
    except TelegramError as e:
        await msg.reply_html(_fmt_err(e))


async def _note(update, context, rest):
    """Save a note: .note keyname The text content to save."""
    msg = update.effective_message; chat = update.effective_chat
    parts = rest.strip().split(None, 1)
    if len(parts) < 2:
        await msg.reply_html("📝 " + sc("Usage: .note <key> <text>")); return
    key, value = parts[0].lower(), parts[1]
    await set_note(chat.id, key, value)
    await msg.reply_html(f"📝 {sc('Note')} <b>{safe_html(key)}</b> {sc('saved!')} {sc('Show it with')} <code>.notes</code>")


async def _notes(update, context):
    """List all saved notes for this group."""
    msg = update.effective_message; chat = update.effective_chat
    keys = await list_notes(chat.id)
    if not keys:
        await msg.reply_html("📝 " + sc("No notes saved. Add with: .note <key> <text>")); return
    lines = [f"📌 <code>{safe_html(k)}</code>" for k in sorted(keys)]
    await msg.reply_html(
        f"📝 <b>{sc('Saved Notes')}</b> ({len(keys)}):\n\n" +
        "\n".join(lines) + f"\n\n{sc('Get a note')}: <code>#key</code> {sc('or')} <code>/get key</code>"
    )


async def _delnote(update, context, rest):
    """Delete one saved note."""
    msg = update.effective_message; chat = update.effective_chat
    key = rest.strip().lower()
    if not key:
        await msg.reply_html("❌ " + sc("Usage: .delnote <key>")); return
    ok = await delete_note(chat.id, key)
    if ok:
        await msg.reply_html(f"🗑️ {sc('Note')} <b>{safe_html(key)}</b> {sc('deleted.')}")
    else:
        await msg.reply_html(f"❌ {sc('No note found with key')} <b>{safe_html(key)}</b>")


async def _clearnotes(update, context):
    """Delete ALL notes for this group."""
    msg = update.effective_message; chat = update.effective_chat
    n = await clear_notes(chat.id)
    await msg.reply_html(f"🗑️ {sc('Cleared')} {n} {sc('notes.')}")
