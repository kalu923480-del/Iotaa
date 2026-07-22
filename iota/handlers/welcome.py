"""
Iota Welcome System — MongoDB-backed, text-first by default.

Defaults:
  • Welcome ENABLED for all groups
  • GIF OFF (text only) — admin can turn GIF on via /setwelcome gif on
  • Works whether or not the bot is admin:
      - If admin: Telegram sends NEW_CHAT_MEMBERS → instant welcome
      - If not admin: soft welcome on the member's first message in the group
  • Deduped via welcome_sent so join-event + first-message never double-fire
"""
import logging
import random
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from utils.mongo_db import (
    ensure_user, get_welcome_settings, set_welcome_settings,
    was_welcomed, mark_welcomed, clear_welcomed,
)
from utils.helpers import mention, is_admin
from utils.gif_provider import get_gif_for_mood
from utils.safe_html import safe_html

logger = logging.getLogger(__name__)

WELCOME_TEXTS = [
    "💗 welcome {name}",
    "🌸 Hiee {name}, welcome to {group}!",
    "✨ Hey {name}! Glad you joined {group} 💕",
    "🎉 Welcome {name} to {group}! Have fun 🎊",
    "💫 Ayyy {name} is here! Welcome to {group} 🥳",
]

WELCOME_STICKER_IDS: list = []  # optional sticker file_ids


async def _bot_has_admin(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> bool:
    """Best-effort check of whether the bot holds admin rights in a chat."""
    try:
        me = await context.bot.get_me()
        m = await context.bot.get_chat_member(chat_id, me.id)
        return m.status in ("administrator", "creator")
    except Exception:
        try:
            from utils.mongo_db import is_bot_admin_in_group
            return await is_bot_admin_in_group(chat_id)
        except Exception:
            return False


def _build_welcome_text(ws: dict, member, chat_title: str) -> str:
    name_str = mention(member)
    group_str = f"<b>{safe_html(chat_title or 'this group')}</b>"
    custom_msg = (ws.get("custom_msg") or "").strip()
    if custom_msg:
        return (
            safe_html(custom_msg)
            .replace("{name}", name_str)
            .replace("{group}", group_str)
        )
    tmpl = random.choice(WELCOME_TEXTS)
    return tmpl.format(name=name_str, group=group_str)


def _build_welcome_markup(ws: dict):
    buttons_raw = ws.get("welcome_buttons") or []
    if not buttons_raw:
        return None
    kb_rows = []
    row = []
    for b in buttons_raw[:8]:
        try:
            text = (b.get("text") or "").strip()
            url = (b.get("url") or "").strip()
            if not text or not url or not url.startswith(("http://", "https://", "tg://")):
                continue
            row.append(InlineKeyboardButton(text[:64], url=url))
            if len(row) == 2:
                kb_rows.append(row)
                row = []
        except Exception:
            continue
    if row:
        kb_rows.append(row)
    return InlineKeyboardMarkup(kb_rows) if kb_rows else None


async def send_welcome_message(
    bot,
    chat,
    member,
    ws: dict | None = None,
    *,
    reply_to_message=None,
    force_text_only: bool = False,
) -> bool:
    """
    Send one welcome for `member` in `chat`. Returns True if a message was sent.
    Dedupes via welcome_sent. force_text_only=True skips GIF even if enabled.
    """
    if not member or getattr(member, "is_bot", False):
        return False
    chat_id = chat.id if hasattr(chat, "id") else int(chat)
    title = getattr(chat, "title", None) or "this group"

    try:
        if await was_welcomed(chat_id, member.id):
            return False
    except Exception:
        pass

    if ws is None:
        try:
            ws = await get_welcome_settings(chat_id)
        except Exception:
            ws = {"enabled": True, "send_gif": False, "custom_msg": ""}

    if not ws.get("enabled", True):
        return False

    try:
        await ensure_user(
            member.id,
            getattr(member, "username", None) or "",
            getattr(member, "full_name", None) or getattr(member, "first_name", "") or "",
        )
    except Exception:
        pass

    text = _build_welcome_text(ws, member, title)
    markup = _build_welcome_markup(ws)
    sent = False

    # Default / preferred path: plain text (GIF off by default)
    use_gif = (not force_text_only) and bool(ws.get("send_gif", False))
    gif_url = None
    if use_gif:
        try:
            gif_url = await get_gif_for_mood("welcome")
        except Exception:
            gif_url = None

    try:
        if gif_url:
            await bot.send_animation(
                chat_id,
                animation=gif_url,
                caption=text,
                parse_mode="HTML",
                reply_markup=markup,
            )
            sent = True
        elif reply_to_message is not None:
            await reply_to_message.reply_html(text, reply_markup=markup)
            sent = True
        else:
            await bot.send_message(
                chat_id, text, parse_mode="HTML", reply_markup=markup
            )
            sent = True
    except Exception as e:
        logger.debug("welcome send failed chat=%s: %s", chat_id, e)
        try:
            await bot.send_message(chat_id, text, parse_mode="HTML")
            sent = True
        except Exception:
            sent = False

    if sent and ws.get("send_sticker") and WELCOME_STICKER_IDS:
        try:
            await bot.send_sticker(chat_id, random.choice(WELCOME_STICKER_IDS))
        except Exception:
            pass

    if sent:
        try:
            await mark_welcomed(chat_id, member.id)
        except Exception:
            pass
    return sent


# ── New member handler (join service message — works when bot can see it) ──

async def new_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    if not msg or not msg.new_chat_members or not chat:
        return
    if chat.type not in ("group", "supergroup"):
        return

    try:
        ws = await get_welcome_settings(chat.id)
    except Exception:
        ws = {"enabled": True, "send_gif": False}

    if not ws.get("enabled", True):
        return

    for member in msg.new_chat_members:
        if member.is_bot:
            continue
        try:
            await send_welcome_message(
                context.bot, chat, member, ws, reply_to_message=msg
            )
        except Exception as e:
            logger.debug("new_member welcome failed: %s", e)


# ── Soft welcome: first message from a user (covers non-admin groups) ─────

async def soft_welcome_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    When the bot is NOT admin, Telegram often does not deliver NEW_CHAT_MEMBERS.
    This handler greets a user on their first non-command message in the group,
    text-only by default, once per user (deduped with join-event welcomes).
    """
    msg = update.effective_message
    chat = update.effective_chat
    u = update.effective_user
    if not msg or not chat or not u:
        return
    if chat.type not in ("group", "supergroup"):
        return
    if u.is_bot:
        return
    # Skip pure service / status updates without real content
    if not (msg.text or msg.caption or msg.sticker or msg.photo or msg.animation
            or msg.video or msg.document or msg.voice or msg.video_note):
        return
    if msg.text and msg.text.startswith("/"):
        return

    try:
        if await was_welcomed(chat.id, u.id):
            return
    except Exception:
        return

    try:
        ws = await get_welcome_settings(chat.id)
    except Exception:
        return
    if not ws.get("enabled", True):
        return

    # Soft path always text-first (no GIF spam on first message)
    try:
        await send_welcome_message(
            context.bot, chat, u, ws,
            reply_to_message=None,
            force_text_only=True,
        )
    except Exception as e:
        logger.debug("soft welcome failed: %s", e)


# ── Left member handler ───────────────────────────────────────────────────

async def left_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    if not msg or not msg.left_chat_member or not chat:
        return
    member = msg.left_chat_member
    if member.is_bot:
        return
    # Allow welcome again if they rejoin later
    try:
        await clear_welcomed(chat.id, member.id)
    except Exception:
        pass

    # Optional goodbye (only if group enabled goodbye elsewhere — keep light)
    try:
        from utils.mongo_db import get_group_settings
        gs = await get_group_settings(chat.id) or {}
        if not gs.get("goodbye_enabled"):
            return
        gmsg = (gs.get("goodbye_msg") or "").strip()
        if gmsg:
            text = safe_html(gmsg).replace("{name}", mention(member)).replace(
                "{group}", f"<b>{safe_html(chat.title)}</b>"
            )
        else:
            text = (
                f"👋 {mention(member)} has left "
                f"<b>{safe_html(chat.title)}</b>. Goodbye!"
            )
        await context.bot.send_message(chat.id, text, parse_mode="HTML")
    except Exception:
        pass


# ── /setwelcome — interactive panel ───────────────────────────────────────

def _panel_kb(chat_id: int, ws: dict) -> InlineKeyboardMarkup:
    enabled = ws.get("enabled", True)
    gif_on = ws.get("send_gif", False)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🟢 Enabled" if enabled else "🔴 Disabled",
            callback_data=f"wset_toggle_{chat_id}"
        )],
        [InlineKeyboardButton(
            "🎬 GIF: On" if gif_on else "🎬 GIF: Off",
            callback_data=f"wset_gif_{chat_id}"
        )],
        [InlineKeyboardButton("✏️ Set Custom Message", callback_data=f"wset_msg_{chat_id}"),
         InlineKeyboardButton("♻️ Reset", callback_data=f"wset_reset_{chat_id}")],
        [InlineKeyboardButton("👁️ Preview", callback_data=f"wset_preview_{chat_id}")],
    ])


def _panel_text(chat_title: str, ws: dict) -> str:
    custom = ws.get("custom_msg", "")
    preview = safe_html(custom) if custom else "<i>(default random text messages)</i>"
    return (
        f"📝 <b>Welcome Settings — {safe_html(chat_title)}</b>\n\n"
        f"Status: <b>{'Enabled ✅' if ws.get('enabled', True) else 'Disabled ❌'}</b>\n"
        f"GIF: <b>{'On' if ws.get('send_gif', False) else 'Off (text only)'}</b>\n\n"
        f"Current message:\n{preview}\n\n"
        f"💡 Placeholders: <code>{{name}}</code> <code>{{group}}</code>\n"
        f"📌 Default is <b>text only</b>. GIF is optional.\n"
        f"📌 Works even if the bot is <b>not admin</b> "
        f"(soft welcome on first message)."
    )


async def setwelcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err)
        return

    bot_admin = await _bot_has_admin(context, chat_id)
    if not bot_admin:
        await update.message.reply_html(
            "ℹ️ I'm <b>not an admin</b> in this group.\n"
            "Telegram may hide join events, but Iota will still welcome new members "
            "with <b>text</b> on their <b>first message</b> (soft welcome).\n"
            "Promote me to admin for <b>instant</b> welcome on join."
        )

    args = context.args or []

    if not args:
        ws = await get_welcome_settings(chat_id)
        await update.message.reply_html(
            _panel_text(title, ws), reply_markup=_panel_kb(chat_id, ws)
        )
        return

    cmd = args[0].lower()
    if cmd == "on":
        await set_welcome_settings(chat_id, enabled=True)
        await update.message.reply_html("✅ Welcome messages <b>enabled</b>!")
    elif cmd == "off":
        await set_welcome_settings(chat_id, enabled=False)
        await update.message.reply_html("❌ Welcome messages <b>disabled</b>!")
    elif cmd == "gif":
        val = args[1].lower() if len(args) > 1 else "off"
        on = val in ("on", "true", "1", "yes")
        await set_welcome_settings(chat_id, send_gif=on)
        await update.message.reply_html(
            f"🎬 Welcome GIF: <b>{'On' if on else 'Off (text only)'}</b>"
        )
    elif cmd == "msg":
        custom = " ".join(args[1:])
        if not custom:
            await update.message.reply_html(
                "❌ Provide a message!\n"
                "Usage: /setwelcome msg Welcome {name} to {group}!"
            )
            return
        await set_welcome_settings(chat_id, custom_msg=custom)
        await update.message.reply_html(
            f"✅ Welcome message set!\nPreview:\n{safe_html(custom)}"
        )
    elif cmd == "reset":
        # Defaults: enabled + text only (GIF off)
        await set_welcome_settings(
            chat_id, custom_msg="", send_gif=False, send_sticker=False, enabled=True
        )
        await update.message.reply_html(
            "✅ Welcome reset to default: <b>enabled</b>, <b>text only</b> (GIF off)."
        )
    else:
        await update.message.reply_html(
            "❌ Unknown option.\n"
            "Usage: /setwelcome | on | off | gif on/off | msg … | reset"
        )


async def welcome_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split("_")
    action = parts[1] if len(parts) > 1 else ""
    try:
        chat_id = int(parts[2])
    except (IndexError, ValueError):
        await q.answer("Invalid action.", show_alert=True)
        return

    uid = q.from_user.id
    from config import OWNER_ID
    if int(uid) != int(OWNER_ID):
        try:
            from utils.group_session import is_user_group_admin
            if not await is_user_group_admin(context.bot, chat_id, uid):
                await q.answer("❌ Not an admin here.", show_alert=True)
                return
        except Exception:
            await q.answer("❌ Not an admin here.", show_alert=True)
            return

    ws = await get_welcome_settings(chat_id)

    if action == "toggle":
        new_state = not ws.get("enabled", True)
        await set_welcome_settings(chat_id, enabled=new_state)
        if new_state and not await _bot_has_admin(context, chat_id):
            await q.answer(
                "Enabled. Not admin → soft welcome on first message (text).",
                show_alert=True,
            )
        else:
            await q.answer("Updated!")
    elif action == "gif":
        await set_welcome_settings(chat_id, send_gif=not ws.get("send_gif", False))
        await q.answer("GIF toggled!")
    elif action == "reset":
        await set_welcome_settings(
            chat_id, custom_msg="", send_gif=False, send_sticker=False, enabled=True
        )
        await q.answer("Reset: text only!")
    elif action == "msg":
        await q.answer(
            "Use: /setwelcome msg <text>\nPlaceholders: {name} {group}",
            show_alert=True,
        )
        return
    elif action == "preview":
        member = q.from_user
        ws2 = await get_welcome_settings(chat_id)
        text = _build_welcome_text(ws2, member, q.message.chat.title or "this group")
        await q.answer()
        await q.message.reply_html(f"👁️ <b>Preview:</b>\n\n{text}")
        return
    else:
        await q.answer()

    ws = await get_welcome_settings(chat_id)
    chat_title = q.message.chat.title or "this group"
    try:
        ch = await context.bot.get_chat(chat_id)
        chat_title = ch.title or chat_title
    except Exception:
        pass
    try:
        await q.edit_message_text(
            _panel_text(chat_title, ws),
            parse_mode="HTML",
            reply_markup=_panel_kb(chat_id, ws),
        )
    except Exception:
        pass
