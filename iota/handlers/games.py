"""
Iota Bot — Games Handler
Card game with:
  • GIF result (Iota style)
  • Tie → normal users eliminated, premium share prize
  • Streak tracking
  • Card rank update
  • 5% fee (premium exempt)
  • XP gain
"""
import logging
import random, uuid, asyncio, json
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from utils.mongo_db import (
    ensure_user, get_user, update_user, add_balance, deduct_balance,
    get_card_rank, update_card_rank, get_card_leaders, get_card_rank_position,
    get_hack_leaders, get_db,
    set_system_status
)
from utils.helpers import mention, mention_id, fmt, xp_level, rank_title, ts
from utils.fonts import sc, sc_all
from utils.gif_provider import get_gif_for_mood
from utils.safe_html import placeholder
from utils.system_gate import games_gate
from utils.game_art import send_game_art as _send_art, render_dice_row as _render_dice_row, render_bomb
from config import (CARD_FEE_PERCENT, CARD_XP_WIN, CARD_XP_LOSS, ITEMS,
                     CARD_MIN_BET, CARD_MAX_BET, CARD_LOBBY_TIMEOUT_SECONDS)

logger = logging.getLogger(__name__)

# ── In-memory game state (card games) ────────────────────────────────────────
_card_games: dict = {}   # game_id -> game dict
_bomb_games: dict = {}
_bluff_games: dict = {}
_hack_games: dict = {}
_word_games: dict = {}

WORD_LIST = [
    "python","telegram","robot","galaxy","empire","warrior","castle",
    "diamond","battle","legend","rocket","thunder","jungle","phantom",
    "dragon","knight","silver","golden","sunrise","victory",
]


async def game_menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📖 View All Games", callback_data="game_list")
    ]])
    await update.message.reply_html(
        "🎮 Cʟɪᴄᴋ Tʜᴇ Bᴜᴛᴛᴏɴ Bᴇʟᴏᴡ Tᴏ Kɴᴏᴡ Aʙᴏᴜᴛ Iᴏᴛᴀ Mɪɴɪ Gᴀᴍᴇꜱ.",
        reply_markup=kb
    )


async def game_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "game_list":
        await q.edit_message_text(
            sc_all(
                "🎮 <b>Iota Mini Games</b>\n\n"
                "🃏 /card — Card Game (4 rounds)\n"
                "🃏 /bet &lt;amount&gt; — Card Game with Bet\n"
                "💣 /bomb — Bomb Passing Game\n"
                "🎭 /bluff — Bluff Card Game\n"
                "💻 /hack — Hack the Code\n"
                "📝 /wordgame — Word Guess\n"
                "🎲 /ludo — Ludo\n"
                "🏆 /leaders — Game Leaderboards\n"
                "📊 /rank — Your card rank\n"
                "🎰 /roulette &lt;amount&gt; — Bid-Elimination Tournament\n"
                "🤝 /rjoin &lt;amount&gt; — Join a Roulette game\n"
                "🎯 /bid &lt;amount&gt; — Bid in DM each round\n"
                "🎡 /wheel — Spin the Iota Wheel (coins/gems)\n\n"
                "Admin: /open | /close"
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="game_back")]])
        )
    elif q.data == "game_back":
        await q.edit_message_text(
            "🎮 Cʟɪᴄᴋ Tʜᴇ Bᴜᴛᴛᴏɴ Bᴇʟᴏᴡ Tᴏ Kɴᴏᴡ Aʙᴏᴜᴛ Iᴏᴛᴀ Mɪɴɪ Gᴀᴍᴇꜱ.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📖 View All Games", callback_data="game_list")]])
        )


# ── /games — Animated Games Hub (menu-style category tabs) ────────────────
#
# A single /games command opens a dashboard with category tabs. Tapping a tab
# switches the visible game list in-place (same UX as the /start economy menu
# and the /leaders game-leaderboard panel). Every game is reachable from here.

_GAMES_HUB_CATS = ["cards", "luck", "multi", "quick", "code", "board"]

_GAMES_HUB_INFO = {
    "cards": {
        "icon": "🃏", "title": "Cᴀʀᴅ Gᴀᴍᴇs",
        "body":
            "🃏 /card — Cᴀʀᴅ Gᴀᴍᴇ (4 ʀᴏᴜɴᴅs)\n"
            "🃏 /bet &lt;ᴀᴍᴏᴜɴᴛ&gt; — Cᴀʀᴅ Gᴀᴍᴇ ᴡɪᴛʜ Bᴇᴛ\n"
            "🎭 /bluff — Bʟᴜꜰꜰ Cᴀʀᴅ Gᴀᴍᴇ\n"
            "📊 /rank — Yᴏᴜʀ ᴄᴀʀᴅ ʀᴀɴᴋ",
    },
    "luck": {
        "icon": "🎰", "title": "Lᴜᴄᴋ & Cʜᴀɴᴄᴇ",
        "body":
            "🎰 /slots &lt;ᴀᴍᴏᴜɴᴛ&gt; — Nᴀᴛɪᴠᴇ Sʟᴏᴛ Mᴀᴄʜɪɴᴇ\n"
            "🎡 /wheel — Sᴘɪɴ Tʜᴇ Iᴏᴛᴀ Wʜᴇᴇʟ\n"
            "🎲 /roulette &lt;ᴀᴍᴏᴜɴᴛ&gt; — Bɪᴅ-Eʟɪᴍɪɴᴀᴛɪᴏɴ Tᴏᴜʀɴᴀᴍᴇɴᴛ\n"
            "🤝 /rjoin &lt;ᴀᴍᴏᴜɴᴛ&gt; — Jᴏɪɴ ᴀ Rᴏᴜʟᴇᴛᴛᴇ ɢᴀᴍᴇ\n"
            "🎯 /bid &lt;ᴀᴍᴏᴜɴᴛ&gt; — Bɪᴅ ɪɴ DM ᴇᴀᴄʜ ʀᴏᴜɴᴅ",
    },
    "multi": {
        "icon": "🐺", "title": "Mᴜʟᴛɪᴘʟᴀʏᴇʀ",
        "body":
            "🐺 /werewolf — Sᴏᴄɪᴀʟ Dᴇᴅᴜᴄᴛɪᴏɴ (5-10)\n"
            "🎲 /ludo — Lᴜᴅᴏ Gᴀᴍᴇ\n"
            "💣 /bomb — Bᴏᴍʙ Pᴀssɪɴɢ Gᴀᴍᴇ",
    },
    "quick": {
        "icon": "🕹️", "title": "Qᴜɪᴄᴋ Fᴜɴ",
        "body":
            "🎲 /dice &lt;ᴀᴍᴏᴜɴᴛ&gt; — Dɪᴄᴇ Bᴇᴛ\n"
            "✊ /rps — Rᴏᴄᴋ Pᴀᴘᴇʀ Sᴄɪssᴏʀs\n"
            "⭕ /tictactoe — Tɪᴄ Tᴀᴄ Tᴏᴇ\n"
            "📝 /wordgame — Wᴏʀᴅ Gᴜᴇss\n"
            "❓ /quiz — Aɪ Qᴜɪᴢ\n"
            "🪢 /hangman — Hᴀɴɢᴍᴀɴ",
    },
    "code": {
        "icon": "💻", "title": "Cᴏᴅᴇ & Bʀᴀɪɴ",
        "body":
            "💻 /hack &lt;ʀᴇᴡᴀʀᴅ&gt; &lt;ᴅɪɢɪᴛs&gt; — Hᴀᴄᴋ Tʜᴇ Cᴏᴅᴇ\n"
            "✍️ /register &lt;ᴀᴍᴏᴜɴᴛ&gt; ᴄᴏɪɴs — Jᴏɪɴ ʜᴀᴄᴋ\n"
            "🔑 /guess &ltᴘᴀssᴡᴏʀᴅ&gt; — Mᴀᴋᴇ ᴀ ɢᴜᴇss",
    },
    "board": {
        "icon": "🏆", "title": "Lᴇᴀᴅᴇʀʙᴏᴀʀᴅs",
        "body":
            "🏆 /leaders — Gᴀᴍᴇ Lᴇᴀᴅᴇʀʙᴏᴀʀᴅs\n"
            "   Tᴀᴘ ᴛʜᴇ ɢᴀᴍᴇ ᴛᴀʙ ᴛᴏ sᴡɪᴛᴄʜ ʙᴏᴀʀᴅ!\n"
            "💰 /toprich — Rɪᴄʜᴇsᴛ Pʟᴀʏᴇʀs\n"
            "💀 /topkill — Tᴏᴘ Kɪʟʟᴇʀs",
    },
}


def _games_hub_kb(active: str) -> InlineKeyboardMarkup:
    """Two-row tab selector; active category is marked."""
    row1, row2 = [], []
    for i, key in enumerate(_GAMES_HUB_CATS):
        info = _GAMES_HUB_INFO[key]
        mark = "▸ " if key == active else ""
        btn = InlineKeyboardButton(
            f"{mark}{info['icon']}", callback_data=f"gh_{key}"
        )
        (row1 if i < 3 else row2).append(btn)
    return InlineKeyboardMarkup([row1, row2])


def _games_hub_text(cat: str) -> str:
    info = _GAMES_HUB_INFO[cat]
    return (
        f"{info['icon']} <b>{info['title']}</b>\n"
        f"{'━' * 22}\n\n"
        f"{info['body']}\n\n"
        f"👇 Tᴀᴘ ᴀ ᴄᴀᴛᴇɢᴏʀʏ ᴀʙᴏᴠᴇ ᴛᴏ sᴇᴇ ᴍᴏʀᴇ ɢᴀᴍᴇs."
    )


async def games_hub_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cat = "cards"
    await update.message.reply_html(_games_hub_text(cat), reply_markup=_games_hub_kb(cat))


async def games_hub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    if not d.startswith("gh_"):
        return
    cat = d[3:]
    if cat not in _GAMES_HUB_INFO:
        return
    try:
        await q.edit_message_text(
            _games_hub_text(cat), parse_mode="HTML", reply_markup=_games_hub_kb(cat)
        )
    except Exception as e:
        logger.debug(f"games_hub_callback edit failed for {cat}: {e}")


async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_html("🚫 Yᴏᴜ Cᴀɴ Uꜱᴇ Tʜᴇꜱᴇ Cᴏᴍᴍᴀɴᴅꜱ Iɴ Gʀᴏᴜᴘꜱ Oɴʟʏ."); return
    from utils.helpers import is_admin
    if not await is_admin(update, context):
        await update.message.reply_html("❌ Admins only!"); return

    # 🔴 /open (and /close below) now control ALL THREE closable
    # systems together by default — games, economy, AND village —
    # matching the reference behaviour where /close disables everything
    # at once. A specific system name can still be given to reopen just
    # one of them (e.g. "/open economy") if the admin only wants that.
    args = [a.lower() for a in context.args]
    valid_systems = {"games", "economy", "village"}
    targets = [a for a in args if a in valid_systems] or list(valid_systems)

    await set_system_status(chat.id, **{k: True for k in targets})
    names = ", ".join(t.title() for t in targets)
    await update.message.reply_html(
        f"💚 <b>{names} System{'s' if len(targets) > 1 else ''} OPENED!</b>\n\n"
        f"{'All games, economy, and village commands are' if len(targets) == 3 else names + ' commands are'} "
        f"now available again."
    )


async def close_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_html("🚫 Yᴏᴜ Cᴀɴ Uꜱᴇ Tʜᴇꜱᴇ Cᴏᴍᴍᴀɴᴅꜱ Iɴ Gʀᴏᴜᴘꜱ Oɴʟʏ."); return
    from utils.helpers import is_admin
    if not await is_admin(update, context):
        await update.message.reply_html("❌ Admins only!"); return

    args = [a.lower() for a in context.args]
    valid_systems = {"games", "economy", "village"}
    targets = [a for a in args if a in valid_systems] or list(valid_systems)

    await set_system_status(chat.id, **{k: False for k in targets})
    names = ", ".join(t.title() for t in targets)
    await update.message.reply_html(
        f"🔒 <b>{names} System{'s' if len(targets) > 1 else ''} CLOSED!</b>\n\n"
        f"{'All economy, games, and village commands are' if len(targets) == 3 else names + ' commands are'} "
        f"now disabled in this group.\n"
        f"Reopen with: /open" + (f" {targets[0]}" if len(targets) == 1 else "")
    )


# ── /leaders — Unified game leaderboard panel ────────────────────────────────
#
# A single /leaders command opens a leaderboard with a row of game tabs
# (buttons) under it. Tapping a tab switches the displayed game leaderboard
# in-place (same UX as the /start economy menu). Only GAMES that have a
# real persistent leaderboard are listed here.

_LEADERBOARD_TABS = ["card", "hackers"]
_LEADERBOARD_MEDALS = [
    "🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟",
    "1️⃣1️⃣","1️⃣2️⃣",
]

# Tab metadata: label + short title used in the header.
_LEADERBOARD_INFO = {
    "card":    {"emoji": "🃏", "title": "Iota Coin Tournament"},
    "hackers": {"emoji": "💻", "title": "Hackers"},
}


async def _lb_display_name(uid) -> str:
    """Resolve a display name for a leaderboard row without spamming the
    Telegram API. Prefer the locally-stored users doc; fall back to a chat
    lookup only if that fails."""
    try:
        doc = await get_db().users.find_one(
            {"_id": uid}, {"full_name": 1, "username": 1}
        )
        if doc:
            return doc.get("full_name") or doc.get("username") or f"User {uid}"
    except Exception:
        pass
    return f"User {uid}"


async def _render_leaderboard(game: str) -> str:
    """Build the text body for the given game leaderboard tab."""
    if game == "card":
        rows = await get_card_leaders(len(_LEADERBOARD_MEDALS))
        title = _LEADERBOARD_INFO["card"]["title"]
        text = f"🏆 <b>{title} — Leaderboard</b>\n\n"
        if not rows:
            text += "Nᴏ ɢᴀᴍᴇs ᴘʟᴀʏᴇᴅ ʏᴇᴛ!"
        for i, r in enumerate(rows):
            name = await _lb_display_name(r["_id"])
            text += (
                f"{_LEADERBOARD_MEDALS[i]} <b>{name}</b>\n"
                f"   🏅 Wins: {r.get('wins',0)} | 💸 Lost: {r.get('losses',0)}\n"
                f"   💰 Won: {fmt(r.get('won_amount',0))} | 🔥 Streak: {r.get('best_streak',0)}\n\n"
            )
        return text

    if game == "hackers":
        rows = await get_hack_leaders(len(_LEADERBOARD_MEDALS))
        title = _LEADERBOARD_INFO["hackers"]["title"]
        text = f"💻 <b>{title} — Leaderboard</b>\n\n"
        if not rows:
            text += "Nᴏ ʜᴀᴄᴋꜱ ᴄᴏᴍᴘʟᴇᴛᴇᴅ ʏᴇᴛ!"
        for i, r in enumerate(rows):
            name = await _lb_display_name(r["_id"])
            text += (
                f"{_LEADERBOARD_MEDALS[i]} <b>{name}</b>\n"
                f"   🏅 Hacks: {r.get('wins',0)} | 💰 Won: {fmt(r.get('won_amount',0))}\n"
                f"   🔥 Best Streak: {r.get('best_streak',0)}\n\n"
            )
        return text

    return "❌ Unknown leaderboard."


def _leaderboard_kb(active: str) -> InlineKeyboardMarkup:
    """Build the tab selector keyboard, highlighting the active game."""
    row = []
    for key in _LEADERBOARD_TABS:
        info = _LEADERBOARD_INFO[key]
        mark = "▸ " if key == active else ""
        label = f"{mark}{info['emoji']} {info['title']}"
        row.append(InlineKeyboardButton(label, callback_data=f"lb_{key}"))
    return InlineKeyboardMarkup([row])


async def leaders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game = "card"
    text = await _render_leaderboard(game)
    await update.message.reply_html(text, reply_markup=_leaderboard_kb(game))


async def leaderboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    if not d.startswith("lb_"):
        return
    game = d[3:]
    if game not in _LEADERBOARD_INFO:
        return
    try:
        text = await _render_leaderboard(game)
        await q.edit_message_text(
            text, parse_mode="HTML", reply_markup=_leaderboard_kb(game)
        )
    except Exception as e:
        logger.debug(f"leaderboard_callback edit failed for {game}: {e}")


# ═══════════════════════════════════════════════════════
#  CARD GAME — Full Iota-style
# ═══════════════════════════════════════════════════════

# Real playing-card deck: 52 unique (rank, suit) cards.
_DECK_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
_DECK_SUITS = ["spade", "heart", "diamond", "club"]

# Card power for comparison (A low=1 ... K high=13).
_CARD_POWER = {r: i + 1 for i, r in enumerate(_DECK_RANKS)}


def _gen_hand():
    """Deal 4 distinct real playing cards (rank, suit) from a shuffled deck."""
    deck = [(r, s) for r in _DECK_RANKS for s in _DECK_SUITS]
    random.shuffle(deck)
    return deck[:4]


def _card_power(card) -> int:
    return _CARD_POWER.get(card[0], 1)


def _card_label(card) -> str:
    from utils.card_assets import SUIT_SYMBOLS
    return f"{card[0]}{SUIT_SYMBOLS.get(card[1], '♠')}"


@games_gate
async def card_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u    = update.effective_user
    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_html("🎮 Yᴏᴜ Cᴀɴ Pʟᴀʏ Tʜᴇ Cᴀʀᴅ Gᴀᴍᴇ Iɴ Gʀᴏᴜᴘ Oɴʟʏ."); return

    await ensure_user(u.id, u.username or "", u.full_name)
    gid = str(uuid.uuid4())[:8]
    _card_games[gid] = {
        "chat_id": chat.id, "player1": u.id, "player2": 0,
        "player1_name": u.first_name,  # stored once, avoids a redundant get_chat() call on join
        "bet": 0, "p1_cards": [], "p2_cards": [],
        "p1_choices": {}, "p2_choices": {},
        "round": 1, "score1": 0, "score2": 0,
        "status": "waiting", "created_at": ts(),
    }
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🃏 Join Game", callback_data=f"card_join_{gid}"),
        InlineKeyboardButton("❌ Cancel",    callback_data=f"card_cancel_{gid}"),
    ]])
    await update.message.reply_html(
        f"🃏 <b>Cᴀʀᴅ Gᴀᴍᴇ</b>\n\n"
        f"Host: {mention(u)}\n"
        f"💰 Bet: Free\n\n"
        f"Waiting for player 2... (auto-cancels in {CARD_LOBBY_TIMEOUT_SECONDS}s if nobody joins)",
        reply_markup=kb
    )
    asyncio.create_task(_card_lobby_timeout(context, gid))


@games_gate
async def bet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u    = update.effective_user
    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_html("🎮 Yᴏᴜ Cᴀɴ Pʟᴀʏ Tʜᴇ Cᴀʀᴅ Gᴀᴍᴇ Iɴ Gʀᴏᴜᴘ Oɴʟʏ."); return

    if not context.args:
        await update.message.reply_html(
            f"❌ Usage: /bet {placeholder('amount')}\n"
            f"Bet range: {fmt(CARD_MIN_BET)} – {fmt(CARD_MAX_BET)}"
        ); return
    try:
        amount = int(context.args[0])
    except ValueError:
        # 🔴 Was previously just "Invalid amount!" — now tells the user
        # exactly what's wrong (decimals, letters, etc. all land here).
        await update.message.reply_html("❌ Invalid amount! Bet must be a whole number (no decimals/letters)."); return
    # 🔴 FIX: there was previously NO upper bound at all — Python's
    # arbitrary-precision ints meant even a 30-digit "bet" was accepted
    # as syntactically valid before the balance check caught it. Also
    # no minimum, so a 1-coin "bet" was technically allowed. Added real
    # limits here, consistent with every other betting game in the bot.
    if amount < CARD_MIN_BET:
        await update.message.reply_html(f"❌ Minimum bet is {fmt(CARD_MIN_BET)}!"); return
    if amount > CARD_MAX_BET:
        await update.message.reply_html(f"❌ Maximum bet is {fmt(CARD_MAX_BET)}!"); return

    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)
    if d["balance"] < amount:
        await update.message.reply_html(
            f"❌ Need {fmt(amount)}, you have {fmt(d['balance'])}"
        ); return

    gid = str(uuid.uuid4())[:8]
    _card_games[gid] = {
        "chat_id": chat.id, "player1": u.id, "player2": 0,
        "player1_name": u.first_name,
        "bet": amount, "p1_cards": [], "p2_cards": [],
        "p1_choices": {}, "p2_choices": {},
        "round": 1, "score1": 0, "score2": 0,
        "status": "waiting", "created_at": ts(),
    }
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"🃏 Join (Bet: {fmt(amount)})", callback_data=f"card_join_{gid}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"card_cancel_{gid}"),
    ]])
    await update.message.reply_html(
        f"🃏 <b>Cᴀʀᴅ Gᴀᴍᴇ</b>\n\n"
        f"Host: {mention(u)}\n"
        f"💰 Bet: <b>{fmt(amount)}</b>\n\n"
        f"Waiting for opponent... (auto-cancels + refunds in {CARD_LOBBY_TIMEOUT_SECONDS}s if nobody joins)",
        reply_markup=kb
    )
    asyncio.create_task(_card_lobby_timeout(context, gid))


async def _card_lobby_timeout(context, gid: str):
    """
    🔴 FIX: previously there was NO timeout mechanism at all for the
    card game lobby — an abandoned "waiting for player 2" game (nobody
    joins, or the host forgets about it) sat in memory forever with no
    way to clean it up except someone manually tapping Cancel. Since no
    coins are actually deducted until a second player joins (see
    card_callback below), there's no refund needed here — just cleanup
    — but this closes off the "stuck session forever" class of bug.
    """
    await asyncio.sleep(CARD_LOBBY_TIMEOUT_SECONDS)
    game = _card_games.get(gid)
    if not game or game["status"] != "waiting":
        return  # already joined, cancelled, or otherwise resolved
    _card_games.pop(gid, None)
    try:
        await context.bot.send_message(
            game["chat_id"],
            f"🃏 Card game lobby (host: {game.get('player1_name', 'Player')}) "
            f"timed out — nobody joined in {CARD_LOBBY_TIMEOUT_SECONDS}s. "
            f"Start a new one anytime with /card or /bet!",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.debug(f"_card_lobby_timeout: notify failed for {gid}: {e}")


@games_gate
async def flip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        "❓ Use the buttons inside the card game to flip!\nStart a game: /card or /bet &lt;amount&gt;"
    )


async def card_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    u   = q.from_user
    data = q.data
    await q.answer()

    # ── JOIN ──────────────────────────────────────────────────────────────────
    if data.startswith("card_join_"):
        gid  = data[len("card_join_"):]
        game = _card_games.get(gid)
        if not game:
            await q.edit_message_text("❌ Game expired!"); return
        if game["status"] != "waiting":
            await q.answer("Game already started!", show_alert=True); return
        if game["player1"] == u.id:
            await q.answer("You created this game!", show_alert=True); return

        await ensure_user(u.id, u.username or "", u.full_name)
        p1d = await get_user(game["player1"])
        p2d = await get_user(u.id)
        bet = game["bet"]

        # Deduct bets
        if bet > 0:
            if p2d["balance"] < bet:
                await q.answer("Not enough balance!", show_alert=True); return
            if p1d["balance"] < bet:
                await q.answer("Host doesn't have enough balance!", show_alert=True); return
            await update_user(game["player1"], balance=p1d["balance"] - bet)
            await update_user(u.id, balance=p2d["balance"] - bet)

        game["player2"]   = u.id
        game["p1_cards"]  = _gen_hand()
        game["p2_cards"]  = _gen_hand()
        game["status"]    = "playing"
        _card_games[gid]  = game

        # 🔴 Was calling context.bot.get_chat() here on every single
        # join just to re-fetch the host's name — pointless extra API
        # call for information already known at game-creation time.
        p1_name = game.get("player1_name", "Player1")

        kb = _card_buttons(gid)
        await q.edit_message_text(
            f"🃏 <b>Cᴀʀᴅ Gᴀᴍᴇ Sᴛᴀʀᴛᴇᴅ!</b>\n\n"
            f"⚔️ {p1_name} vs {u.first_name}\n"
            f"💰 Bet: {fmt(bet)}\n\n"
            f"🔵 Round <b>1 / 4</b>\n"
            f"Choose your card (A/B/C/D):",
            parse_mode="HTML",
            reply_markup=kb
        )
        return

    # ── CANCEL ────────────────────────────────────────────────────────────────
    if data.startswith("card_cancel_"):
        gid  = data[len("card_cancel_"):]
        # 🔴 FIX: this used to pop() the game from _card_games BEFORE
        # checking whether the caller was actually the host — so any
        # random player in the group could destroy someone else's game
        # lobby by tapping Cancel, even though the bot would then (too
        # late) tell them "Only host can cancel!". The permission check
        # must happen before any state is mutated, not after.
        game = _card_games.get(gid)
        if not game:
            await q.answer("Game already ended or expired!", show_alert=True); return
        if game["player1"] != u.id:
            await q.answer("Only host can cancel!", show_alert=True); return
        _card_games.pop(gid, None)
        await q.edit_message_text("❌ Card game cancelled!")
        return

    # ── FLIP ──────────────────────────────────────────────────────────────────
    if data.startswith("card_flip_"):
        parts = data.split("_")
        # card_flip_<gid>_<card>
        card  = parts[-1]
        gid   = "_".join(parts[2:-1])
        game  = _card_games.get(gid)
        if not game or game["status"] != "playing":
            await q.answer("No active game!", show_alert=True); return
        if u.id not in (game["player1"], game["player2"]):
            await q.answer("You're not in this game!", show_alert=True); return

        is_p1   = u.id == game["player1"]
        choices = game["p1_choices"] if is_p1 else game["p2_choices"]
        rnd     = str(game["round"])

        if rnd in choices:
            await q.answer("Already chose this round!", show_alert=True); return

        card_idx = {"A":0,"B":1,"C":2,"D":3}[card]
        cards    = game["p1_cards"] if is_p1 else game["p2_cards"]
        chosen   = cards[card_idx]          # (rank, suit) real card
        choices[rnd] = (card, chosen)

        if is_p1:
            game["p1_choices"] = choices
        else:
            game["p2_choices"] = choices

        # Both chose?
        if rnd in game["p1_choices"] and rnd in game["p2_choices"]:
            await _resolve_round(q, context, gid)
        else:
            await q.answer(f"✅ Card {card} selected! Waiting for opponent...", show_alert=False)


def _card_buttons(gid):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🅰️ A", callback_data=f"card_flip_{gid}_A"),
        InlineKeyboardButton("🅱️ B", callback_data=f"card_flip_{gid}_B"),
        InlineKeyboardButton("©️ C", callback_data=f"card_flip_{gid}_C"),
        InlineKeyboardButton("🅾️ D", callback_data=f"card_flip_{gid}_D"),
    ]])


async def _resolve_round(q, context, gid):
    game  = _card_games.get(gid)
    if not game:
        return
    rnd   = str(game["round"])
    p1c, p1card = game["p1_choices"][rnd]   # p1c = slot letter, p1card = (rank, suit)
    p2c, p2card = game["p2_choices"][rnd]
    p1v = _card_power(p1card)
    p2v = _card_power(p2card)
    p1lbl = _card_label(p1card)
    p2lbl = _card_label(p2card)

    if p1v > p2v:
        game["score1"] += p1v
        rnd_winner = "p1"
    elif p2v > p1v:
        game["score2"] += p2v
        rnd_winner = "p2"
    else:
        game["score1"] += p1v
        game["score2"] += p2v
        rnd_winner = "tie"

    try:
        p1u = await context.bot.get_chat(game["player1"])
        p2u = await context.bot.get_chat(game["player2"])
        p1_name = p1u.first_name
        p2_name = p2u.first_name
    except Exception:
        p1_name = "Player1"; p2_name = "Player2"

    rw_text = p1_name if rnd_winner=="p1" else (p2_name if rnd_winner=="p2" else "🤝 TIE")

    # Send the two played real cards as one composed image.
    try:
        from utils.card_assets import get_card_image, compose_cards_bytes
        imgs = [get_card_image(p1card[0], p1card[1]),
                get_card_image(p2card[0], p2card[1])]
        art = compose_cards_bytes(imgs, label=f"{p1_name}  vs  {p2_name}")
        await context.bot.send_photo(game["chat_id"], art, parse_mode="HTML",
                                     caption=f"🃏 Rᴏᴜɴᴅ {rnd}: {p1_name} {p1lbl}  vs  {p2_name} {p2lbl} — Wɪɴɴᴇʀ: {rw_text}")
    except Exception as e:
        logger.debug(f"card round image failed: {e}")

    if game["round"] >= 4:
        # Game over
        await _end_game(q, context, gid, p1_name, p2_name, p1c, p1v, p2c, p2v, rw_text)
    else:
        game["round"] += 1
        _card_games[gid] = game
        kb = _card_buttons(gid)
        await q.edit_message_text(
            f"🃏 <b>Cᴀʀᴅ Gᴀᴍᴇ</b>\n\n"
            f"Round {rnd} result:\n"
            f"🔵 {p1_name}: {p1lbl} = <b>{p1v}</b>\n"
            f"🔴 {p2_name}: {p2lbl} = <b>{p2v}</b>\n"
            f"Round Winner: <b>{rw_text}</b>\n\n"
            f"Score — {p1_name}: {game['score1']} | {p2_name}: {game['score2']}\n\n"
            f"🔵 Round <b>{game['round']} / 4</b> — Choose your card:",
            parse_mode="HTML",
            reply_markup=kb
        )


async def _bot_can_pin(context, chat_id) -> bool:
    """True only if the bot itself is an admin with pin permission in
    this chat (or the chat creator, which always can). Never assumes —
    always checks live, since permissions can change between games."""
    try:
        member = await context.bot.get_chat_member(chat_id, context.bot.id)
        if member.status == "creator":
            return True
        if member.status == "administrator":
            return bool(getattr(member, "can_pin_messages", False))
        return False
    except Exception:
        return False


async def _announce_final_winner(context, chat_id: int, winner_id: int | None, result_text: str):
    """
    🆕 Sends the final-winner card game result. Tries, in order:
    1. The winner's own profile photo as the image (what was asked for
       — "final winner ka photo bhi aaye").
    2. A mood GIF (previous behaviour), if no profile photo exists.
    3. Plain text, if both of the above fail for any reason.
    Then auto-pins the sent message, but ONLY if the bot actually has
    pin permission in this chat — checked live via _bot_can_pin(), never
    assumed, so this never raises a permission error for groups where
    the bot isn't an admin.
    """
    sent = None
    if winner_id:
        try:
            photos = await context.bot.get_user_profile_photos(winner_id, limit=1)
            if photos and photos.total_count > 0:
                file_id = photos.photos[0][-1].file_id
                sent = await context.bot.send_photo(chat_id, file_id, caption=result_text, parse_mode="HTML")
        except Exception as e:
            logger.debug(f"_announce_final_winner: profile photo failed: {e}")

    if sent is None:
        try:
            win_gif = await get_gif_for_mood("card_win")
            if win_gif:
                sent = await context.bot.send_animation(chat_id, animation=win_gif, caption=result_text, parse_mode="HTML")
        except Exception as e:
            logger.debug(f"_announce_final_winner: gif fallback failed: {e}")

    if sent is None:
        try:
            sent = await context.bot.send_message(chat_id, result_text, parse_mode="HTML")
        except Exception as e:
            logger.debug(f"_announce_final_winner: text fallback failed: {e}")
            return

    if await _bot_can_pin(context, chat_id):
        try:
            await context.bot.pin_chat_message(chat_id, sent.message_id, disable_notification=True)
        except Exception as e:
            logger.debug(f"_announce_final_winner: pin failed: {e}")


async def _end_game(q, context, gid, p1_name, p2_name, p1c, p1v, p2c, p2v, rw_text):
    game = _card_games.pop(gid, None)
    if not game:
        return

    # Send a composed image of both players' final (4th-round) cards.
    try:
        from utils.card_assets import get_card_image, compose_cards_bytes
        final_rnd = str(max(1, game["round"]))
        p1_final = game["p1_choices"].get(final_rnd, (None, ("A", "spade")))[1]
        p2_final = game["p2_choices"].get(final_rnd, (None, ("A", "spade")))[1]
        imgs = [get_card_image(p1_final[0], p1_final[1]),
                get_card_image(p2_final[0], p2_final[1])]
        art = compose_cards_bytes(imgs, label=f"🏆 {rw_text}")
        await context.bot.send_photo(game["chat_id"], art, parse_mode="HTML",
                                     caption=f"🃏 Fɪɴᴀʟ Cᴀʀᴅs — {p1_name} vs {p2_name}")
    except Exception as e:
        logger.debug(f"card final image failed: {e}")

    s1 = game["score1"]; s2 = game["score2"]
    bet = game["bet"]
    p1_id = game["player1"]; p2_id = game["player2"]

    p1d = await get_user(p1_id)
    p2d = await get_user(p2_id)
    cr1 = await get_card_rank(p1_id)
    cr2 = await get_card_rank(p2_id)

    is_tie = (s1 == s2)

    if is_tie:
        # Tie logic (Iota style): normal users eliminated, premium share
        p1_prem = p1d["is_premium"]
        p2_prem = p2d["is_premium"]

        # 🔴 FIX: the tie-branch message always claimed a fee was taken
        # ("Won: X (10% Fee)") but the prize math here never actually
        # deducted one — only the non-tie branch below did. Now the fee
        # is genuinely applied in the "both normal" tie case too,
        # consistently with the non-tie branch, and premium players
        # stay fee-exempt in both, matching what the message claims.
        if p1_prem and p2_prem:
            # Both premium: split prize, no fee (fee-exempt)
            prize = bet * 2 if bet > 0 else 0
            each = prize // 2
            if each > 0:
                await add_balance(p1_id, each)
                await add_balance(p2_id, each)
            tie_result_text = f"⚖️ Tɪᴇ Dᴇᴛᴇᴄᴛᴇᴅ! Nᴏʀᴍᴀʟ ᴜꜱᴇʀꜱ ᴇʟɪᴍɪɴᴀᴛᴇᴅ, Pʀᴇᴍɪᴜᴍ Pʟᴀʏᴇʀꜱ ꜱʜᴀʀᴇ Pʀɪᴢᴇ 💓"
            winner_ids = [p1_id, p2_id]
            prize_each = each
            fee_applied = 0  # both premium — fee-exempt, matches non-tie branch's rule
        elif p1_prem:
            gross = bet*2 if bet else 0
            prize_each = gross  # premium winner — fee-exempt
            winner_ids = [p1_id]
            if prize_each: await add_balance(p1_id, prize_each)
            tie_result_text = f"⚖️ Tɪᴇ! Nᴏʀᴍᴀʟ ᴜꜱᴇʀ ᴇʟɪᴍɪɴᴀᴛᴇᴅ, Pʀᴇᴍɪᴜᴍ Wɪɴꜱ 💓"
            fee_applied = 0
        elif p2_prem:
            gross = bet*2 if bet else 0
            prize_each = gross  # premium winner — fee-exempt
            winner_ids = [p2_id]
            if prize_each: await add_balance(p2_id, prize_each)
            tie_result_text = f"⚖️ Tɪᴇ! Nᴏʀᴍᴀʟ ᴜꜱᴇʀ ᴇʟɪᴍɪɴᴀᴛᴇᴅ, Pʀᴇᴍɪᴜᴍ Wɪɴꜱ 💓"
            fee_applied = 0
        else:
            # Both normal: random winner, fee genuinely applies here now
            winner_id = random.choice([p1_id, p2_id])
            gross = bet*2 if bet else 0
            fee_applied = int(gross * CARD_FEE_PERCENT / 100)
            prize_each = gross - fee_applied
            winner_ids = [winner_id]
            if prize_each: await add_balance(winner_id, prize_each)
            tie_result_text = f"⚖️ Tɪᴇ! Rᴀɴᴅᴏᴍ Wɪɴɴᴇʀ Sᴇʟᴇᴄᴛᴇᴅ"

        # Card rank for tie
        # 🔴 FIX: previously this loop computed new_streak/best_streak
        # for the DISPLAY message using the pre-update cr["best_streak"],
        # then separately recomputed a slightly different value for the
        # DB write — the two could disagree, and the display used a
        # meaningless "+1" fudge factor. Now we track the actual final
        # values per winner and use those consistently in both places.
        streak_display = {}
        for wid in winner_ids:
            cr = await get_card_rank(wid)
            new_streak = cr["streak"] + 1
            final_best = max(cr["best_streak"], new_streak)
            await update_card_rank(wid,
                wins=cr["wins"]+1, won_amount=cr["won_amount"]+prize_each,
                streak=new_streak,
                best_streak=final_best
            )
            streak_display[wid] = (new_streak, final_best)

        # Build result message
        winner_name = p1_name if (winner_ids[0] if len(winner_ids)==1 else 0) == p1_id else p2_name
        if len(winner_ids) == 2:
            winner_display = f"🏅 {p1_name} & {p2_name}"
        else:
            winner_display = f"👑 {winner_name}"

        # Streak line: show each winner's actual current/best streak
        # rather than a single potentially-misleading "N/A".
        if streak_display:
            streak_line = ", ".join(f"{s}/{b}" for s, b in streak_display.values())
        else:
            streak_line = "N/A"

        fee_note = f" (🎿 {CARD_FEE_PERCENT}% Fee)" if fee_applied > 0 else " (💓 No Fee)"
        result_text = (
            f"{tie_result_text}\n\n"
            f"👑 Fɪɴᴀʟ Wɪɴɴᴇʀ(ꜱ) 👑\n\n"
            f"🎿 {winner_display}\n"
            f"🎯 Pᴏɪɴᴛꜱ: {max(s1,s2)}\n"
            f"💵 Wᴏɴ: {fmt(prize_each)}{fee_note}\n"
            f"🔥 Sᴛʀᴇᴀᴋ: {streak_line}\n"
            f"⚡ Xᴘ Gᴀɪɴᴇᴅ: +{CARD_XP_WIN}\n\n"
            f"👥 Mᴇᴍʙᴇʀꜱ: 👤🎿👤👤\n\n"
            f"👉 Pʟᴀʏ Aɢᴀɪɴ Uꜱɪɴɢ : /card {bet if bet else ''}"
        )

        try:
            tie_gif = await get_gif_for_mood("card_tie")
            if tie_gif:
                await context.bot.send_animation(
                    game["chat_id"],
                    animation=tie_gif,
                    caption=result_text,
                    parse_mode="HTML"
                )
            else:
                await context.bot.send_message(game["chat_id"], result_text, parse_mode="HTML")
        except Exception:
            await context.bot.send_message(game["chat_id"], result_text, parse_mode="HTML")
        return

    # Normal win
    if s1 > s2:
        winner_id = p1_id; loser_id = p2_id; winner_name = p1_name; loser_name = p2_name
        winner_score = s1
    else:
        winner_id = p2_id; loser_id = p1_id; winner_name = p2_name; loser_name = p1_name
        winner_score = s2

    # Calculate prize
    prize = bet * 2 if bet > 0 else 0
    wd = await get_user(winner_id)
    fee_exempt = wd["is_premium"]
    fee = 0 if fee_exempt else int(prize * CARD_FEE_PERCENT / 100)
    net = prize - fee
    if net > 0:
        await add_balance(winner_id, net)

    # XP
    await update_user(winner_id, xp=(await get_user(winner_id))["xp"] + CARD_XP_WIN)
    await update_user(loser_id,  xp=(await get_user(loser_id))["xp"]  + CARD_XP_LOSS)

    # Card rank winner
    crw = await get_card_rank(winner_id)
    new_streak = crw["streak"] + 1
    await update_card_rank(winner_id,
        wins=crw["wins"]+1, won_amount=crw["won_amount"]+net,
        streak=new_streak,
        best_streak=max(crw["best_streak"], new_streak)
    )
    # Card rank loser
    crl = await get_card_rank(loser_id)
    await update_card_rank(loser_id,
        losses=crl["losses"]+1, lost_amount=crl["lost_amount"]+bet,
        streak=0
    )

    result_text = (
        f"👑 Fɪɴᴀʟ Wɪɴɴᴇʀ(ꜱ) 👑\n\n"
        f"🎿 {winner_name}\n"
        f"🎯 Pᴏɪɴᴛꜱ: {winner_score}\n"
        f"💵 Wᴏɴ: {fmt(net)} (🎿 {CARD_FEE_PERCENT}% Fee)\n"
        f"🔥 Sᴛʀᴇᴀᴋ: {new_streak}/{crw['best_streak']+1}\n"
        f"⚡ Xᴘ Gᴀɪɴᴇᴅ: +{CARD_XP_WIN}\n\n"
        f"👥 Mᴇᴍʙᴇʀꜱ: 👤🎿👤👤\n\n"
        f"👉 Pʟᴀʏ Aɢᴀɪɴ Uꜱɪɴɢ : /card {bet if bet else ''}"
    )

    try:
        from utils.progress import record_game_result
        await record_game_result(winner_id, won=True, bet=bet, game="card")
        await record_game_result(loser_id, won=False, bet=bet, game="card")
    except Exception:
        pass

    await _announce_final_winner(context, game["chat_id"], winner_id, result_text)


# ═══════════════════════════════════════════════════════
#  BOMB GAME
# ═══════════════════════════════════════════════════════

@games_gate
async def bomb_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_html("🎮 Gʀᴏᴜᴘ Oɴʟʏ!"); return
    await ensure_user(u.id, u.username or "", u.full_name)
    gid = str(uuid.uuid4())[:8]
    _bomb_games[gid] = {
        "chat_id": chat.id, "players": [u.id],
        "holder": u.id, "status": "waiting", "bet": 0
    }
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("💣 Join!", callback_data=f"bomb_join_{gid}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"bomb_cancel_{gid}"),
    ]])
    await update.message.reply_html(
        f"💣 <b>Bomb Game!</b>\n\nHost: {mention(u)}\n"
        f"Pass the bomb before it explodes! (30s)\nWaiting for players...",
        reply_markup=kb
    )


async def bomb_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; u = q.from_user; await q.answer()

    if q.data.startswith("bomb_join_"):
        gid  = q.data[len("bomb_join_"):]
        game = _bomb_games.get(gid)
        if not game: await q.edit_message_text("❌ Expired!"); return
        if u.id in game["players"]: await q.answer("Already joined!", show_alert=True); return
        game["players"].append(u.id)
        if len(game["players"]) >= 2:
            game["status"] = "playing"
            _bomb_games[gid] = game
            names = []
            for pid in game["players"]:
                try:
                    c = await context.bot.get_chat(pid)
                    names.append(c.first_name)
                except Exception as e:
                    logger.debug(f"Suppressed error in games.py: {e}")
                    names.append(str(pid))
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("💣 Pass Bomb!", callback_data=f"bomb_pass_{gid}")
            ]])
            await q.edit_message_text(
                f"💣 <b>Bomb Game Started!</b>\nPlayers: {', '.join(names)}\n\n"
                f"🔥 <b>{names[0]}</b> has the bomb!\n⏱️ 30 seconds!",
                parse_mode="HTML", reply_markup=kb
            )
            asyncio.create_task(_bomb_explode(context, gid, game["chat_id"]))
        else:
            await q.edit_message_text(
                f"💣 Waiting... {len(game['players'])} player(s) joined.",
                reply_markup=q.message.reply_markup
            )

    elif q.data.startswith("bomb_pass_"):
        gid  = q.data[len("bomb_pass_"):]
        game = _bomb_games.get(gid)
        if not game or game["status"] != "playing":
            await q.answer("No active game!", show_alert=True); return
        if u.id != game["holder"]:
            await q.answer("You don't have the bomb! 💣", show_alert=True); return
        idx  = game["players"].index(u.id)
        nxt  = game["players"][(idx+1) % len(game["players"])]
        game["holder"] = nxt; _bomb_games[gid] = game
        try:
            nc = await context.bot.get_chat(nxt)
            nname = nc.first_name
        except Exception as e:
            logger.debug(f"Suppressed error in games.py: {e}")
            nname = str(nxt)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("💣 Pass Bomb!", callback_data=f"bomb_pass_{gid}")
        ]])
        await q.edit_message_text(
            f"💣 <b>Bomb Game!</b>\n🔥 <b>{nname}</b> has the bomb!\n⏱️ 30 seconds!",
            parse_mode="HTML", reply_markup=kb
        )

    elif q.data.startswith("bomb_cancel_"):
        gid = q.data[len("bomb_cancel_"):]
        _bomb_games.pop(gid, None)
        await q.edit_message_text("❌ Bomb game cancelled!")


async def _bomb_explode(context, gid, chat_id):
    await asyncio.sleep(30)
    game = _bomb_games.pop(gid, None)
    if not game or game["status"] != "playing": return
    try:
        hc = await context.bot.get_chat(game["holder"])
        hname = hc.first_name
    except Exception as e:
        logger.debug(f"Suppressed error in games.py: {e}")
        hname = str(game["holder"])
    try:
        await _send_art(context, chat_id,
                       lambda: render_bomb(defused=False, seconds_left=0,
                                           wires=["red", "green", "blue", "yellow"]),
                       caption="💥 ʙᴏᴍʙ ᴅᴇғᴜsᴇ ʙᴏᴀʀᴅ")
        await context.bot.send_message(
            chat_id,
            f"💥 <b>BOOM!</b>\n💀 <b>{hname}</b> got blown up! Everyone else survives! 🎉",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.debug(f"Suppressed error in games.py: {e}")
        pass


# ═══════════════════════════════════════════════════════
#  BLUFF GAME
# ═══════════════════════════════════════════════════════

# ── (Bluff game moved to handlers/bluff_game.py — /bluff /enter /drop /judge) ──


# ── (Password Hacking game moved to handlers/hack_game.py — /hack /register /guess /end) ──

# ── wordgame only (ludo moved to handlers/ludo.py) ───────────────────────────


@games_gate
async def wordgame_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; chat = update.effective_chat
    await ensure_user(u.id, u.username or "", u.full_name)
    word   = random.choice(WORD_LIST)
    reward = len(word) * 200
    _word_games[chat.id] = {"word": word, "masked": list("_"*len(word)), "reward": reward, "attempts": 6}
    await update.message.reply_html(
        f"📝 <b>Word Game!</b>\nHost: {mention(u)}\n💰 Reward: {fmt(reward)}\n\n"
        f"Word: <code>{' '.join('_'*len(word))}</code>  ({len(word)} letters)\n"
        f"Attempts: 6\n\nReply with a single letter!"
    )


async def wordgame_letter_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; u = update.effective_user
    game = _word_games.get(chat.id)
    if not game: return
    text = (update.message.text or "").strip().lower()
    if len(text) != 1 or not text.isalpha(): return
    word = game["word"]; masked = game["masked"]
    if text in word:
        for i, ch in enumerate(word):
            if ch == text: masked[i] = text
        game["masked"] = masked
        if "_" not in masked:
            _word_games.pop(chat.id, None)
            await add_balance(u.id, game["reward"])
            await update.message.reply_html(
                f"🎉 {mention(u)} guessed: <b>{word}</b>!\n💰 +{fmt(game['reward'])}"
            )
        else:
            await update.message.reply_html(
                f"✅ '{text}' found!\n<code>{' '.join(masked)}</code>\nAttempts: {game['attempts']}"
            )
    else:
        game["attempts"] -= 1
        if game["attempts"] <= 0:
            _word_games.pop(chat.id, None)
            await update.message.reply_html(f"❌ Game over! Word: <b>{word}</b>")
        else:
            await update.message.reply_html(
                f"❌ '{text}' not in word!\n<code>{' '.join(masked)}</code>\nAttempts: {game['attempts']}"
            )


# ── /dice ────────────────────────────────────────────────────────────────────
_DICE_FACES = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}

@games_gate
async def dice_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /dice <amount> — roll a die against Iota's die.
    Higher roll wins 2x your bet (5% fee for non-premium, premium exempt),
    a tie pushes (your bet is returned), a lower roll loses the bet.
    """
    u = update.effective_user
    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_html("🎲 Yᴏᴜ Cᴀɴ Pʟᴀʏ Dɪᴄᴇ Iɴ Gʀᴏᴜᴘ Oɴʟʏ."); return
    if not context.args:
        await update.message.reply_html(
            f"❌ {sc('Usage')}: /dice {placeholder('amount')}\n"
            f"{sc('Bet range')}: {fmt(CARD_MIN_BET)} – {fmt(CARD_MAX_BET)}"
        ); return
    try:
        amount = int(context.args[0])
    except ValueError:
        await update.message.reply_html("❌ Invalid amount! Bet must be a whole number."); return
    if amount < CARD_MIN_BET:
        await update.message.reply_html(f"❌ {sc('Minimum bet is')} {fmt(CARD_MIN_BET)}!"); return
    if amount > CARD_MAX_BET:
        await update.message.reply_html(f"❌ {sc('Maximum bet is')} {fmt(CARD_MAX_BET)}!"); return

    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)
    if d["balance"] < amount:
        await update.message.reply_html(
            f"❌ {sc('Need')} {fmt(amount)}, {sc('you have')} {fmt(d['balance'])}"
        ); return

    await deduct_balance(u.id, amount)
    p = random.randint(1, 6)
    b = random.randint(1, 6)

    if p > b:
        winnings = amount
        if not d.get("is_premium"):
            winnings = int(winnings * (100 - CARD_FEE_PERCENT) / 100)
        await add_balance(u.id, amount + winnings)
        await update.message.reply_html(
            f"🎲 {mention(u)}\n"
            f"{sc('You')}: {_DICE_FACES[p]}   {sc('Iota')}: {_DICE_FACES[b]}\n"
            f"🏆 {sc('You Win')}!  💰 +{fmt(winnings)}"
        )
        try:
            from utils.progress import record_game_result
            await record_game_result(u.id, won=True, bet=amount, game="dice")
        except Exception:
            pass
        await _send_art(context, chat.id, lambda: _render_dice_row([p, b]),
                        caption="🎲 ʀᴏʟʟ ʀᴇsᴜʟᴛ")
    elif p == b:
        await add_balance(u.id, amount)
        await update.message.reply_html(
            f"🎲 {mention(u)}\n"
            f"{sc('You')}: {_DICE_FACES[p]}   {sc('Iota')}: {_DICE_FACES[b]}\n"
            f"🤝 {sc('Tie')}! {sc('Bet returned')} {fmt(amount)}"
        )
        await _send_art(context, chat.id, lambda: _render_dice_row([p, b]),
                        caption="🎲 ʀᴏʟʟ ʀᴇsᴜʟᴛ")
    else:
        await update.message.reply_html(
            f"🎲 {mention(u)}\n"
            f"{sc('You')}: {_DICE_FACES[p]}   {sc('Iota')}: {_DICE_FACES[b]}\n"
            f"💀 {sc('You Lose')}!  💰 -{fmt(amount)}"
        )
        await _send_art(context, chat.id, lambda: _render_dice_row([p, b]),
                        caption="🎲 ʀᴏʟʟ ʀᴇsᴜʟᴛ")
