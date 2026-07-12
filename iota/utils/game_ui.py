"""
Iota Bot — Shared Game UI Toolkit
════════════════════════════════════
Centralises the visual language used by every mini-game so the whole games
section looks like ONE coherent, polished product instead of a loose pile of
handlers. Everything here is either:

  • PURE TEXT layout helpers (banner / medal / progress_bar / result_card)
  • a SAFE GIF sender (send_gif_result) that NEVER raises — if GIPHY is down
    or rate-limited it silently falls back to plain text.

Letter-casing (the Iota small-caps style) is handled globally by the wrapper
installed in bot.py (sc_out), so these helpers only worry about structure and
animation. Keep this module dependency-light and crash-free on purpose.
"""
import logging
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

from utils.gif_provider import get_gif_for_mood

logger = logging.getLogger(__name__)

# ── Visual constants ──────────────────────────────────────────────────────
_BAR_FULL = "█"   # █
_BAR_EMPTY = "░"  # ░
_DIVIDER = "━" * 22

_MEDALS = [
    "🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟",
    "1️⃣1️⃣", "1️⃣2️⃣", "1️⃣3️⃣", "1️⃣4️⃣", "1️⃣5️⃣",
]


def banner(title: str, icon: str = "🎮") -> str:
    """A consistent, decorated section header."""
    return f"{icon} <b>{title}</b>\n{_DIVIDER}"


def medal(i: int) -> str:
    """Medal/rank emoji for leaderboard position `i` (0-based)."""
    return _MEDALS[i] if 0 <= i < len(_MEDALS) else f"{i + 1}."


def progress_bar(value: int, maximum: int, width: int = 10) -> str:
    """A text progress bar, e.g. ████░░░░░░ (never raises on bad input)."""
    if maximum <= 0:
        return _BAR_EMPTY * width
    ratio = max(0.0, min(1.0, value / maximum))
    filled = round(ratio * width)
    return _BAR_FULL * filled + _BAR_EMPTY * (width - filled)


def result_card(title: str, lines: list, icon: str = "🎮", footer: str = "") -> str:
    """Build a tidy result card from `lines` (list of strings)."""
    body = "\n".join(lines)
    out = f"{banner(title, icon)}\n\n{body}"
    if footer:
        out += f"\n\n{footer}"
    return out


async def send_gif_result(
    context,
    chat_id: int,
    mood: str,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
):
    """
    Send `text` as a GIF animation for `mood` when one is available,
    otherwise send the text alone. ALWAYS succeeds (Never raises) — the
    whole point is to upgrade the UI without ever breaking a game flow.
    """
    gif = None
    try:
        gif = await get_gif_for_mood(mood)
    except Exception as e:  # GIPHY unreachable / rate-limited / bad key
        logger.debug(f"get_gif_for_mood('{mood}') failed: {e}")
    if gif:
        try:
            await context.bot.send_animation(
                chat_id,
                animation=gif,
                caption=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            return
        except Exception as e:  # couldn't send the animation for any reason
            logger.debug(f"send_animation('{mood}') failed, falling back to text: {e}")
    await context.bot.send_message(
        chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup
    )


def back_button(callback_data: str = "gh_home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("« ʙᴀᴄᴋ", callback_data=callback_data)
    ]])


def nav_bar(home: str = "gh_home", back: str = None,
            refresh: str = None) -> InlineKeyboardMarkup:
    """One consistent in-game navigation row: Home / Back / Refresh.

    Used by every mini-game so the whole catalogue feels like one product
    (see GAMES_ROADMAP.md Phase 2). Buttons are omitted when their
    callback is None so callers can build a minimal bar easily.
    """
    row = [InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data=home)]
    if back:
        row.append(InlineKeyboardButton("« ʙᴀᴄᴋ", callback_data=back))
    if refresh:
        row.append(InlineKeyboardButton("🔄 ʀᴇғʀᴇsʜ", callback_data=refresh))
    return InlineKeyboardMarkup([row])


def chip(label: str, value: str = "", emoji: str = "🎯") -> str:
    """A compact key/value chip used in result cards and lobbies."""
    if value:
        return f"{emoji} <b>{label}:</b> {value}"
    return f"{emoji} <b>{label}</b>"


def scoreboard(rows: list, title: str = "🏆 sᴄᴏʀᴇʙᴏᴀʀᴅ") -> str:
    """Text scoreboard (complement to game_art.render_scoreboard PNG).

    `rows` = list of (name, score). Renders a tidy, monospace-aligned
    table that degrades gracefully when art can't be sent.
    """
    if not rows:
        return f"{title}\n{_DIVIDER}\n⚠️ ɴᴏ sᴄᴏʀᴇs ʏᴇᴛ."
    lines = [title, _DIVIDER]
    medals = ["🥇", "🥈", "🥉"]
    for i, (name, score) in enumerate(rows[:12]):
        badge = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{badge} {name} — {score}")
    return "\n".join(lines)


def card_face(rank: str, suit: str = "spades", hidden: bool = False):
    """Convenience wrapper → game_art.render_card (returns PNG BytesIO)."""
    from utils import game_art
    return game_art.render_card(rank, suit, hidden)


def dice_face(value: int):
    """Convenience wrapper → game_art.render_dice (returns PNG BytesIO)."""
    from utils import game_art
    return game_art.render_dice(value)


def board_thumb(title: str = "ʙᴏᴀʀᴅ", lines: list = None) -> str:
    """Text fallback 'board thumbnail' for games that don't yet have a
    dedicated PNG renderer (e.g. connect-four). Used inside result cards
    so every game has a consistent visual anchor."""
    out = [f"🎲 {title}", _DIVIDER]
    if lines:
        out.extend(lines[:10])
    return "\n".join(out)
