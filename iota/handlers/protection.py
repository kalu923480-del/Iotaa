"""
Iota Group Protection System — MongoDB-backed
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Auto-detects and handles:
  • Spam floods (too many messages from one user)
  • New-member gate (restrict until member is mature)
  • Mention spam
  • Name/username filter
  • Channel posts only (selective forward block)
  • Excessive CAPS
  • Repeat message spam
  • RTL / Zalgo (invisible spam)
  • Arabic/foreign script spam
  • Link spam (non-whitelisted URLs)
  • Linkban (foreign invite / spam links)
  • Forwarded channel spam
  • New-account spam bots
  • Bot additions
  • Profanity/bad words
  • Report system (/report or @admin)
  • Anti-raid (mass join flood)
"""

import re
import asyncio
import logging
from collections import defaultdict
from telegram import Update, ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import TelegramError
from utils.mongo_db import (
    ensure_user, get_prot, update_prot, add_report, get_reports,
    get_report_count, get_bad_words, add_bad_word, remove_bad_word,
    get_name_blocklist, add_name_block, remove_name_block,
    add_link_allow, remove_link_allow, log_mod_action,
)
from utils.helpers import mention, ts, is_admin, resolve_target_chat
from utils.safe_html import safe_html
from utils.linkban import should_block_links, normalize_allow_entry, merge_linkban_settings

logger = logging.getLogger(__name__)

# ── Runtime trackers (in-memory, per-process) ───────────────────────────────
_join_times: dict = defaultdict(dict)    # {chat_id: {user_id: join_ts}}
_repeat_data: dict = defaultdict(lambda: defaultdict(list))  # {chat_id: {user_id: [(text_hash, ts), ...]}}
# Legacy flood tracking (in-memory)
_flood_data: dict = defaultdict(lambda: defaultdict(list))   # {chat_id: {user_id: [timestamps]}}
_raid_data:  dict = defaultdict(list)   # {chat_id: [join_timestamps]}

# Legacy broad pattern used only when anti_link is ON and linkban is OFF
LINK_PATTERN   = re.compile(r'(https?://|t\.me/|telegram\.me/)', re.IGNORECASE)
ARABIC_PATTERN = re.compile(r'[\u0600-\u06FF\u0750-\u077F]{5,}')
RTL_OVERRIDE   = re.compile(r'[\u202E\u202D]')
COMBINING_MARK = re.compile(r'[\u0300-\u036F\u1AB0-\u1AFF\u1DC0-\u1DFF\u20D0-\u20FF\uFE20-\uFE2F]')


# ── Pure helpers (importable without telegram) ───────────────────────────────

def word_matches(mode: str, pattern: str, text: str) -> bool:
    """Match a bad-word pattern against text using the configured mode."""
    text_lower = text.lower()
    bw = pattern.lower()
    if mode == "exact":
        return bool(re.search(rf'(?<!\w){re.escape(bw)}(?!\w)', text_lower))
    if mode == "regex":
        try:
            return bool(re.search(bw, text_lower, re.I))
        except Exception:
            return False
    return bw in text_lower


def caps_ratio(text: str) -> float:
    """Return ratio of uppercase letters to total alphabetical characters."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    upper = sum(1 for c in letters if c.isupper())
    return upper / len(letters)


def zalgo_score(text: str) -> int:
    """Count combining (invisible diacritic) marks in text."""
    return len(COMBINING_MARK.findall(text))


def count_mentions(text: str, entities) -> int:
    """Count @mentions from Telegram entities, else bare @username tokens."""
    count = 0
    if entities:
        for ent in entities:
            et = getattr(ent, "type", None)
            et_name = getattr(et, "name", str(et)).lower() if et is not None else ""
            if "text_mention" in et_name or et_name in ("mention", "text_mention"):
                count += 1
            elif et_name == "mention" or (et_name and et_name.endswith("mention")):
                count += 1
        if count:
            return count
    return len(re.findall(r"@[\w]{4,}", text or ""))


# ── Mute / ban / delete helpers ──────────────────────────────────────────────

async def _mute_user(bot, chat_id, user_id, seconds=300):
    try:
        from datetime import datetime, timezone
        until = datetime.fromtimestamp(ts() + seconds, tz=timezone.utc)
        await bot.restrict_chat_member(
            chat_id, user_id,
            ChatPermissions(can_send_messages=False),
            until_date=until
        )
    except TelegramError:
        pass


async def _ban_user(bot, chat_id, user_id):
    try:
        await bot.ban_chat_member(chat_id, user_id)
    except TelegramError:
        pass


async def _delete_msg(msg):
    try:
        await msg.delete()
    except Exception:
        pass


async def _auto_del_msg(bot, chat_id, message_id, delay=6):
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass


async def _log(bot, log_channel, text):
    if not log_channel:
        return
    try:
        await bot.send_message(log_channel, text, parse_mode="HTML")
    except Exception:
        pass


async def _enforce_action(bot, chat_id, user_id, action, seconds=300):
    if action == "mute":
        await _mute_user(bot, chat_id, user_id, seconds)


# ── Main protection message handler ──────────────────────────────────────────

async def protection_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    chat = update.effective_chat
    u    = update.effective_user

    if not msg or not u or chat.type == "private":
        return

    if u.is_bot:
        return
    if await is_admin(update, context, u.id):
        return

    prot = await get_prot(chat.id)
    if not prot.get("enabled", True):
        return

    text = msg.text or msg.caption or ""
    entities = list(msg.entities or []) + list(msg.caption_entities or [])
    try:
        for ent in entities:
            et = getattr(ent, "type", None)
            et_name = getattr(et, "name", str(et)).lower() if et is not None else ""
            if "url" in et_name or et_name in ("url", "text_link"):
                if getattr(ent, "url", None):
                    text = f"{text} {ent.url}"
                else:
                    try:
                        src = msg.text or msg.caption or ""
                        fragment = src[ent.offset: ent.offset + ent.length]
                        if fragment:
                            text = f"{text} {fragment}"
                    except Exception:
                        pass
    except Exception:
        pass
    now = ts()

    # ── New-member gate ────────────────────────────────────────────────────────
    if prot.get("newmember_gate") and u.id in _join_times.get(chat.id, {}):
        join_ts = _join_times[chat.id].get(u.id)
        if join_ts is not None:
            gate_min = int(prot.get("newmember_minutes") or 10)
            if (now - join_ts) < gate_min * 60:
                action = prot.get("newmember_action", "delete")
                if action == "mute":
                    await _mute_user(context.bot, chat.id, u.id, gate_min * 60)
                await _delete_msg(msg)
                try:
                    await log_mod_action(
                        chat.id, 0, "newmember_gate", u.id,
                        reason=f"restricted for {gate_min}m after join"
                    )
                except Exception:
                    pass
                return

    # ── Anti-flood ────────────────────────────────────────────────────────────
    if prot.get("anti_flood", True):
        window = prot.get("flood_window", 5)
        limit  = prot.get("flood_limit", 5)
        times  = _flood_data[chat.id][u.id]
        times  = [t for t in times if now - t < window]
        times.append(now)
        _flood_data[chat.id][u.id] = times

        if len(times) > limit:
            await _delete_msg(msg)
            block_seconds = 900
            await _mute_user(context.bot, chat.id, u.id, block_seconds)
            try:
                from utils.fonts import sc as _sc
                warn = await context.bot.send_message(
                    chat.id,
                    f"⛔ {_sc('Spam Detected!')} {mention(u)} "
                    f"{_sc('You Are Blocked For 15 Minutes.')}",
                    parse_mode="HTML"
                )
                try:
                    import time as _time
                    from utils.mongo_db import set_spam_block
                    await set_spam_block(u.id, _time.time() + block_seconds)
                    await context.bot.send_message(
                        u.id,
                        "⛔ Yᴏᴜ ʜᴀᴠᴇ ʙᴇᴇɴ ʙʟᴏᴄᴋᴇᴅ ꜰʀᴏᴍ ᴜsɪɴɢ Iᴏᴛᴀ ꜰᴏʀ "
                        "15 ᴍɪɴᴜᴛᴇs ᴅᴜᴇ ᴛᴏ sᴘᴀᴍᴍɪɴɢ. Pʟᴇᴀsᴇ sʟᴏᴡ ᴅᴏᴡɴ."
                    )
                except Exception:
                    pass
                if context.job_queue:
                    context.job_queue.run_once(
                        lambda c: c.bot.delete_message(chat.id, warn.message_id),
                        10
                    )
            except Exception:
                pass
            await _log(context.bot, prot.get("log_channel", 0),
                       f"⛔ Spam | {u.id} | {chat.title}")
            return

    # ── Linkban (smart invite / foreign link filter) ──────────────────────────
    lb = merge_linkban_settings(prot)
    if lb.get("linkban_enabled") and text:
        own_uname = ""
        if lb.get("linkban_allow_own", True):
            try:
                own_uname = (chat.username or "") if chat else ""
            except Exception:
                own_uname = ""
        blocked, hits = should_block_links(
            text,
            enabled=True,
            allowlist=lb.get("link_allowlist") or [],
            own_username=own_uname,
            allow_own=bool(lb.get("linkban_allow_own", True)),
            block_urls=bool(lb.get("linkban_block_urls", False)),
        )
        if blocked:
            await _delete_msg(msg)
            mode = (lb.get("linkban_mode") or "delete").lower()
            mute_secs = int(lb.get("linkban_mute_secs") or 300)
            sample = hits[0].get("match", "link") if hits else "link"
            try:
                if mode == "mute":
                    await _mute_user(context.bot, chat.id, u.id, mute_secs)
                    await context.bot.send_message(
                        chat.id,
                        f"🔗 {mention(u)} — invite/link blocked! Muted "
                        f"{mute_secs // 60}m.\n<code>{safe_html(sample[:80])}</code>",
                        parse_mode="HTML",
                    )
                elif mode == "warn":
                    await context.bot.send_message(
                        chat.id,
                        f"🔗 {mention(u)} — foreign links are not allowed here!\n"
                        f"<code>{safe_html(sample[:80])}</code>",
                        parse_mode="HTML",
                    )
                else:
                    warn = await context.bot.send_message(
                        chat.id,
                        f"🔗 {mention(u)} — that link is not allowed.",
                        parse_mode="HTML",
                    )
                    asyncio.create_task(_auto_del_msg(context.bot, chat.id, warn.message_id, 6))
            except Exception:
                pass
            try:
                await log_mod_action(
                    chat.id, 0, "linkban", u.id,
                    reason=sample[:120],
                    meta={"mode": mode, "hits": len(hits)},
                )
            except Exception:
                pass
            await _log(context.bot, prot.get("log_channel", 0),
                       f"🔗 Linkban | {u.id} | {safe_html(chat.title or '')} | {safe_html(sample[:60])}")
            return

    # ── Anti-link (legacy coarse filter — only if linkban is OFF) ─────────────
    elif prot.get("anti_link", True) and text:
        if LINK_PATTERN.search(text):
            await _delete_msg(msg)
            await _mute_user(context.bot, chat.id, u.id, 180)
            try:
                await context.bot.send_message(
                    chat.id,
                    f"🔗 {mention(u)} — Links are not allowed! Muted 3 min.",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            await _log(context.bot, prot.get("log_channel", 0),
                       f"🔗 Link spam | {u.id} | {chat.title}")
            return

    # ── Anti-Arabic/foreign ───────────────────────────────────────────────────
    if prot.get("anti_arabic", False) and text:
        if ARABIC_PATTERN.search(text):
            await _delete_msg(msg)
            try:
                await context.bot.send_message(
                    chat.id,
                    f"🌐 {mention(u)} — Foreign script spam removed!",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            return

    # ── Anti-forward (all forwards) ──────────────────────────────────────────
    if prot.get("anti_forward", False) and msg.forward_origin:
        await _delete_msg(msg)
        try:
            await context.bot.send_message(
                chat.id,
                f"📤 {mention(u)} — Forwarded messages are not allowed here!",
                parse_mode="HTML"
            )
        except Exception:
            pass
        return

    # ── Name filter (checks sender name/username, not message body) ───────────
    if prot.get("name_filter"):
        try:
            bad_names = list(await get_bad_words(chat.id)) + list(await get_name_blocklist(chat.id))
        except Exception:
            bad_names = []
        mode = prot.get("bad_word_mode", "contains")
        haystack = f"{u.full_name or ''} {u.username or ''}".lower()
        for nb in bad_names:
            if not nb:
                continue
            if word_matches(mode, str(nb), haystack):
                await _delete_msg(msg)
                if prot.get("name_filter_action", "delete") == "mute":
                    await _mute_user(context.bot, chat.id, u.id, 300)
                try:
                    await log_mod_action(chat.id, 0, "name_filter", u.id, reason=str(nb)[:50])
                except Exception:
                    pass
                return

    # ── Anti-channel-post (channel posts / anonymous channel, not all forwards)
    if prot.get("anti_channel_post"):
        is_channel_post = False
        try:
            fo = getattr(msg, "forward_origin", None)
            if fo is not None:
                # PTB: MessageOriginChannel / type enum CHANNEL
                cls = type(fo).__name__.lower()
                t = getattr(fo, "type", None)
                tname = getattr(t, "name", str(t or "")).lower()
                if "channel" in cls or "channel" in tname:
                    is_channel_post = True
            sc = getattr(msg, "sender_chat", None)
            if sc is not None and getattr(sc, "id", None) and sc.id != chat.id:
                # Anonymous admin from linked channel or channel identity
                if getattr(sc, "type", None) and str(sc.type).lower() in (
                    "channel", "ChatType.CHANNEL", "chattype.channel"
                ):
                    is_channel_post = True
                elif getattr(sc, "username", None) or getattr(sc, "title", None):
                    # sender_chat present and not this group → treat as channel identity
                    if sc.id != chat.id:
                        is_channel_post = True
        except Exception:
            is_channel_post = False
        if is_channel_post:
            await _delete_msg(msg)
            try:
                await log_mod_action(chat.id, 0, "anti_channel_post", u.id, reason="channel post")
            except Exception:
                pass
            return

    # ── Anti-mention ──────────────────────────────────────────────────────────
    if prot.get("anti_mention") and (text or entities):
        mention_limit = int(prot.get("mention_limit") or 5)
        mc = count_mentions(text, entities)
        if mc >= mention_limit:
            action = prot.get("mention_action", "delete")
            await _delete_msg(msg)
            if action == "mute":
                await _mute_user(context.bot, chat.id, u.id, 300)
            try:
                await log_mod_action(chat.id, 0, "anti_mention", u.id, reason=f"{mc} mentions")
            except Exception:
                pass
            return

    # ── Anti-caps ─────────────────────────────────────────────────────────────
    if prot.get("anti_caps") and text:
        caps_min_len = int(prot.get("caps_min_len") or 12)
        caps_ratio_thresh = float(prot.get("caps_ratio") or 0.75)
        stripped = text.strip()
        if len(stripped) >= caps_min_len:
            ratio = caps_ratio(stripped)
            if ratio >= caps_ratio_thresh:
                await _delete_msg(msg)
                try:
                    await log_mod_action(chat.id, 0, "anti_caps", u.id, reason=f"caps {ratio:.0%}")
                except Exception:
                    pass
                return

    # ── Anti-repeat ───────────────────────────────────────────────────────────
    if prot.get("anti_repeat") and text:
        r_count = int(prot.get("repeat_count") or 3)
        r_window = int(prot.get("repeat_window") or 30)
        text_hash = hash(text.strip().lower())
        rp = _repeat_data[chat.id][u.id]
        rp = [(h, t) for h, t in rp if now - t < r_window]
        rp.append((text_hash, now))
        _repeat_data[chat.id][u.id] = rp
        same_count = sum(1 for h, _ in rp if h == text_hash)
        if same_count >= r_count:
            await _delete_msg(msg)
            await _mute_user(context.bot, chat.id, u.id, 120)
            try:
                await log_mod_action(chat.id, 0, "anti_repeat", u.id, reason=f"repeat x{same_count}")
            except Exception:
                pass
            _repeat_data[chat.id][u.id] = []
            return

    # ── Anti-zalgo ────────────────────────────────────────────────────────────
    if prot.get("anti_zalgo") and text:
        z_score = zalgo_score(text)
        if z_score > 8:
            await _delete_msg(msg)
            try:
                await log_mod_action(chat.id, 0, "anti_zalgo", u.id, reason=f"score {z_score}")
            except Exception:
                pass
            return

    # ── Anti-RTL ──────────────────────────────────────────────────────────────
    if prot.get("anti_rtl") and text:
        if RTL_OVERRIDE.search(text):
            await _delete_msg(msg)
            try:
                await log_mod_action(chat.id, 0, "anti_rtl", u.id, reason="RTL override chars")
            except Exception:
                pass
            return

    # ── Profanity filter (uses bad_word_mode) ─────────────────────────────────
    if prot.get("profanity_filter", False) and text:
        bad_words = await get_bad_words(chat.id)
        mode = prot.get("bad_word_mode", "contains")
        for bw in bad_words:
            if word_matches(mode, bw, text):
                await _delete_msg(msg)
                await _mute_user(context.bot, chat.id, u.id, 120)
                try:
                    await context.bot.send_message(
                        chat.id,
                        f"🤬 {mention(u)} — Bad word detected! Muted 2 min.",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
                try:
                    await log_mod_action(chat.id, 0, "profanity", u.id, reason=bw[:50])
                except Exception:
                    pass
                return

async def _set_silence(bot, chat_id, silent: bool):
    try:
        from telegram.constants import ChatType
    except Exception:
        pass
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
            can_change_info=False,
            can_invite_users=False,
            can_pin_messages=False,
        )
        # modern PTB accepts ChatPermissions directly
        await bot.set_chat_permissions(chat_id, perms)
    except TypeError:
        try:
            await bot.set_chat_permissions(chat_id, can_send_messages=False)
        except Exception:
            pass
    except Exception:
        pass


async def _set_slow(bot, chat_id, seconds: int):
    try:
        await bot.set_chat_slow_mode_delay(chat_id, seconds)
    except Exception:
        pass


async def anti_raid_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    chat = update.effective_chat
    if not msg or not msg.new_chat_members:
        return

    now = ts()
    # Always record join times for /prot newmember gate (even if anti-raid is OFF)
    for m in msg.new_chat_members:
        if not m.is_bot:
            _join_times[chat.id][m.id] = now

    try:
        prot = await get_prot(chat.id)
    except Exception:
        return
    if not prot.get("enabled", True) or not prot.get("anti_raid", True):
        return

    window = int(prot.get("raid_window") or 30)
    thresh = int(prot.get("raid_threshold") or 10)
    raid_action = (prot.get("raid_action") or "slow").lower()
    mute_new = bool(prot.get("raid_mute_new"))

    joins = _raid_data[chat.id]
    joins = [t for t in joins if now - t < window]
    human_joins = [m for m in msg.new_chat_members if not m.is_bot]
    joins.extend([now] * len(human_joins))
    _raid_data[chat.id] = joins
    in_raid = len(joins) >= thresh

    if in_raid:
        try:
            await log_mod_action(
                chat.id, 0, "raid", 0,
                reason=f"{len(joins)} joins in {window}s",
            )
        except Exception:
            pass

        if raid_action == "silence":
            try:
                await _set_silence(context.bot, chat.id, True)
                await context.bot.send_message(
                    chat.id,
                    "🚨 <b>RAID DETECTED!</b>\n\n"
                    f"{len(joins)} users joined in {window}s.\n"
                    "Chat silenced for all non-admins.\n"
                    "Admins can restore with /unsilence",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        else:
            try:
                await _set_slow(context.bot, chat.id, 60)
                await context.bot.send_message(
                    chat.id,
                    "🚨 <b>RAID DETECTED!</b>\n\n"
                    f"{len(joins)} users joined in {window}s.\n"
                    "Slow mode enabled (60s) for protection!\n"
                    "Admins: /prot raid off or disable slowmode",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        await _log(
            context.bot, prot.get("log_channel", 0),
            f"🚨 Raid detected | {safe_html(chat.title or '')} | "
            f"{len(joins)} joins in {window}s",
        )

    if in_raid and mute_new:
        for m in human_joins:
            try:
                await _mute_user(context.bot, chat.id, m.id, 3600)
            except Exception:
                pass


# ── Anti-bot ──────────────────────────────────────────────────────────────────

async def anti_bot_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    chat = update.effective_chat
    if not msg or not msg.new_chat_members:
        return

    prot = await get_prot(chat.id)
    if not prot.get("enabled", True) or not prot.get("anti_bot", True):
        return

    for member in msg.new_chat_members:
        if member.is_bot and member.id != context.bot.id:
            try:
                await context.bot.ban_chat_member(chat.id, member.id)
                await context.bot.send_message(
                    chat.id,
                    f"🤖 Bot <b>{member.first_name}</b> was auto-removed! (Anti-bot protection)",
                    parse_mode="HTML"
                )
            except Exception:
                pass


# ── /report command ───────────────────────────────────────────────────────────

async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    chat = update.effective_chat
    u    = update.effective_user

    if chat.type == "private":
        await msg.reply_html("🚫 Use in a group!"); return

    await ensure_user(u.id, u.username or "", u.full_name)

    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_html(
            "❌ Reply to a message to report it!\n"
            "Usage: /report [reason]"
        ); return

    reported = msg.reply_to_message.from_user
    if reported.is_bot:
        await msg.reply_html("❌ Can't report a bot!"); return
    if reported.id == u.id:
        await msg.reply_html("❌ Can't report yourself!"); return

    reason   = " ".join(context.args) if context.args else "No reason provided"
    msg_text = msg.reply_to_message.text or ""

    await add_report(chat.id, u.id, reported.id, reason, msg_text[:200])

    report_count = await get_report_count(chat.id)

    try:
        admins = await context.bot.get_chat_administrators(chat.id)
        admin_mentions = " ".join(
            mention(a.user) for a in admins if not a.user.is_bot
        )

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔨 Mute",  callback_data=f"rep_mute_{reported.id}_{chat.id}"),
            InlineKeyboardButton("⛔ Ban",   callback_data=f"rep_ban_{reported.id}_{chat.id}"),
            InlineKeyboardButton("✅ Ignore", callback_data=f"rep_ignore_{reported.id}_{chat.id}"),
        ]])

        await msg.reply_html(
            f"🚨 <b>User Reported!</b>\n\n"
            f"👤 Reported: {mention(reported)}\n"
            f"📝 By: {mention(u)}\n"
            f"💬 Reason: {reason}\n"
            f"📊 Pending reports: <b>{report_count}</b>\n\n"
            f"👮 Admins: {admin_mentions}",
            reply_markup=kb
        )
    except Exception:
        await msg.reply_html(f"✅ Report submitted! Pending reports: {report_count}")


# ── Report callback ────────────────────────────────────────────────────────────

async def report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    u = q.from_user
    if not await is_admin(update, context, u.id):
        await q.answer("Admins only!", show_alert=True); return

    parts = q.data.split("_")
    action    = parts[1]
    target_id = int(parts[2])
    chat_id   = int(parts[3])

    await q.answer()

    if action == "mute":
        await _mute_user(context.bot, chat_id, target_id, 3600)
        await q.edit_message_text(
            q.message.text + f"\n\n✅ Muted by {mention(u)} for 1 hour.",
            parse_mode="HTML"
        )
    elif action == "ban":
        await _ban_user(context.bot, chat_id, target_id)
        await q.edit_message_text(
            q.message.text + f"\n\n⛔ Banned by {mention(u)}.",
            parse_mode="HTML"
        )
    elif action == "ignore":
        await q.edit_message_text(
            q.message.text + f"\n\n✅ Ignored by {mention(u)}.",
            parse_mode="HTML"
        )


# ── /reports command ──────────────────────────────────────────────────────────

async def reports_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    u    = update.effective_user

    if chat.type == "private":
        await update.message.reply_html("🚫 Use in a group!"); return
    if not await is_admin(update, context):
        await update.message.reply_html("❌ Admins only!"); return

    pending  = await get_reports(chat.id, "pending")
    resolved = await get_reports(chat.id, "resolved")

    if not pending:
        await update.message.reply_html(
            f"📊 <b>Reports — {safe_html(chat.title)}</b>\n\n"
            f"✅ No pending reports!\n"
            f"Total resolved: {len(resolved)}"
        ); return

    text = f"📊 <b>Pending Reports — {safe_html(chat.title)}</b>\n\n"
    for i, r in enumerate(pending[:10], 1):
        try:
            rep_user = await context.bot.get_chat(r["reporter_id"])
            tgt_user = await context.bot.get_chat(r["reported_id"])
            rep_name = rep_user.first_name
            tgt_name = tgt_user.first_name
        except Exception:
            rep_name = str(r["reporter_id"])
            tgt_name = str(r["reported_id"])

        import time as _time
        t = _time.strftime("%d/%m %H:%M", _time.localtime(r["created_at"]))
        text += (
            f"{i}. 👤 <b>{tgt_name}</b>\n"
            f"   Reason: {r['reason']}\n"
            f"   By: {rep_name} | {t}\n\n"
        )

    text += f"📌 Showing {min(10, len(pending))}/{len(pending)} pending"
    await update.message.reply_html(text)


# ── /prot command — protection settings ──────────────────────────────────────

async def prot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    chat = update.effective_chat
    if chat.type != "private" and not await is_admin(update, context):
        await update.message.reply_html("❌ Admins only!"); return
    if chat.type == "private" and not await is_admin(update, context):
        from utils.group_session import is_user_group_admin
        if not await is_user_group_admin(context.bot, chat_id, update.effective_user.id):
            await update.message.reply_html("❌ Admins only!"); return

    args = context.args
    prot = await get_prot(chat_id)

    if not args:
        def _s(v): return "✅" if v else "❌"
        lb_on = bool(prot.get("linkban_enabled"))
        allow_n = len(prot.get("link_allowlist") or [])
        nsfw_on = prot.get("nsfw_enabled") is not False
        await update.message.reply_html(
            f"🛡️ <b>Protection — {safe_html(title)}</b>\n\n"
            f"{_s(prot['enabled'])} Overall\n"
            f"{_s(prot['anti_flood'])} Anti-Flood ({prot['flood_limit']}/{prot['flood_window']}s)\n"
            f"{_s(prot['anti_spam'])} Anti-Spam\n"
            f"{_s(prot['anti_link'])} Anti-Link\n"
            f"{_s(lb_on)} Linkban ({prot.get('linkban_mode','delete')}, {allow_n})\n"
            f"{_s(prot['anti_arabic'])} Anti-Arabic/Foreign\n"
            f"{_s(prot['anti_forward'])} Anti-Forward\n"
            f"{_s(prot['anti_channel_post'])} Anti-Channel-Post\n"
            f"{_s(prot['anti_bot'])} Anti-Bot\n"
            f"{_s(prot['anti_raid'])} Anti-Raid ({prot['raid_threshold']}/{prot['raid_window']}s)\n"
            f"{_s(prot['anti_mention'])} Anti-Mention (limit {prot['mention_limit']})\n"
            f"{_s(prot['anti_caps'])} Anti-CAPS (min {prot['caps_min_len']}, ratio {prot['caps_ratio']})\n"
            f"{_s(prot['anti_repeat'])} Anti-Repeat (x{prot['repeat_count']}/{prot['repeat_window']}s)\n"
            f"{_s(prot['newmember_gate'])} Newmember Gate ({prot['newmember_minutes']}m, {prot['newmember_action']})\n"
            f"{_s(prot['name_filter'])} Name Filter\n"
            f"{_s(prot['anti_zalgo'])} Anti-Zalgo\n"
            f"{_s(prot['anti_rtl'])} Anti-RTL\n"
            f"{_s(prot['profanity_filter'])} Profanity (mode: {prot.get('bad_word_mode','contains')})\n"
            f"{_s(nsfw_on)} NSFW filter\n\n"
            "Usage:\n"
            "/prot on/off\n"
            "/prot flood on/off\n"
            "/prot flood limit <n>\n"
            "/prot flood window <n>\n"
            "/prot link on/off\n"
            "/prot arabic on/off\n"
            "/prot forward on/off\n"
            "/prot antipost on/off\n"
            "/prot bot on/off\n"
            "/prot raid on/off\n"
            "/prot raid threshold <n>\n"
            "/prot raid window <n>\n"
            "/prot raid action slow|silence\n"
            "/prot raid mutenew on|off\n"
            "/prot mention on/off\n"
            "/prot mention limit <n>\n"
            "/prot namefilter on|off\n"
            "/prot caps on/off\n"
            "/prot caps min <n>\n"
            "/prot caps ratio <0.5-1.0>\n"
            "/prot repeat on/off\n"
            "/prot repeat count <n>\n"
            "/prot repeat window <n>\n"
            "/prot newmember on/off\n"
            "/prot newmember minutes <n>\n"
            "/prot newmember action delete|mute\n"
            "/prot zalgo on|off\n"
            "/prot rtl on|off\n"
            "/prot profanity on|off\n"
            "/prot badmode contains|exact|regex\n"
            "/prot setlog <channel_id>\n"
            "Linkban: /linkban · /linkallow · /linkdeny · /linkallowlist\n"
            "Name blocks: /blockname · /unblockname · /namelist"
        ); return

    cmd     = args[0].lower()
    val_str = args[1].lower() if len(args) > 1 else "on"
    val     = val_str == "on"

    mapping = {
        "on":        {"enabled": True},
        "off":       {"enabled": False},
        "flood":     {"anti_flood": val},
        "spam":      {"anti_spam": val},
        "link":      {"anti_link": val},
        "arabic":    {"anti_arabic": val},
        "foreign":   {"anti_arabic": val},
        "forward":   {"anti_forward": val},
        "antipost":  {"anti_channel_post": val},
        "bot":       {"anti_bot": val},
        "raid":      {"anti_raid": val},
        "mention":   {"anti_mention": val},
        "namefilter":{"name_filter": val},
        "caps":      {"anti_caps": val},
        "repeat":    {"anti_repeat": val},
        "newmember": {"newmember_gate": val},
        "zalgo":     {"anti_zalgo": val},
        "rtl":       {"anti_rtl": val},
        "profanity": {"profanity_filter": val},
    }

    if cmd in ("flood",):
        if val_str == "limit" and len(args) > 2:
            try:
                limit = int(args[2])
                await update_prot(chat_id, flood_limit=limit)
                await update.message.reply_html(
                    f"✅ Flood limit set to <b>{limit} msgs/{prot['flood_window']}s</b>"
                )
                return
            except ValueError:
                await update.message.reply_html("❌ Invalid limit number!")
                return
        if val_str == "window" and len(args) > 2:
            try:
                window = int(args[2])
                await update_prot(chat_id, flood_window=window)
                await update.message.reply_html(
                    f"✅ Flood window set to <b>{window}s</b>"
                )
                return
            except ValueError:
                await update.message.reply_html("❌ Invalid window number!")
                return

    if cmd == "raid":
        if val_str == "threshold" and len(args) > 2:
            try:
                await update_prot(chat_id, raid_threshold=int(args[2]))
                await update.message.reply_html("✅ Raid threshold updated.")
                return
            except ValueError:
                await update.message.reply_html("❌ Invalid threshold.")
                return
        if val_str == "window" and len(args) > 2:
            try:
                await update_prot(chat_id, raid_window=int(args[2]))
                await update.message.reply_html("✅ Raid window updated.")
                return
            except ValueError:
                await update.message.reply_html("❌ Invalid window.")
                return
        if val_str == "action" and len(args) > 2:
            mode = args[2].lower()
            if mode not in ("slow", "silence"):
                await update.message.reply_html("❌ Raid action must be slow or silence.")
                return
            await update_prot(chat_id, raid_action=mode)
            await update.message.reply_html(f"✅ Raid action → <b>{mode}</b>")
            return
        if val_str == "mutenew" and len(args) > 2:
            mv = args[2].lower() in ("on", "true", "1", "yes")
            await update_prot(chat_id, raid_mute_new=mv)
            await update.message.reply_html(f"✅ Raid mute-new joiners → <b>{'ON' if mv else 'OFF'}</b>")
            return

    if cmd == "mention" and val_str == "limit" and len(args) > 2:
        try:
            await update_prot(chat_id, mention_limit=int(args[2]))
            await update.message.reply_html(f"✅ Mention limit → <b>{int(args[2])}</b>")
            return
        except ValueError:
            await update.message.reply_html("❌ Invalid limit number!")
            return

    if cmd == "namefilter" and val_str == "on":
        await update_prot(chat_id, name_filter=True)
        await update.message.reply_html("✅ Name filter → <b>ON</b>")
        return
    if cmd == "namefilter" and val_str == "off":
        await update_prot(chat_id, name_filter=False)
        await update.message.reply_html("✅ Name filter → <b>OFF</b>")
        return

    if cmd == "caps" and val_str == "min" and len(args) > 2:
        try:
            await update_prot(chat_id, caps_min_len=int(args[2]))
            await update.message.reply_html(f"✅ CAPS min length → <b>{int(args[2])}</b>")
            return
        except ValueError:
            await update.message.reply_html("❌ Invalid min number!")
            return
    if cmd == "caps" and val_str == "ratio" and len(args) > 2:
        try:
            r = float(args[2])
            if not 0.5 <= r <= 1.0:
                raise ValueError
            await update_prot(chat_id, caps_ratio=r)
            await update.message.reply_html(f"✅ CAPS ratio → <b>{r}</b>")
            return
        except ValueError:
            await update.message.reply_html("❌ Ratio must be between 0.5 and 1.0.")
            return

    if cmd == "repeat" and val_str == "count" and len(args) > 2:
        try:
            await update_prot(chat_id, repeat_count=int(args[2]))
            await update.message.reply_html(f"✅ Repeat count → <b>{int(args[2])}</b>")
            return
        except ValueError:
            await update.message.reply_html("❌ Invalid count number!")
            return
    if cmd == "repeat" and val_str == "window" and len(args) > 2:
        try:
            await update_prot(chat_id, repeat_window=int(args[2]))
            await update.message.reply_html(f"✅ Repeat window → <b>{int(args[2])}s</b>")
            return
        except ValueError:
            await update.message.reply_html("❌ Invalid window number!")
            return

    if cmd == "newmember" and val_str == "minutes" and len(args) > 2:
        try:
            await update_prot(chat_id, newmember_minutes=int(args[2]))
            await update.message.reply_html(f"✅ Newmember gate → <b>{int(args[2])}m</b>")
            return
        except ValueError:
            await update.message.reply_html("❌ Invalid minutes number!")
            return
    if cmd == "newmember" and val_str == "action" and len(args) > 2:
        mode = args[2].lower()
        if mode not in ("delete", "mute"):
            await update.message.reply_html("❌ Action must be delete or mute.")
            return
        await update_prot(chat_id, newmember_action=mode)
        await update.message.reply_html(f"✅ Newmember action → <b>{mode}</b>")
        return

    if cmd == "badmode" and len(args) > 1:
        mode = args[1].lower()
        if mode not in ("contains", "exact", "regex"):
            await update.message.reply_html("❌ Badword mode must be contains, exact, or regex.")
            return
        await update_prot(chat_id, bad_word_mode=mode)
        await update.message.reply_html(f"✅ Badword match mode → <b>{mode}</b>")
        return

    if cmd == "setlog":
        try:
            log_id = int(args[1])
            await update_prot(chat_id, log_channel=log_id)
            await update.message.reply_html(f"✅ Log channel set to <code>{log_id}</code>")
        except (ValueError, IndexError):
            await update.message.reply_html("❌ Provide channel ID: /prot setlog -1001234567890")
        return

    if cmd in mapping:
        await update_prot(chat_id, **mapping[cmd])
        await update.message.reply_html(
            f"✅ Protection <b>{cmd}</b> → <b>{'ON' if val else 'OFF'}</b>"
        )
    else:
        await update.message.reply_html("❌ Unknown option. Use /prot for help.")


# ═══════════════════════════════════════════════════════════════════════
#  LINKS, NAMES, EXTRA PROTECTION COMMANDS
# ═══════════════════════════════════════════════════════════════════════

# ── /protstatus (alias) ───────────────────────────────────────────────────────

async def protstatus_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await prot_cmd(update, context)


# ── Blockname / Unblockname / Namelist ────────────────────────────────────────

async def blockname_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    if not context.args:
        await update.message.reply_html("Usage: /blockname <word>")
        return
    word = " ".join(context.args).lower().strip()
    await add_name_block(chat_id, word)
    await update.message.reply_html(
        f"✅ Name blocked: <b>{safe_html(word)}</b>\nGroup: {safe_html(title)}"
    )


async def unblockname_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    if not context.args:
        await update.message.reply_html("Usage: /unblockname <word>")
        return
    word = " ".join(context.args).lower().strip()
    await remove_name_block(chat_id, word)
    await update.message.reply_html(
        f"🗑️ Name unblocked: <b>{safe_html(word)}</b>\nGroup: {safe_html(title)}"
    )


async def namelist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    names = await get_name_blocklist(chat_id)
    if not names:
        await update.message.reply_html(
            f"📋 <b>Name blocklist — {safe_html(title)}</b>\n\n<i>Empty.</i> Add with /blockname <word>"
        )
        return
    lines = [f"📋 <b>Name blocklist — {safe_html(title)}</b> ({len(names)})\n"]
    for i, w in enumerate(names, 1):
        lines.append(f"{i}. <code>{safe_html(w)}</code>")
    await update.message.reply_html("\n".join(lines))


# ═══════════════════════════════════════════════════════════════════════
#  LINKBAN — foreign invite / spam link filter
# ═══════════════════════════════════════════════════════════════════════

_LINKBAN_HELP = (
    "🔗 <b>Linkban</b> — block foreign Telegram invites &amp; spam links\n\n"
    "<b>Commands:</b>\n"
    "• /linkban — status\n"
    "• /linkban on|off\n"
    "• /linkban mode delete|mute|warn\n"
    "• /linkban mute &lt;seconds&gt; — mute duration (mute mode)\n"
    "• /linkban urls on|off — also block any http(s) URL\n"
    "• /linkban own on|off — allow this group's public @username\n"
    "• /linkallow &lt;entry&gt; — whitelist domain / @user / t.me/…\n"
    "• /linkdeny &lt;entry&gt; — remove from whitelist\n"
    "• /linkallowlist — show whitelist\n"
    "• /linkcheck &lt;text&gt; — test what would be blocked\n\n"
    "<i>Admins are never blocked. When Linkban is ON it replaces "
    "the coarse Anti-Link mute for smarter invite filtering.</i>"
)


async def linkban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: configure linkban (DM-capable via active group)."""
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err)
        return

    prot = await get_prot(chat_id)
    args = context.args or []

    if not args or args[0].lower() in ("status", "help", "?"):
        allow = prot.get("link_allowlist") or []
        await update.message.reply_html(
            f"🔗 <b>Linkban — {safe_html(title)}</b>\n\n"
            f"Status: <b>{'ON ✅' if prot.get('linkban_enabled') else 'OFF ❌'}</b>\n"
            f"Mode: <b>{safe_html(prot.get('linkban_mode', 'delete'))}</b>\n"
            f"Mute: <b>{int(prot.get('linkban_mute_secs') or 300)}s</b>\n"
            f"Allow own group: <b>{'yes' if prot.get('linkban_allow_own', True) else 'no'}</b>\n"
            f"Block all URLs: <b>{'yes' if prot.get('linkban_block_urls') else 'no'}</b>\n"
            f"Allowlist: <b>{len(allow)}</b> entr{'y' if len(allow) == 1 else 'ies'}\n\n"
            + _LINKBAN_HELP
        )
        return

    sub = args[0].lower()

    if sub in ("on", "enable", "true", "1"):
        await update_prot(chat_id, linkban_enabled=True)
        await update.message.reply_html(
            f"✅ Linkban <b>ON</b> for <b>{safe_html(title)}</b>.\n"
            f"Foreign invites will be blocked (mode: "
            f"<b>{safe_html(prot.get('linkban_mode', 'delete'))}</b>)."
        )
        return

    if sub in ("off", "disable", "false", "0"):
        await update_prot(chat_id, linkban_enabled=False)
        await update.message.reply_html(
            f"✅ Linkban <b>OFF</b> for <b>{safe_html(title)}</b>."
        )
        return

    if sub == "mode":
        if len(args) < 2:
            await update.message.reply_html(
                "Usage: /linkban mode <code>delete|mute|warn</code>"
            )
            return
        mode = args[1].lower()
        if mode not in ("delete", "mute", "warn"):
            await update.message.reply_html(
                "❌ Mode must be <code>delete</code>, <code>mute</code>, or <code>warn</code>."
            )
            return
        await update_prot(chat_id, linkban_mode=mode)
        await update.message.reply_html(
            f"✅ Linkban mode → <b>{mode}</b>"
        )
        return

    if sub == "mute":
        if len(args) < 2:
            await update.message.reply_html(
                "Usage: /linkban mute &lt;seconds&gt; (e.g. 300)"
            )
            return
        try:
            secs = max(30, min(int(args[1]), 86400))
        except ValueError:
            await update.message.reply_html("❌ Seconds must be a number.")
            return
        await update_prot(chat_id, linkban_mute_secs=secs)
        await update.message.reply_html(
            f"✅ Linkban mute duration → <b>{secs}s</b> ({secs // 60}m)"
        )
        return

    if sub == "urls":
        val = (args[1].lower() if len(args) > 1 else "on") in ("on", "true", "1", "yes")
        await update_prot(chat_id, linkban_block_urls=val)
        await update.message.reply_html(
            f"✅ Block all http(s) URLs → <b>{'ON' if val else 'OFF'}</b>"
        )
        return

    if sub == "own":
        val = (args[1].lower() if len(args) > 1 else "on") in ("on", "true", "1", "yes")
        await update_prot(chat_id, linkban_allow_own=val)
        await update.message.reply_html(
            f"✅ Allow this group's own @username → <b>{'ON' if val else 'OFF'}</b>"
        )
        return

    await update.message.reply_html(
        "❌ Unknown option.\n\n" + _LINKBAN_HELP
    )


async def linkallow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Whitelist a domain / @username / invite fragment."""
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err)
        return
    if not context.args:
        await update.message.reply_html(
            "Usage: /linkallow &lt;domain|@user|t.me/…&gt;\n"
            "Example: <code>/linkallow youtube.com</code>\n"
            "         <code>/linkallow @mychannel</code>"
        )
        return
    raw = " ".join(context.args)
    ok = await add_link_allow(chat_id, raw)
    entry = normalize_allow_entry(raw)
    if ok:
        await update.message.reply_html(
            f"✅ Allowed: <code>{safe_html(entry)}</code>\n"
            f"Group: <b>{safe_html(title)}</b>"
        )
    else:
        await update.message.reply_html(
            f"⚠️ Already allowed or invalid: <code>{safe_html(entry or raw)}</code>"
        )


async def linkdeny_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a whitelist entry."""
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err)
        return
    if not context.args:
        await update.message.reply_html(
            "Usage: /linkdeny &lt;entry&gt;"
        )
        return
    raw = " ".join(context.args)
    ok = await remove_link_allow(chat_id, raw)
    entry = normalize_allow_entry(raw)
    if ok:
        await update.message.reply_html(
            f"🗑️ Removed from allowlist: <code>{safe_html(entry)}</code>"
        )
    else:
        await update.message.reply_html(
            f"❌ Not in allowlist: <code>{safe_html(entry or raw)}</code>"
        )


async def linkallowlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current link allowlist."""
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err)
        return
    prot = await get_prot(chat_id)
    allow = prot.get("link_allowlist") or []
    if not allow:
        await update.message.reply_html(
            f"📋 <b>Link allowlist — {safe_html(title)}</b>\n\n"
            f"<i>Empty.</i> Add with /linkallow &lt;entry&gt;"
        )
        return
    lines = [f"📋 <b>Link allowlist — {safe_html(title)}</b> ({len(allow)})\n"]
    for i, e in enumerate(allow, 1):
        lines.append(f"{i}. <code>{safe_html(str(e))}</code>")
    await update.message.reply_html("\n".join(lines))


async def linkcheck_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dry-run: test text against current linkban rules (no delete)."""
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err)
        return
    if not context.args:
        await update.message.reply_html(
            "Usage: /linkcheck &lt;text with links&gt;\n"
            "Example: <code>/linkcheck join https://t.me/+AbCdEf</code>"
        )
        return
    text = " ".join(context.args)
    prot = await get_prot(chat_id)
    lb = merge_linkban_settings(prot)
    own = ""
    try:
        if update.effective_chat and update.effective_chat.type != "private":
            own = update.effective_chat.username or ""
        elif lb.get("linkban_allow_own"):
            ch = await context.bot.get_chat(chat_id)
            own = getattr(ch, "username", "") or ""
    except Exception:
        own = ""
    blocked, hits = should_block_links(
        text,
        enabled=True,
        allowlist=lb.get("link_allowlist") or [],
        own_username=own,
        allow_own=bool(lb.get("linkban_allow_own", True)),
        block_urls=bool(lb.get("linkban_block_urls", False)),
    )
    if not hits and not blocked:
        from utils.linkban import extract_link_hits
        all_hits = extract_link_hits(text)
        if not all_hits:
            await update.message.reply_html(
                f"🔎 No links found in text.\nGroup: <b>{safe_html(title)}</b>"
            )
            return
        lines = [f"🔎 <b>Linkcheck — all allowed</b> ({safe_html(title)})\n"]
        for h in all_hits:
            lines.append(
                f"✅ <code>{safe_html(h.get('match', '')[:80])}</code> "
                f"({h.get('kind')})"
            )
        await update.message.reply_html("\n".join(lines))
        return
    lines = [
        f"🔎 <b>Linkcheck — {safe_html(title)}</b>\n"
        f"Would block: <b>{'YES' if blocked else 'NO'}</b>\n"
    ]
    for h in hits:
        lines.append(
            f"🚫 <code>{safe_html(h.get('match', '')[:80])}</code> "
            f"({h.get('kind')}: {safe_html(str(h.get('value', ''))[:40])})"
        )
    await update.message.reply_html("\n".join(lines))


# ── /addword / /removeword / /badwords ────────────────────────────────────────

async def addword_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    if not context.args: await update.message.reply_html("Usage: /addword <word>"); return
    word = " ".join(context.args).lower()
    await add_bad_word(chat_id, word)
    await update.message.reply_html(f"✅ Bad word added: <b>{safe_html(word)}</b>")


async def removeword_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    if not context.args: await update.message.reply_html("Usage: /removeword <word>"); return
    word = " ".join(context.args).lower()
    await remove_bad_word(chat_id, word)
    await update.message.reply_html(f"✅ Removed: <b>{safe_html(word)}</b>")


async def badwords_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return
    words = await get_bad_words(chat_id)
    if not words: await update.message.reply_html("📋 No bad words set!"); return
    await update.message.reply_html(
        f"🤬 <b>Bad Words List — {safe_html(title)}</b>\n\n"
        + "\n".join(f"• {safe_html(w)}" for w in words)
    )
