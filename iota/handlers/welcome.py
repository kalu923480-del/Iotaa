"""Iota Welcome System — MongoDB-backed, with an interactive settings panel"""
import random
from telegram import Update, ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from utils.mongo_db import ensure_user, get_user, get_welcome_settings, set_welcome_settings
from utils.helpers import mention, ts
from utils.gif_provider import get_gif_for_mood
from utils.safe_html import safe_html

# NOTE: the old static WELCOME_GIFS backup list (2 hardcoded giphy.com
# media IDs) has been removed — both links had rotted (403 on request).
# Welcome GIFs now come exclusively from the live GIPHY search in
# utils/gif_provider.py. If that's ever unreachable, new_member_handler
# below just sends the welcome text without a GIF — no broken image.

WELCOME_TEXTS = [
    "💗 welcome {name}",
    "🌸 Hiee {name}, welcome to {group}!",
    "✨ Hey {name}! Glad you joined {group} 💕",
    "🎉 Welcome {name} to {group}! Have fun 🎊",
    "💫 Ayyy {name} is here! Welcome to {group} 🥳",
]

WELCOME_STICKER_IDS: list = []  # Add Telegram sticker file_ids here


async def _bot_has_admin(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> bool:
    """Best-effort check of whether the bot holds admin rights in a chat."""
    try:
        me = await context.bot.get_me()
        m = await context.bot.get_chat_member(chat_id, me.id)
        return m.status in ("administrator", "creator")
    except Exception:
        # Fall back to the persisted flag (kept fresh by the my_chat_member
        # auto-tracking handler in bot.py).
        try:
            from utils.mongo_db import is_bot_admin_in_group
            return await is_bot_admin_in_group(chat_id)
        except Exception:
            return False


# ── New member handler ────────────────────────────────────────────────────────

async def new_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg  = update.effective_message
    if not msg or not msg.new_chat_members:
        return

    ws = await get_welcome_settings(chat.id)
    if not ws.get("enabled", True):
        return

    for member in msg.new_chat_members:
        if member.is_bot:
            continue

        await ensure_user(member.id, member.username or "", member.full_name)
        name_str  = mention(member)
        group_str = f"<b>{safe_html(chat.title)}</b>"

        custom_msg = ws.get("custom_msg", "")
        if custom_msg:
            text = safe_html(custom_msg).replace("{name}", name_str).replace("{group}", group_str)
        else:
            tmpl = random.choice(WELCOME_TEXTS)
            text = tmpl.format(name=name_str, group=group_str)

        buttons_raw = ws.get("welcome_buttons", [])
        reply_markup = None
        if buttons_raw:
            kb_rows = []
            row = []
            for b in buttons_raw[:8]:
                try:
                    btn = InlineKeyboardButton(safe_html(b.get("text", "")), url=b.get("url", "#"))
                    row.append(btn)
                    if len(row) == 2:
                        kb_rows.append(row)
                        row = []
                except Exception:
                    continue
            if row:
                kb_rows.append(row)
            if kb_rows:
                reply_markup = InlineKeyboardMarkup(kb_rows)

        try:
            gif_url = None
            if ws.get("send_gif", True):
                try:
                    gif_url = await get_gif_for_mood("welcome")
                except Exception:
                    pass

            if gif_url:
                await context.bot.send_animation(
                    chat.id,
                    animation=gif_url,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
            else:
                await msg.reply_html(text, reply_markup=reply_markup)

            if ws.get("send_sticker") and WELCOME_STICKER_IDS:
                await context.bot.send_sticker(chat.id, random.choice(WELCOME_STICKER_IDS))

        except Exception:
            try:
                await context.bot.send_message(chat.id, text, parse_mode="HTML")
            except Exception:
                pass


# ── Left member handler ───────────────────────────────────────────────────────

async def left_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg  = update.effective_message
    if not msg or not msg.left_chat_member:
        return
    member = msg.left_chat_member
    if member.is_bot:
        return
    try:
        await context.bot.send_message(
            chat.id,
            f"👋 {mention(member)} has left <b>{safe_html(chat.title)}</b>. Goodbye!",
            parse_mode="HTML"
        )
    except Exception:
        pass


# ── /setwelcome — interactive panel (with text shortcuts still supported) ──

def _panel_kb(chat_id: int, ws: dict) -> InlineKeyboardMarkup:
    enabled = ws.get("enabled", True)
    gif_on  = ws.get("send_gif", True)
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
    preview = safe_html(custom) if custom else "<i>(using default random messages)</i>"
    return (
        f"📝 <b>Welcome Settings — {safe_html(chat_title)}</b>\n\n"
        f"Status: <b>{'Enabled ✅' if ws.get('enabled', True) else 'Disabled ❌'}</b>\n"
        f"GIF: <b>{'On' if ws.get('send_gif', True) else 'Off'}</b>\n\n"
        f"Current message:\n{preview}\n\n"
        f"💡 Tip: use {{name}} and {{group}} as placeholders in a custom message."
    )


async def setwelcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import resolve_target_chat
    chat_id, title, err = await resolve_target_chat(update, context, need_admin=True)
    if err:
        await update.message.reply_html(err); return

    if not await _bot_has_admin(context, chat_id):
        await update.message.reply_html(
            "⚠️ <b>Heads up:</b> I'm <b>not an admin</b> in this group. "
            "Telegram won't send me the \"new member joined\" event unless I'm "
            "an admin, so <b>welcome messages will NOT trigger</b> until you "
            "promote me to admin (even with the least privileges)."
        )

    args = context.args

    if not args:
        ws = await get_welcome_settings(chat_id)
        await update.message.reply_html(
            _panel_text(title, ws), reply_markup=_panel_kb(chat_id, ws)
        ); return

    cmd = args[0].lower()
    if cmd == "on":
        await set_welcome_settings(chat_id, enabled=True)
        await update.message.reply_html("✅ Welcome messages <b>enabled</b>!")
    elif cmd == "off":
        await set_welcome_settings(chat_id, enabled=False)
        await update.message.reply_html("❌ Welcome messages <b>disabled</b>!")
    elif cmd == "gif":
        val = args[1].lower() if len(args) > 1 else "on"
        await set_welcome_settings(chat_id, send_gif=(val == "on"))
        await update.message.reply_html(f"🎬 Welcome GIF: <b>{safe_html(val)}</b>")
    elif cmd == "msg":
        custom = " ".join(args[1:])
        if not custom:
            await update.message.reply_html("❌ Provide a message!"); return
        await set_welcome_settings(chat_id, custom_msg=custom)
        await update.message.reply_html(f"✅ Welcome message set!\nPreview:\n{safe_html(custom)}")
    elif cmd == "reset":
        await set_welcome_settings(chat_id, custom_msg="", send_gif=True)
        await update.message.reply_html("✅ Welcome reset to default!")
    else:
        await update.message.reply_html("❌ Unknown option. Use /setwelcome for help.")


# ── Button panel callback ───────────────────────────────────────────────────

async def welcome_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.helpers import is_admin
    q = update.callback_query
    parts = q.data.split("_")
    action = parts[1]
    try:
        chat_id = int(parts[2])
    except (IndexError, ValueError):
        await q.answer("Invalid action.", show_alert=True); return

    uid = q.from_user.id
    from config import OWNER_ID
    if uid != OWNER_ID:
        try:
            from utils.group_session import is_user_group_admin
            if not await is_user_group_admin(context.bot, chat_id, uid):
                await q.answer("❌ Not an admin here.", show_alert=True); return
        except Exception:
            await q.answer("❌ Not an admin here.", show_alert=True); return

    ws = await get_welcome_settings(chat_id)

    if action == "toggle":
        new_state = not ws.get("enabled", True)
        await set_welcome_settings(chat_id, enabled=new_state)
        if new_state and not await _bot_has_admin(context, chat_id):
            await q.answer(
                "Enabled — but I'm NOT an admin here, so welcome won't "
                "actually fire. Promote me to admin to activate it.",
                show_alert=True
            )
    elif action == "gif":
        await set_welcome_settings(chat_id, send_gif=not ws.get("send_gif", True))
    elif action == "reset":
        await set_welcome_settings(chat_id, custom_msg="", send_gif=True, enabled=True)
        await q.answer("Reset to default!")
    elif action == "msg":
        await q.answer(
            "To set a custom message, use:\n/setwelcome msg <your text>\n\n"
            "Use {name} and {group} as placeholders.",
            show_alert=True
        )
        return
    elif action == "preview":
        member = q.from_user
        ws2 = await get_welcome_settings(chat_id)
        custom = ws2.get("custom_msg", "")
        name_str = mention(member)
        group_str = f"<b>{safe_html(q.message.chat.title or 'this group')}</b>"
        if custom:
            text = safe_html(custom).replace("{name}", name_str).replace("{group}", group_str)
        else:
            text = random.choice(WELCOME_TEXTS).format(name=name_str, group=group_str)
        await q.answer()
        await q.message.reply_html(f"👁️ <b>Preview:</b>\n\n{text}")
        return

    await q.answer()
    ws = await get_welcome_settings(chat_id)
    chat_title = q.message.chat.title or "this group"
    try:
        from utils.group_session import get_active_group
        uid = q.from_user.id
        active_cid = await get_active_group(uid)
        if active_cid == chat_id and q.message.chat.type == "private":
            chat_title = chat_title
        else:
            try:
                ch = await context.bot.get_chat(chat_id)
                chat_title = ch.title or chat_title
            except Exception:
                pass
    except Exception:
        pass
    try:
        await q.edit_message_text(
            _panel_text(chat_title, ws), parse_mode="HTML",
            reply_markup=_panel_kb(chat_id, ws)
        )
    except Exception:
        pass  # message unchanged (e.g. double-tap) — not an error
