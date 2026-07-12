"""
Iota Bot — Spectator commands (/watch, /unwatch)

Lets a user silently follow a live multiplayer game (Connect-4 / UNO) in
their group. They get a DM with every board update instead of watching the
group flood with moves. Self-contained: finds the active game in the chat,
subscribes the user to its `watchers` set, and DM-sends the current state.
"""
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from utils.spectator import add_watcher, remove_watcher, is_watching
from utils.helpers import mention

logger = logging.getLogger(__name__)


def _find_game(chat_id):
    """Return (game, kind) for the active game in this chat, or (None, None)."""
    from handlers import connect_four, uno
    g = connect_four._active_game_for_chat(chat_id)
    if g:
        return g, "connect4"
    g = uno._active_game_for_chat(chat_id)
    if g:
        return g, "uno"
    return None, None


def _render(game, kind):
    if kind == "connect4":
        from handlers import connect_four
        return connect_four._render(game["board"], "Game")
    if kind == "uno":
        from handlers import uno
        return uno._render(game)
    return "👀 Watching…"


async def watch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_html("👀 /watch sirf groups mein chalao!")
        return
    game, kind = _find_game(chat.id)
    if not game:
        await update.message.reply_html(
            "👀 Is chat mein koi active Connect-4 / UNO game nahi.\n"
            "Pehle koi game shuru karo, phir /watch use karo!"
        )
        return
    if is_watching(game, u.id):
        await update.message.reply_html("✅ Tu already watch kar raha hai!")
        return
    add_watcher(game, u.id)
    try:
        await context.bot.send_message(
            u.id,
            f"👀 <b>Watching started!</b>\n\n{_render(game, kind)}\n\n"
            f"Har move ka update yahan DM aayega. Stop karne ke liye /unwatch.",
            parse_mode="HTML"
        )
        await update.message.reply_html(f"👀 {mention(u)} ab game dekh raha hai (DM updates).")
    except Exception:
        remove_watcher(game, u.id)
        await update.message.reply_html(
            "⚠️ DM nahi bhej paya — shayad bot ne tujhe message nahi kiya hai. "
            "Pehle bot ko DM karke start kar."
        )


async def unwatch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    chat = update.effective_chat
    game, kind = _find_game(chat.id)
    if not game or not is_watching(game, u.id):
        await update.message.reply_html("❌ Tu is game ko watch nahi kar raha.")
        return
    remove_watcher(game, u.id)
    await update.message.reply_html(f"🔕 {mention(u)} ne watch stop kar diya.")
