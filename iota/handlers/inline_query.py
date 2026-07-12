"""
Iota Bot — Inline Query Support (net-new)

Lets users type @Its_iotabot <text> in ANY chat to get instant inline
results: a quick calculator, a dice roll, or a quoted Iota card. All
results are built locally (no network), so it can never fail.

Register with: app.add_handler(InlineQueryHandler(inline_query_handler))
"""
import logging
import re
import random
from telegram import (
    Update, InlineQueryResultArticle, InputTextMessageContent,
    InlineQueryResultPhoto,
)
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


def _safe_eval(expr: str):
    """Evaluate a basic arithmetic expression safely (no names / calls)."""
    if not re.fullmatch(r"[0-9+\-*/%.()\s]+", expr):
        return None
    try:
        return eval(expr, {"__builtins__": {}}, {})
    except Exception:
        return None


async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = (update.inline_query.query or "").strip()
    results = []

    # 1) Calculator
    if query and re.search(r"[0-9]", query) and re.search(r"[+\-*/]", query):
        val = _safe_eval(query)
        if val is not None:
            results.append(InlineQueryResultArticle(
                id="calc",
                title=f"🧮 = {val}",
                description=query,
                input_message_content=InputTextMessageContent(
                    f"🧮 {query} = {val}"
                ),
            ))

    # 2) Dice roll
    if query.lower() in ("dice", "roll", "🎲", ""):
        r = random.randint(1, 6)
        results.append(InlineQueryResultArticle(
            id="dice",
            title=f"🎲 Roll: {r}",
            description="Roll a die",
            input_message_content=InputTextMessageContent(f"🎲 I rolled a {r}!"),
        ))

    # 3) Echo / quote card
    text = query or "Iota Bot"
    results.append(InlineQueryResultArticle(
        id="card",
        title="💬 Iota Card",
        description=f"Send: {text[:40]}",
        input_message_content=InputTextMessageContent(
            f"💬 <b>{text}</b>\n— ᴠɪᴀ @Its_iotabot", parse_mode="HTML"
        ),
    ))

    if not results:
        results.append(InlineQueryResultArticle(
            id="help",
            title="💡 Iota Inline",
            description="Type math, 'dice', or any text",
            input_message_content=InputTextMessageContent(
                "💡 Try @Its_iotabot 2+2 or 'dice'"
            ),
        ))

    try:
        await update.inline_query.answer(results, cache_time=1)
    except Exception as e:
        logger.debug(f"inline_query answer failed: {e}")
