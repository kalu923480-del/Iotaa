"""Iota Bot — Clans commands."""
import logging
import time
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from utils.mongo_db import ensure_user, get_user
from utils.helpers import mention, fmt
from utils.fonts import sc, bold_sc
from utils.permissions import group_only, requires_not_banned
from utils.clans import (
    create_clan, get_clan, get_user_clan, join_clan, leave_clan,
    kick_member, disband_clan, deposit_to_clan, top_clans,
    set_clan_desc, transfer_ownership, clan_info_text,
    get_clan_by_name_or_tag, CLAN_MAX_MEMBERS,
)

logger = logging.getLogger(__name__)


@requires_not_banned
async def clan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    clan = await get_user_clan(u.id)
    if not clan:
        await update.message.reply_html(
            f"🏰 <b>{sc('Clans')}</b>\n\n"
            f"{sc('You are not in a clan yet.')}\n"
            f"/clancreate <name> | <TAG> — {sc('create one')}\n"
            f"/clanjoin <tag|name> — {sc('join one')}"
        )
        return
    members = clan.get("members", {})
    member_lines = []
    for uid_s, info in members.items():
        role = info.get("role", "member")
        member_lines.append(f"  {role.title()}: {uid_s}")
    member_text = "\n".join(member_lines[:10])
    text = (
        f"🏰 <b>{clan.get('name','?')}</b> [{clan.get('tag','?')}]\n"
        f"👑 Owner: {clan.get('owner_id','?')}\n"
        f"👥 Members: {len(members)}/{CLAN_MAX_MEMBERS}\n"
        f"⚡ Total XP: {clan.get('total_xp',0)}\n"
        f"🏦 Bank: {fmt(clan.get('bank',0))}\n"
        f"📝 {clan.get('desc','') or 'No description.'}\n\n"
        f"<b>Members:</b>\n{member_text}"
    )
    await update.message.reply_html(text)


@requires_not_banned
async def clancreate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    args = context.args or []
    if not args:
        await update.message.reply_html(
            f"⚠️ {sc('Usage')}: /clancreate <name> | <TAG>\n"
            f"Example: /clancreate Iota Warriors | IOTA"
        )
        return
    raw = " ".join(args)
    if "|" in raw:
        parts = raw.split("|", 1)
        name = parts[0].strip()
        tag = parts[1].strip()
    else:
        name = raw
        tag = args[-1].strip()
        name = " ".join(args[:-1]).strip()
    if not name or not tag:
        await update.message.reply_html(
            f"⚠️ {sc('Usage')}: /clancreate <name> | <TAG>"
        )
        return
    ok, msg, _ = await create_clan(u.id, name, tag)
    await update.message.reply_html(msg)


@requires_not_banned
async def clanjoin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    args = context.args or []
    if not args:
        await update.message.reply_html(
            f"⚠️ {sc('Usage')}: /clanjoin <tag|name>"
        )
        return
    query = " ".join(args).strip()
    clan = await get_clan_by_name_or_tag(query)
    if not clan:
        await update.message.reply_html("❌ Clan not found.")
        return
    ok, msg = await join_clan(u.id, clan["_id"])
    await update.message.reply_html(msg)


@requires_not_banned
async def clanleave_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    ok, msg = await leave_clan(u.id)
    await update.message.reply_html(msg)


@requires_not_banned
async def clankick_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    target = None
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target = update.message.reply_to_message.from_user
    elif context.args:
        try:
            target_id = int(context.args[0])
            target = type("User", (), {"id": target_id, "username": "", "full_name": f"User {target_id}"})()
        except Exception:
            pass
    if not target:
        await update.message.reply_html(
            f"⚠️ {sc('Usage')}: /clankick (reply to user) or /clankick <user_id>"
        )
        return
    await ensure_user(target.id, target.username or "", target.full_name or f"User {target.id}")
    ok, msg = await kick_member(u.id, target.id)
    await update.message.reply_html(msg)


@requires_not_banned
async def clandisband_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    ok, msg = await disband_clan(u.id)
    await update.message.reply_html(msg)


@requires_not_banned
async def clandeposit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    args = context.args or []
    if not args:
        await update.message.reply_html(f"⚠️ {sc('Usage')}: /clandeposit <amount>")
        return
    try:
        amount = int(args[0])
    except Exception:
        await update.message.reply_html("❌ Invalid amount.")
        return
    ok, msg = await deposit_to_clan(u.id, amount)
    await update.message.reply_html(msg)


@requires_not_banned
async def clanbank_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    clan = await get_user_clan(u.id)
    if not clan:
        await update.message.reply_html("❌ You are not in a clan.")
        return
    await update.message.reply_html(
        f"🏦 <b>{clan.get('name','?')} Bank</b>\n"
        f"💰 {fmt(clan.get('bank',0))} coins"
    )


async def clantop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await top_clans(10)
    if not rows:
        await update.message.reply_html("📊 No clans yet!")
        return
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    lines = [f"🏆 <b>{sc('Top Clans')}</b>", "━" * 18]
    for i, c in enumerate(rows):
        lines.append(
            f"{medals[i]} {c.get('name','?')} [{c.get('tag','?')}] — "
            f"⚡ {c.get('total_xp',0)} XP"
        )
    await update.message.reply_html("\n".join(lines))


@requires_not_banned
async def clanmembers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    clan = await get_user_clan(u.id)
    if not clan:
        await update.message.reply_html("❌ You are not in a clan.")
        return
    members = clan.get("members", {})
    lines = [f"👥 <b>{clan.get('name','?')} Members</b>", "━" * 18]
    for uid_s, info in sorted(members.items(), key=lambda x: x[1].get("joined", 0)):
        role = info.get("role", "member")
        lines.append(f"• {uid_s} — {role.title()}")
    await update.message.reply_html("\n".join(lines))


@requires_not_banned
async def clandesc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    args = context.args or []
    if not args:
        await update.message.reply_html(f"⚠️ {sc('Usage')}: /clandesc <text>")
        return
    desc = " ".join(args)
    ok, msg = await set_clan_desc(u.id, desc)
    await update.message.reply_html(msg)


@requires_not_banned
async def clantransfer_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    target = None
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target = update.message.reply_to_message.from_user
    elif context.args:
        try:
            tid = int(context.args[0])
            target = type("User", (), {"id": tid, "username": "", "full_name": f"User {tid}"})()
        except Exception:
            pass
    if not target:
        await update.message.reply_html(
            f"⚠️ {sc('Usage')}: /clantransfer (reply) or /clantransfer <user_id>"
        )
        return
    await ensure_user(target.id, target.username or "", target.full_name or f"User {target.id}")
    ok, msg = await transfer_ownership(u.id, target.id)
    await update.message.reply_html(msg)
