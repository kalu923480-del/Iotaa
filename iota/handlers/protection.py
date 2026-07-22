"""
Iota Group Protection System — MongoDB-backed
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Auto-detects and handles:
  • Spam floods (too many messages from one user)
  • Arabic/foreign script spam
  • Link spam (non-whitelisted URLs)
  • Linkban (foreign invite links + optional all URLs, with allowlist)
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
    add_link_allow, remove_link_allow, log_mod_action,
)
from utils.helpers import mention, ts, is_admin, resolve_target_chat
from utils.safe_html import safe_html
from utils.linkban import should_block_links, normalize_allow_entry, merge_linkban_settings

logger = logging.getLogger(__name__)

# ── Runtime flood tracking (in-memory, per-process) ───────────────────────────
# {chat_id: {user_id: [timestamps]}}
_flood_data: dict = defaultdict(lambda: defaultdict(list))
_raid_data:  dict = defaultdict(list)   # {chat_id: [join_timestamps]}

# Legacy broad pattern used only when anti_link is ON and linkban is OFF
# (keeps old behaviour: any http/t.me/@mention-like spam).
LINK_PATTERN   = re.compile(r'(https?://|t\.me/|telegram\.me/)', re.IGNORECASE)
ARABIC_PATTERN = re.compile(r'[\u0600-\u06FF\u0750-\u077F]{5,}')


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
    # Also pull URLs from message entities (hidden text_link / url entities)
    try:
        entities = list(msg.entities or []) + list(msg.caption_entities or [])
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
    now  = ts()

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
            block_seconds = 900  # 15 minutes, matching Baka
            await _mute_user(context.bot, chat.id, u.id, block_seconds)
            try:
                from utils.fonts import sc as _sc
                warn = await context.bot.send_message(
                    chat.id,
                    f"⛔ {_sc('Spam Detected!')} {mention(u)} "
                    f"{_sc('You Are Blocked For 15 Minutes.')}",
                    parse_mode="HTML"
                )
                # DM the spammer — exact Baka-style message
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
                    pass  # user may have blocked the bot — that's fine
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
    # Preferred system: blocks t.me invites / foreign group usernames with
    # whitelist + modes. When enabled, it supersedes the coarse anti_link mute.
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
                    # delete-only (default) — quiet warning that auto-clears
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

    # ── Anti-forward ──────────────────────────────────────────────────────────
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

    # ── Profanity filter ──────────────────────────────────────────────────────
    if prot.get("profanity_filter", False) and text:
        bad_words = await get_bad_words(chat.id)
        for bw in bad_words:
            if bw in text.lower():
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
                return


# ── Anti-raid (new member flood) ─────────────────────────────────────────────

async def anti_raid_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    chat = update.effective_chat
    if not msg or not msg.new_chat_members:
        return

    prot = await get_prot(chat.id)
    if not prot.get("enabled", True) or not prot.get("anti_raid", True):
        return

    now    = ts()
    window = prot.get("raid_window", 30)
    thresh = prot.get("raid_threshold", 10)

    joins = _raid_data[chat.id]
    joins = [t for t in joins if now - t < window]
    joins.extend([now] * len(msg.new_chat_members))
    _raid_data[chat.id] = joins

    if len(joins) >= thresh:
        try:
            await context.bot.set_chat_slow_mode_delay(chat.id, 60)
            await context.bot.send_message(
                chat.id,
                f"🚨 <b>RAID DETECTED!</b>\n\n"
                f"{len(joins)} users joined in {window}s.\n"
                f"Slow mode enabled (60s) for protection!\n"
                f"Admins can disable with /prot raid off",
                parse_mode="HTML"
            )
        except Exception:
            pass
        await _log(context.bot, prot.get("log_channel", 0),
                   f"🚨 Raid detected | {chat.title} | {len(joins)} joins in {window}s")


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
        nsfw_on = prot.get("nsfw_enabled") is not False  # auto ON unless hard-disabled
        await update.message.reply_html(
            f"🛡️ <b>Protection Settings — {safe_html(title)}</b>\n\n"
            f"{_s(prot['enabled'])} Overall: <b>{'ON' if prot['enabled'] else 'OFF'}</b>\n"
            f"{_s(prot['anti_flood'])} Anti-Flood (limit: {prot['flood_limit']}/{prot['flood_window']}s)\n"
            f"{_s(prot['anti_spam'])} Anti-Spam\n"
            f"{_s(prot['anti_link'])} Anti-Link (legacy)\n"
            f"{_s(lb_on)} Linkban (mode: <b>{safe_html(prot.get('linkban_mode','delete'))}</b>, "
            f"allowlist: {allow_n})\n"
            f"{_s(prot['anti_arabic'])} Anti-Arabic/Foreign\n"
            f"{_s(prot['anti_forward'])} Anti-Forward\n"
            f"{_s(prot['anti_bot'])} Anti-Bot\n"
            f"{_s(prot['anti_raid'])} Anti-Raid (threshold: {prot['raid_threshold']}/{prot['raid_window']}s)\n"
            f"{_s(prot['profanity_filter'])} Profanity Filter\n"
            f"{_s(nsfw_on)} NSFW auto-filter (stickers/photos, high-confidence only)\n\n"
            "Usage:\n"
            "/prot on/off — Enable/disable all\n"
            "/prot flood on/off\n"
            "/prot link on/off\n"
            "/prot arabic on/off\n"
            "/prot forward on/off\n"
            "/prot bot on/off\n"
            "/prot raid on/off\n"
            "/prot profanity on/off\n"
            "/prot flood limit <number>\n"
            "/prot setlog <channel_id>\n"
            "Linkban: /linkban · /linkallow · /linkdeny · /linkallowlist"
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
        "bot":       {"anti_bot": val},
        "raid":      {"anti_raid": val},
        "profanity": {"profanity_filter": val},
    }

    if cmd == "flood" and val_str == "limit" and len(args) > 2:
        try:
            limit = int(args[2])
            await update_prot(chat_id, flood_limit=limit)
            await update.message.reply_html(
                f"✅ Flood limit set to <b>{limit} msgs/{prot['flood_window']}s</b>"
            )
        except ValueError:
            await update.message.reply_html("❌ Invalid limit number!")
    elif cmd == "setlog":
        try:
            log_id = int(args[1])
            await update_prot(chat_id, log_channel=log_id)
            await update.message.reply_html(f"✅ Log channel set to <code>{log_id}</code>")
        except (ValueError, IndexError):
            await update.message.reply_html("❌ Provide channel ID: /prot setlog -1001234567890")
    elif cmd in mapping:
        await update_prot(chat_id, **mapping[cmd])
        await update.message.reply_html(
            f"✅ Protection <b>{cmd}</b> → <b>{'ON' if val else 'OFF'}</b>"
        )
    else:
        await update.message.reply_html("❌ Unknown option. Use /prot for help.")


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
        enabled=True,  # always evaluate for dry-run
        allowlist=lb.get("link_allowlist") or [],
        own_username=own,
        allow_own=bool(lb.get("linkban_allow_own", True)),
        block_urls=bool(lb.get("linkban_block_urls", False)),
    )
    if not hits and not blocked:
        # still show extracted hits even if all allowed
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
