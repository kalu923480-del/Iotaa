"""DM-based group management panel for Iota bot."""
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from utils.mongo_db import (
    get_welcome_settings, set_welcome_settings,
    get_group_settings, ensure_group_settings,
    get_prot, update_prot, get_db,
)
from utils.safe_html import safe_html

logger = logging.getLogger(__name__)

# ── Helpers ──────────────────────────────────────────────────────────────────

def _is_owner(uid: int) -> bool:
    from config import OWNER_ID
    return int(uid) == int(OWNER_ID)


async def _require_active(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.group_session import get_active_group
    uid = update.effective_user.id
    cid = await get_active_group(uid)
    if cid is None:
        await update.message.reply_html("🔗 Pehle /mygroups se group select karo.")
        return None
    return cid


# ── /mygroups ────────────────────────────────────────────────────────────────

async def mygroups_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List groups where the user is admin, with inline select buttons."""
    chat = update.effective_chat
    if chat.type in ("group", "supergroup"):
        await update.message.reply_html(
            "💬 <b>My Groups is a DM command.</b>\n"
            "Open this bot in DM and use <code>/mygroups</code> there."
        )
        return
    bot = context.bot
    uid = update.effective_user.id
    try:
        from utils.group_session import list_candidate_groups_for_user
        groups = await list_candidate_groups_for_user(bot, uid, limit=40)
    except Exception as e:
        logger.debug(f"mygroups list error: {e}")
        groups = []

    if not groups:
        await update.message.reply_html(
            "📭 <b>No groups found!</b>\n\n"
            "Add me to a group as admin, then use /mygroups here."
        )
        return

    rows = []
    for g in groups:
        rows.append([InlineKeyboardButton(
            safe_html(g["title"]),
            callback_data=f"gdm_sel_{g['id']}"
        )])
    rows.append([InlineKeyboardButton("🔄 Refresh", callback_data="gdm_refresh")])

    await update.message.reply_html(
        f"📋 <b>Your Groups</b> — {len(groups)} found\n\n"
        "Tap a group to make it <b>active</b> for DM commands:",
        reply_markup=InlineKeyboardMarkup(rows)
    )


# ── /gpanel ──────────────────────────────────────────────────────────────────

async def gpanel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the DM control panel for the active group."""
    cid = await _require_active(update, context)
    if cid is None:
        return
    await _send_panel(update, context, cid)


async def _send_panel(update, context, cid: int):
    bot = context.bot
    uid = update.effective_user.id
    try:
        ch = await bot.get_chat(cid)
        title = ch.title or f"Group {cid}"
    except Exception:
        title = f"Group {cid}"

    ws = await get_welcome_settings(cid)
    gs = await get_group_settings(cid) or {}
    prot = await get_prot(cid)

    w_on = "🟢" if ws.get("enabled", True) else "🔴"
    cap = "🟢" if gs.get("captcha_enabled", False) else "🔴"
    cs = "🟢" if gs.get("clean_service", False) else "🔴"
    acp = "🟢" if gs.get("anti_channel_pin", False) else "🔴"

    locks = []
    lock_map = [
        ("lock_messages", "Messages"), ("lock_media", "Media"),
        ("lock_stickers", "Stickers"), ("lock_gifs", "GIFs"),
        ("lock_links", "Links"), ("lock_polls", "Polls"),
        ("lock_forwards", "Forwards"), ("lock_games", "Games"),
    ]
    for k, label in lock_map:
        locks.append(f"{'🔒' if gs.get(k) else '🔓'} {label}")

    text = (
        f"⚙️ <b>Group Panel — {safe_html(title)}</b>\n\n"
        f"🆔 <code>{cid}</code>\n\n"
        f"👋 Welcome: {w_on}\n"
        f"🔐 Captcha: {cap}\n"
        f"🧹 Clean service: {cs}\n"
        f"📌 Anti-channel pin: {acp}\n\n"
        f"🌊 Flood: <b>{prot.get('flood_limit', 0) or 'OFF'}</b> "
        f"(action: {prot.get('flood_action', 'mute')})\n\n"
        f"<b>Locks:</b>\n" + "\n".join(locks) + "\n\n"
        f"🌙 Nightmode: {'🟢 ON' if gs.get('nightmode_enabled') else '🔴 OFF'} "
        f"({gs.get('nightmode_start','?')}→{gs.get('nightmode_end','?')})\n"
        f"📢 Forcesub: {'<code>' + safe_html(gs.get('forcesub_channel','')) + '</code>' if gs.get('forcesub_channel') else '<i>Not set</i>'}\n\n"
        f"📋 Rules: {'<b>Set</b>' if gs.get('rules') else '<i>Not set</i>'}\n\n"
        f"<b>DM / group commands:</b>\n"
        f"<code>/setwelcome</code> · <code>/setrules</code> · <code>/nightmode</code>\n"
        f"<code>/forcesub</code> · <code>/welcomebtn</code> · <code>/exportgconfig</code>\n"
        f"<code>/silence</code> · <code>/zombies</code> · <code>/staff</code> · <code>/modlog</code>\n"
        f"<code>/linkban</code> · <code>/linkallow</code> · <code>/lock media</code> · <code>/prot</code>"
    )

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"👋 Welcome: {'ON' if ws.get('enabled', True) else 'OFF'}", callback_data="gdm_w_toggle"),
            InlineKeyboardButton(f"🔐 Captcha: {'ON' if gs.get('captcha_enabled') else 'OFF'}", callback_data="gdm_cap_toggle"),
        ],
        [
            InlineKeyboardButton(f"🧹 Service: {'ON' if gs.get('clean_service') else 'OFF'}", callback_data="gdm_cs_toggle"),
            InlineKeyboardButton(f"📌 ACP: {'ON' if gs.get('anti_channel_pin') else 'OFF'}", callback_data="gdm_acp_toggle"),
        ],
        [
            InlineKeyboardButton(f"🌙 Nightmode: {'ON' if gs.get('nightmode_enabled') else 'OFF'}", callback_data="gdm_night_toggle"),
            InlineKeyboardButton(f"📢 Forcesub", callback_data="gdm_forcesub"),
        ],
        [
            InlineKeyboardButton("📋 View Rules", callback_data="gdm_rules"),
            InlineKeyboardButton("🔐 Locks", callback_data="gdm_locks"),
        ],
        [
            InlineKeyboardButton("🌊 Flood", callback_data="gdm_flood"),
            InlineKeyboardButton("🔄 Refresh", callback_data="gdm_panel"),
        ],
        [
            InlineKeyboardButton("📦 Export Config", callback_data="gdm_export"),
            InlineKeyboardButton("🗑️ Clear Active", callback_data="gdm_clear"),
        ],
    ])

    try:
        if update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        else:
            await update.message.reply_html(text, reply_markup=kb)
    except Exception:
        pass


# ── /usegroup ────────────────────────────────────────────────────────────────

async def usegroup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set active group by chat_id (must be admin there)."""
    bot = context.bot
    uid = update.effective_user.id
    args = context.args
    if not args:
        await update.message.reply_html(
            "Usage: <code>/usegroup &lt;chat_id&gt;</code>\n\n"
            "Or use /mygroups to pick from a list."
        )
        return
    try:
        cid = int(args[0])
    except ValueError:
        await update.message.reply_html("❌ Invalid chat ID. Use a number.")
        return
    try:
        from utils.group_session import is_user_group_admin
        if not _is_owner(uid) and not await is_user_group_admin(bot, cid, uid):
            await update.message.reply_html("❌ Tum is group ke admin nahi ho.")
            return
        from utils.group_session import set_active_group
        await set_active_group(uid, cid)
        await update.message.reply_html(f"✅ Active group set to <code>{cid}</code>.\n/gpanel se panel dekho.")
    except Exception as e:
        logger.debug(f"usegroup error: {e}")
        await update.message.reply_html("⚠️ Failed. Make sure the bot is in that group.")


# ── /clearactive / /ungroup ──────────────────────────────────────────────────

async def clearactive_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear the active group for this DM session."""
    uid = update.effective_user.id
    try:
        from utils.group_session import clear_active_group
        await clear_active_group(uid)
        await update.message.reply_html("🗑️ Active group cleared.")
    except Exception as e:
        logger.debug(f"clearactive error: {e}")
        await update.message.reply_html("⚠️ Failed to clear.")


# ── Callback router ──────────────────────────────────────────────────────────

async def group_dm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    data = q.data
    bot = context.bot

    async def _ans(text="", show_alert=False):
        try:
            await q.answer(text, show_alert=show_alert)
        except Exception:
            pass

    try:
        from utils.group_session import get_active_group, set_active_group, is_user_group_admin
        cid = await get_active_group(uid)
    except Exception:
        cid = None

    # ── Select group ────────────────────────────────────────────────────────
    if data.startswith("gdm_sel_"):
        try:
            sel_cid = int(data.split("_")[-1])
        except ValueError:
            await _ans("Invalid group.", show_alert=True)
            return
        if not _is_owner(uid) and not await is_user_group_admin(bot, sel_cid, uid):
            await _ans("❌ Not an admin here.", show_alert=True)
            return
        await set_active_group(uid, sel_cid)
        sel_title = f"Group {sel_cid}"
        try:
            ch = await bot.get_chat(sel_cid)
            sel_title = ch.title or sel_title
        except Exception:
            pass
        await _ans(f"✅ {safe_html(sel_title)} selected!")
        confirm = await bot.send_message(
            uid,
            f"✅ <b>Active group set:</b> {safe_html(sel_title)}\n"
            f"Use <code>/gpanel</code> to open the control panel.",
            parse_mode="HTML",
        )
        await _send_panel(update, context, sel_cid)
        return

    # ── Refresh list ────────────────────────────────────────────────────────
    if data == "gdm_refresh":
        await _ans("Refreshing...")
        try:
            from utils.group_session import list_candidate_groups_for_user
            groups = await list_candidate_groups_for_user(bot, uid, limit=40)
        except Exception:
            groups = []
        if not groups:
            await q.edit_message_text("📭 No groups found.")
            return
        rows = [[InlineKeyboardButton(safe_html(g["title"]), callback_data=f"gdm_sel_{g['id']}")]
                for g in groups]
        rows.append([InlineKeyboardButton("🔄 Refresh", callback_data="gdm_refresh")])
        await q.edit_message_text(
            f"📋 <b>Your Groups</b> — {len(groups)} found\n\nTap a group to activate:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(rows)
        )
        return

    # ── Panel actions require active group ───────────────────────────────────
    if cid is None:
        await _ans("🔗 Select a group first with /mygroups", show_alert=True)
        return

    if not _is_owner(uid) and not await is_user_group_admin(bot, cid, uid):
        await _ans("❌ Not an admin here.", show_alert=True)
        return

    # ── Welcome toggle ───────────────────────────────────────────────────────
    if data == "gdm_w_toggle":
        ws = await get_welcome_settings(cid)
        new_state = not ws.get("enabled", True)
        await set_welcome_settings(cid, enabled=new_state)
        await _ans(f"Welcome {'ON' if new_state else 'OFF'}")
        await _send_panel(update, context, cid)
        return

    # ── Captcha toggle ───────────────────────────────────────────────────────
    if data == "gdm_cap_toggle":
        gs = await get_group_settings(cid) or {}
        new_state = not gs.get("captcha_enabled", False)
        await ensure_group_settings(cid)
        await get_db().group_settings.update_one({"_id": cid}, {"$set": {"captcha_enabled": new_state}})
        await _ans(f"Captcha {'ON' if new_state else 'OFF'}")
        await _send_panel(update, context, cid)
        return

    # ── Clean service toggle ─────────────────────────────────────────────────
    if data == "gdm_cs_toggle":
        gs = await get_group_settings(cid) or {}
        new_state = not gs.get("clean_service", False)
        await get_db().group_settings.update_one({"_id": cid}, {"$set": {"clean_service": new_state}})
        await _ans(f"Clean service {'ON' if new_state else 'OFF'}")
        await _send_panel(update, context, cid)
        return

    # ── Anti-channel pin toggle ──────────────────────────────────────────────
    if data == "gdm_acp_toggle":
        gs = await get_group_settings(cid) or {}
        new_state = not gs.get("anti_channel_pin", False)
        await get_db().group_settings.update_one({"_id": cid}, {"$set": {"anti_channel_pin": new_state}})
        await _ans(f"Anti-channel pin {'ON' if new_state else 'OFF'}")
        await _send_panel(update, context, cid)
        return

    # ── Nightmode toggle ──────────────────────────────────────────────────────
    if data == "gdm_night_toggle":
        gs = await ensure_group_settings(cid)
        new_state = not gs.get("nightmode_enabled", False)
        await get_db().group_settings.update_one({"_id": cid}, {"$set": {"nightmode_enabled": new_state}})
        await _ans(f"Nightmode {'ON' if new_state else 'OFF'}")
        await _send_panel(update, context, cid)
        return

    # ── Forcesub info ─────────────────────────────────────────────────────────
    if data == "gdm_forcesub":
        gs = await get_group_settings(cid) or {}
        ch = gs.get("forcesub_channel", "")
        text = (
            f"📢 <b>Force Subscribe</b>\n\n"
            f"Channel: {'<code>' + safe_html(ch) + '</code>' if ch else '<i>Not set</i>'}\n\n"
            f"Use <code>/forcesub @channel</code> or <code>/forcesub off</code> in this DM."
        )
        await _ans()
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=_panel_kb(cid))
        return

    # ── Export config ─────────────────────────────────────────────────────────
    if data == "gdm_export":
        await _ans()
        await q.edit_message_text(
            "📦 Use <code>/exportgconfig</code> in this DM to export the group config as a JSON file.",
            parse_mode="HTML",
            reply_markup=_panel_kb(cid),
        )
        return

    # ── View rules ───────────────────────────────────────────────────────────
    if data == "gdm_rules":
        gs = await get_group_settings(cid) or {}
        rules = gs.get("rules", "")
        gtitle = f"Group {cid}"
        try:
            ch = await context.bot.get_chat(cid)
            gtitle = ch.title or gtitle
        except Exception:
            pass
        if rules:
            text = f"📋 <b>Rules — {safe_html(gtitle)}</b>\n\n{safe_html(rules)}"
        else:
            text = "📋 No rules set. Use <code>/setrules &lt;text&gt;</code> in this DM."
        await _ans()
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=_panel_kb(cid))
        return

    # ── Locks overview ───────────────────────────────────────────────────────
    if data == "gdm_locks":
        gs = await get_group_settings(cid) or {}
        lock_map = [
            ("lock_messages", "Messages"), ("lock_media", "Media"),
            ("lock_stickers", "Stickers"), ("lock_gifs", "GIFs"),
            ("lock_links", "Links"), ("lock_polls", "Polls"),
            ("lock_forwards", "Forwards"), ("lock_games", "Games"),
        ]
        lines = [f"{'🔒' if gs.get(k) else '🔓'} {label}" for k, label in lock_map]
        text = "🔐 <b>Locks</b>\n\n" + "\n".join(lines)
        await _ans()
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=_panel_kb(cid))
        return

    # ── Flood overview ───────────────────────────────────────────────────────
    if data == "gdm_flood":
        prot = await get_prot(cid)
        fl = prot.get("flood_limit", 0)
        text = (
            f"🌊 <b>Flood Control</b>\n\n"
            f"Limit: <b>{fl if fl else 'OFF'}</b> msgs\n"
            f"Action: <b>{prot.get('flood_action', 'mute')}</b>\n\n"
            "Use <code>/setflood &lt;number&gt;</code> or <code>/setflood off</code> "
            "in this DM to change."
        )
        await _ans()
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=_panel_kb(cid))
        return

    # ── Clear active ─────────────────────────────────────────────────────────
    if data == "gdm_clear":
        try:
            from utils.group_session import clear_active_group
            await clear_active_group(uid)
        except Exception:
            pass
        await _ans("🗑️ Cleared")
        try:
            await q.edit_message_text("🗑️ Active group cleared.\nUse /mygroups to select a new one.")
        except Exception:
            pass
        return

    # ── Refresh panel ────────────────────────────────────────────────────────
    if data == "gdm_panel":
        await _send_panel(update, context, cid)
        return


def _panel_kb(cid: int):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👋 Welcome", callback_data="gdm_w_toggle"),
            InlineKeyboardButton("🔐 Captcha", callback_data="gdm_cap_toggle"),
        ],
        [
            InlineKeyboardButton("🧹 Service", callback_data="gdm_cs_toggle"),
            InlineKeyboardButton("📌 ACP", callback_data="gdm_acp_toggle"),
        ],
        [
            InlineKeyboardButton("🌙 Nightmode", callback_data="gdm_night_toggle"),
            InlineKeyboardButton("📢 Forcesub", callback_data="gdm_forcesub"),
        ],
        [
            InlineKeyboardButton("📋 Rules", callback_data="gdm_rules"),
            InlineKeyboardButton("🔐 Locks", callback_data="gdm_locks"),
        ],
        [
            InlineKeyboardButton("🌊 Flood", callback_data="gdm_flood"),
            InlineKeyboardButton("📦 Export", callback_data="gdm_export"),
        ],
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="gdm_panel"),
            InlineKeyboardButton("🗑️ Clear Active", callback_data="gdm_clear"),
        ],
    ])
