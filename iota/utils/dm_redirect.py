"""
Reusable helper: force certain commands/features to run only in a DM, and
show a clickable "Open in DM" button when they're used in a group.

Used by /commands, /pay, /fpay, /help, /panel and other info/owner commands
so sensitive or long output never spams a group chat.
"""
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from config import BOT_USERNAME


def dm_button(payload: str = ""):
    """A clickable button that opens the bot's DM, optionally deep-linking a
    /start payload (e.g. 'commands' or 'pay')."""
    url = f"https://t.me/{BOT_USERNAME}"
    if payload:
        url += f"?start={payload}"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("💬 Open in DM", url=url)
    ]])


async def require_dm(update, context, label: str = "ye feature", payload: str = "start"):
    """If the update came from a group/supergroup, send a 'use in DM' notice
    with a clickable button and return False. In a private chat, return True
    so the caller can proceed. Never raises."""
    chat = update.effective_chat
    if chat and chat.type != "private":
        try:
            await update.effective_message.reply_html(
                f"🔒 <b>{label}</b> sirf <b>DM (Private Chat)</b> me chalta hai.\n"
                f"Niche diye gaye button se bot ke DM me open karein 👇",
                reply_markup=dm_button(payload),
            )
        except Exception:
            pass
        return False
    return True
