"""
╔══════════════════════════════════════════════════╗
║     IOTA BOT — Professional Ludo Game           ║
║  2-4 Players | Dice | Full Board | Coins        ║
╚══════════════════════════════════════════════════╝

Features:
- 2 to 4 players
- Real Ludo board logic (safe cells, home path, star cells)
- Coin bet system
- Turn timer (60s per turn)
- Visual board via text art
- Kill & capture mechanics
- Win detection
- GIF celebration
"""

import random, asyncio, uuid, time, logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from utils.mongo_db import ensure_user, get_user, add_balance, deduct_balance
from utils.helpers import mention, fmt
from utils.safe_html import safe_html
from utils.fonts import sc
from utils.system_gate import games_gate
from utils.game_art import send_game_art as _send_art, render_ludo_share as _render_ludo_share

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
#  LUDO CONSTANTS
# ─────────────────────────────────────────────────────────────

# Player colors & emojis
COLORS = {
    "red":    {"emoji": "🔴", "home": "🏠", "piece": "🔴"},
    "blue":   {"emoji": "🔵", "home": "🏠", "piece": "🔵"},
    "green":  {"emoji": "🟢", "home": "🏠", "piece": "🟢"},
    "yellow": {"emoji": "🟡", "home": "🏠", "piece": "🟡"},
}
COLOR_LIST = ["red", "blue", "green", "yellow"]

# Board path (52 cells), 0 = not started (yard), 1-51 = path, 52+ = home stretch
BOARD_SIZE = 52
HOME_STRETCH = 6  # extra cells to reach home after lap
WINNING_POS  = BOARD_SIZE + HOME_STRETCH  # = 58

# Safe cells (star positions on real Ludo board)
SAFE_CELLS = {1, 9, 14, 22, 27, 35, 40, 48}

# Starting positions for each color (global board position)
START_POSITIONS = {
    "red":    1,
    "blue":   14,
    "green":  27,
    "yellow": 40,
}

# Home entry positions (last safe cell before home stretch)
HOME_ENTRY = {
    "red":    51,
    "blue":   12,
    "green":  25,
    "yellow": 38,
}

LUDO_GIF = "https://media.giphy.com/media/l4FGuhL4U2WyjdkaY/giphy.gif"
WIN_GIF  = "https://media.giphy.com/media/3o7abKhOpu0NwenH3O/giphy.gif"

# Active ludo games: game_id -> game_state
_ludo_games: dict = {}

# ─────────────────────────────────────────────────────────────
#  GAME STATE HELPERS
# ─────────────────────────────────────────────────────────────

def _new_game(chat_id: int, host_id: int, host_name: str, bet: int = 0) -> dict:
    gid = str(uuid.uuid4())[:8]
    return {
        "id":        gid,
        "chat_id":   chat_id,
        "bet":       bet,
        "status":    "waiting",   # waiting | playing | finished
        "players":   [{
            "id":    host_id,
            # 🔴 Player display names come straight from Telegram
            # (first_name), which is fully user-controlled — a user
            # could set their name to include "<" or ">" and break every
            # HTML-mode game message that displays it. Escaping once
            # here, at the point names enter game state, means every
            # downstream <b>{p['name']}</b> usage throughout this file
            # is automatically safe with no per-call-site changes needed.
            "name":  safe_html(host_name),
            "color": "red",
            "pieces": [0, 0, 0, 0],   # 0=yard, 1-57=board, 58=home
            "finished_pieces": 0,
            "score": 0,
        }],
        "turn":      0,            # index in players list
        "dice":      0,
        "last_roll": 0,
        "winner":    None,
        "created":   int(time.time()),
        "turn_deadline": 0,
    }


def _can_move(piece_pos: int, dice: int, color: str) -> bool:
    """Check if a piece can legally move."""
    if piece_pos == 0:
        return dice == 6  # Must roll 6 to leave yard
    new_pos = _calc_new_pos(piece_pos, dice, color)
    # A piece that would overshoot past WINNING_POS returns its own
    # current position unchanged (see _calc_new_pos) — that's NOT a
    # legal move, so explicitly exclude the no-op case here too.
    return new_pos != piece_pos and new_pos <= WINNING_POS


def _calc_new_pos(current: int, dice: int, color: str) -> int:
    """Calculate new position after rolling dice."""
    if current == 0:
        if dice == 6:
            return START_POSITIONS[color]
        return 0

    # 🔴 CRITICAL FIX: a piece already in its home stretch (53-58) is NOT
    # on the shared 52-cell outer ring anymore. The old code re-applied
    # the outer-track modulo formula here regardless, which produced a
    # nonsensical position for ANY piece past position 52 — making it
    # effectively impossible to ever legally finish a game (the piece
    # would appear to teleport back onto the outer track instead of
    # advancing toward home). Home-stretch movement must be a simple
    # straight-line add capped at WINNING_POS.
    if current > BOARD_SIZE:
        new_pos = current + dice
        return new_pos if new_pos <= WINNING_POS else current  # overshoot = illegal, no move

    # Convert to relative position on board
    start = START_POSITIONS[color]
    # Relative position from start (0-indexed)
    rel = (current - start) % BOARD_SIZE

    new_rel = rel + dice
    if new_rel >= BOARD_SIZE:
        # Entering home stretch
        home_pos = BOARD_SIZE + (new_rel - BOARD_SIZE)
        if home_pos > WINNING_POS:
            return current  # Can't move, need exact
        return home_pos
    
    # Convert back to absolute
    new_abs = (start + new_rel - 1) % BOARD_SIZE + 1
    return new_abs


def _is_safe(pos: int) -> bool:
    return pos in SAFE_CELLS or pos > BOARD_SIZE


def _check_capture(game: dict, mover_color: str, new_pos: int) -> list:
    """Check if any opponent pieces get captured at new_pos. Returns list of captured names."""
    if _is_safe(new_pos):
        return []
    captured = []
    for p in game["players"]:
        if p["color"] == mover_color:
            continue
        for i, pp in enumerate(p["pieces"]):
            if pp == new_pos:
                p["pieces"][i] = 0  # send back to yard
                captured.append(p["name"])
    return captured


def _count_movable(pieces: list, dice: int, color: str) -> int:
    return sum(1 for pos in pieces if _can_move(pos, dice, color))


def _has_won(pieces: list) -> bool:
    return all(p == WINNING_POS for p in pieces)


# ─────────────────────────────────────────────────────────────
#  VISUAL BOARD (TEXT ART)
# ─────────────────────────────────────────────────────────────

def _render_board(game: dict) -> str:
    """Render a compact visual board."""
    # Build position map: pos -> list of color emojis
    pos_map: dict[int, list] = {}
    for p in game["players"]:
        col = COLORS[p["color"]]["emoji"]
        for pp in p["pieces"]:
            if pp == 0:  # in yard
                pos_map.setdefault(-1, []).append(col)
            elif pp == WINNING_POS:
                pos_map.setdefault(99, []).append(col)
            else:
                pos_map.setdefault(pp, []).append(col)

    def cell(n):
        pieces = pos_map.get(n, [])
        if not pieces:
            if n in SAFE_CELLS:
                return "⭐"
            return "⬜"
        if len(pieces) == 1:
            return pieces[0]
        return "💥"

    # Top row (cells 1-13)
    row1  = "".join(cell(i) for i in range(1, 14))
    # Right column (14-26)
    right = [cell(i) for i in range(14, 27)]
    # Bottom row (27-39) reversed
    row3  = "".join(cell(i) for i in range(39, 26, -1))
    # Left column (40-52) reversed
    left  = [cell(i) for i in range(52, 39, -1)]

    lines = []
    lines.append(f"🎲 <b>{sc('Iota Ludo Board')}</b> 🎲")
    lines.append(f"┌{'─'*13}┐")
    lines.append(f"│{row1}│")
    for i in range(13):
        l = left[i]  if i < len(left)  else "  "
        r = right[i] if i < len(right) else "  "
        lines.append(f"{l}{'  '*5}⬛{'  '*5}{r}")
    lines.append(f"│{row3}│")
    lines.append(f"└{'─'*13}┘")
    lines.append(f"🏆 Home: {cell(99)}")
    return "\n".join(lines)


def _render_scoreboard(game: dict) -> str:
    turn_player = game["players"][game["turn"]]
    lines = ["👥 <b>" + sc("Players") + ":</b>"]
    for p in game["players"]:
        col   = COLORS[p["color"]]["emoji"]
        home  = p["pieces"].count(WINNING_POS)
        yard  = p["pieces"].count(0)
        arrow = "👉 " if p["id"] == turn_player["id"] else "   "
        lines.append(f"{arrow}{col} <b>{p['name']}</b> — 🏠×{home} 🎯×{4-home-yard}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
#  KEYBOARD BUILDERS
# ─────────────────────────────────────────────────────────────

def _lobby_kb(gid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Join Game", callback_data=f"ludo_join_{gid}"),
         InlineKeyboardButton("❌ Cancel",    callback_data=f"ludo_cancel_{gid}")],
        [InlineKeyboardButton("▶️ Start Now", callback_data=f"ludo_start_{gid}")],
    ])


def _roll_kb(gid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🎲 Roll Dice!", callback_data=f"ludo_roll_{gid}")
    ]])


def _piece_kb(gid: str, movable_indices: list) -> InlineKeyboardMarkup:
    labels = ["①", "②", "③", "④"]
    btns = [
        InlineKeyboardButton(labels[i], callback_data=f"ludo_move_{gid}_{i}")
        for i in movable_indices
    ]
    # Split into rows of 2
    rows = [btns[i:i+2] for i in range(0, len(btns), 2)]
    return InlineKeyboardMarkup(rows)


# ─────────────────────────────────────────────────────────────
#  LOBBY TEXT
# ─────────────────────────────────────────────────────────────

def _lobby_text(game: dict) -> str:
    plist = "\n".join(
        f"  {COLORS[p['color']]['emoji']} {p['name']}"
        for p in game["players"]
    )
    bet_txt = f"💰 Bet: <b>{fmt(game['bet'])}</b> per player" if game["bet"] else "🆓 Free Game"
    return (
        f"🎲 <b>{sc('Iota Ludo')}</b>\n\n"
        f"{bet_txt}\n"
        f"👥 Players ({len(game['players'])}/4):\n{plist}\n\n"
        f"⏳ Waiting for more players...\n"
        f"Min 2 players needed to start!"
    )


# ─────────────────────────────────────────────────────────────
#  /ludo COMMAND
# ─────────────────────────────────────────────────────────────

@games_gate
async def ludo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 🔴 Top-level guard: ANY failure below (DB hiccup, dead GIF link,
    # WebApp domain not configured in @BotFather, network blip, etc.)
    # used to leak to the global error handler and show the user a cold
    # "Kuch gadbad ho gayi!" with no clue what broke. Now /ludo ALWAYS
    # replies with something sensible, and any deducted bet is refunded
    # so a mid-command failure never silently eats the user's coins.
    try:
        u    = update.effective_user
        chat = update.effective_chat

        if chat.type == "private":
            await update.message.reply_html(
                "🎲 <b>Ludo sirf group mein khel sakte hain!</b>\n"
                "Apne group mein add karo aur /ludo use karo."
            ); return

        await ensure_user(u.id, u.username or "", u.full_name)

        # Parse bet
        bet = 0
        if context.args:
            try:
                bet = max(0, int(context.args[0]))
            except ValueError:
                await update.message.reply_html("❌ Usage: /ludo [bet_amount]"); return

        if bet > 0:
            d = await get_user(u.id)
            if d["balance"] < bet:
                await update.message.reply_html(
                    f"❌ Tumhare paas enough coins nahi!\n"
                    f"💰 Balance: {fmt(d['balance'])} | Bet: {fmt(bet)}"
                ); return
            await deduct_balance(u.id, bet)

        game = _new_game(chat.id, u.id, u.first_name, bet)
        gid  = game["id"]
        _ludo_games[gid] = game

        # If anything while announcing the game blows up, refund the bet
        # so the user isn't charged for a game they couldn't even start.
        try:
            from config import WEBAPP_BASE_URL
            if WEBAPP_BASE_URL:
                # 🎮 Full visual Mini App experience is configured — offer it
                # as the primary way to play, alongside the classic buttons.
                from telegram import WebAppInfo
                webapp_url = f"{WEBAPP_BASE_URL}/ludo?game_id={gid}&chat_id={chat.id}&bet={bet}"
                spectate_url = f"{WEBAPP_BASE_URL}/ludo?game_id={gid}&chat_id={chat.id}&mode=spectate"
                caption = (
                    f"🎲 <b>{sc('Iota Ludo — Mini App')}</b>\n\n"
                    "Play with a real animated board, live dice, and in-lobby chat "
                    "— right inside Telegram!\n\n"
                    f"💰 Bet: {fmt(bet)} per player\n"
                    f"👤 Host: {mention(u)}\n\n"
                    "Tap <b>Play Ludo</b> to join, or <b>Watch</b> to spectate without playing."
                )
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎮 Play Ludo", web_app=WebAppInfo(url=webapp_url))],
                    [InlineKeyboardButton("👀 Watch", web_app=WebAppInfo(url=spectate_url))],
                    [InlineKeyboardButton("💬 Classic chat mode", callback_data=f"ludo_classic_{gid}")],
                ])
                try:
                    await update.message.reply_animation(
                        animation=LUDO_GIF, caption=caption, parse_mode="HTML", reply_markup=kb
                    )
                except Exception as e:
                    # WebApp buttons get rejected when the domain isn't
                    # configured in @BotFather. Re-sending the SAME kb would
                    # fail again — fall back to the classic chat lobby so the
                    # command still responds.
                    logger.debug(f"ludo_cmd WebApp send failed ({e}); classic lobby")
                    await _announce_classic(update, game, gid)
            else:
                # No Mini App URL configured — classic in-chat game.
                await _announce_classic(update, game, gid)
        except Exception as announce_err:
            logger.warning(f"ludo_cmd announce failed, refunding bet: {announce_err}")
            if bet > 0:
                try:
                    await add_balance(u.id, bet)
                except Exception as refund_err:
                    logger.warning(f"ludo_cmd bet refund failed: {refund_err}")
                _ludo_games.pop(gid, None)
            await update.message.reply_html(
                "🎲 Ludo shuru nahi ho paya 😣 thodi der baad try karo!"
            )
    except Exception as e:
        logger.warning(f"ludo_cmd failed: {e}")
        try:
            await update.effective_message.reply_html(
                "🎲 Ludo shuru nahi ho paya 😣 thodi der baad try karo!"
            )
        except Exception:
            pass


async def _announce_classic(update: Update, game: dict, gid: str):
    """Sends the lobby message as an animation (GIF) when possible, and
    cleanly falls back to a plain text message if the GIF URL is dead or
    Telegram rejects it — never raises, never double-sends a broken kb."""
    try:
        await update.message.reply_animation(
            animation=LUDO_GIF,
            caption=_lobby_text(game),
            parse_mode="HTML",
            reply_markup=_lobby_kb(gid),
        )
    except Exception:
        await update.message.reply_html(_lobby_text(game), reply_markup=_lobby_kb(gid))


# ─────────────────────────────────────────────────────────────
#  LUDO CALLBACKS
# ─────────────────────────────────────────────────────────────

async def _safe_edit(q, text: str, reply_markup=None):
    """
    Edit a callback's message safely regardless of whether it's a plain
    text message or carries media (the lobby announcement is sent via
    reply_animation with LUDO_GIF, so it has a caption, not text).

    🔴 FIXES: several call sites in this file used a bare
    q.edit_message_text(...), which raises "There is no text in the
    message to edit" whenever the message being edited is actually a
    photo/animation/etc. — exactly the case for the Ludo lobby message.
    This single helper picks the right method every time and falls back
    gracefully if the first attempt still fails for any other reason.
    """
    try:
        if q.message.photo or q.message.animation or q.message.video:
            await q.edit_message_caption(caption=text, parse_mode="HTML", reply_markup=reply_markup)
        else:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception:
        # Last-resort fallback: try the other method once, in case our
        # media detection above was wrong for some edge-case message type.
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
        except Exception:
            try:
                await q.edit_message_caption(caption=text, parse_mode="HTML", reply_markup=reply_markup)
            except Exception as e:
                logger.debug(f"_safe_edit: both edit methods failed: {e}")


async def ludo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    u    = q.from_user
    data = q.data
    await q.answer()

    # ── Switch to classic chat-button mode ──────────────────────
    if data.startswith("ludo_classic_"):
        gid  = data[len("ludo_classic_"):]
        game = _ludo_games.get(gid)
        if not game:
            await _safe_edit(q, "❌ Game expire ho gayi!"); return
        await _safe_edit(q, _lobby_text(game), reply_markup=_lobby_kb(gid))
        return

    # ── JOIN ──────────────────────────────────────────────────
    if data.startswith("ludo_join_"):
        gid  = data[len("ludo_join_"):]
        game = _ludo_games.get(gid)
        if not game:
            await _safe_edit(q, "❌ Game expire ho gayi!"); return
        if game["status"] != "waiting":
            await q.answer("Game already shuru ho gayi!", show_alert=True); return
        if any(p["id"] == u.id for p in game["players"]):
            await q.answer("Tum already join kar chuke ho!", show_alert=True); return
        if len(game["players"]) >= 4:
            await q.answer("Game full hai! (Max 4 players)", show_alert=True); return

        # Check & deduct bet
        if game["bet"] > 0:
            await ensure_user(u.id, u.username or "", u.full_name)
            d = await get_user(u.id)
            if d["balance"] < game["bet"]:
                await q.answer(f"❌ Tumhare paas {fmt(game['bet'])} coins nahi!", show_alert=True); return
            await deduct_balance(u.id, game["bet"])

        color = COLOR_LIST[len(game["players"])]
        game["players"].append({
            "id":     u.id,
            "name":   safe_html(u.first_name),  # see host_name comment above
            "color":  color,
            "pieces": [0, 0, 0, 0],
            "finished_pieces": 0,
            "score":  0,
        })
        _ludo_games[gid] = game
        await _safe_edit(q, _lobby_text(game), reply_markup=_lobby_kb(gid))
        return

    # ── CANCEL ────────────────────────────────────────────────
    if data.startswith("ludo_cancel_"):
        gid  = data[len("ludo_cancel_"):]
        game = _ludo_games.pop(gid, None)
        if not game: return
        host = game["players"][0]
        if host["id"] != u.id:
            await q.answer("Sirf host cancel kar sakta hai!", show_alert=True); return
        # Refund all players
        if game["bet"] > 0:
            for p in game["players"]:
                await add_balance(p["id"], game["bet"])
        await _safe_edit(q, "❌ Ludo game cancel kar di gayi!\n💰 Bets refund ho gaye.")
        return

    # ── START ──────────────────────────────────────────────────
    if data.startswith("ludo_start_"):
        gid  = data[len("ludo_start_"):]
        game = _ludo_games.get(gid)
        if not game: return
        if game["players"][0]["id"] != u.id:
            await q.answer("Sirf host start kar sakta hai!", show_alert=True); return
        if len(game["players"]) < 2:
            await q.answer("Kam se kam 2 players chahiye!", show_alert=True); return
        if game["status"] != "waiting":
            await q.answer("Game already shuru hai!", show_alert=True); return

        game["status"] = "playing"
        game["turn"]   = 0
        _ludo_games[gid] = game

        first = game["players"][0]
        txt = (
            f"🎲 <b>{sc('Iota Ludo Shuru')}</b>\n\n"
            f"{_render_scoreboard(game)}\n\n"
            f"🎯 Pehli baari: {COLORS[first['color']]['emoji']} <b>{first['name']}</b>\n"
            f"Dice roll karo!"
        )
        await _safe_edit(q, txt, reply_markup=_roll_kb(gid))

        # Start turn timer
        asyncio.create_task(_turn_timer(context, gid))
        return

    # ── ROLL DICE ─────────────────────────────────────────────
    if data.startswith("ludo_roll_"):
        gid  = data[len("ludo_roll_"):]
        game = _ludo_games.get(gid)
        if not game or game["status"] != "playing":
            await q.answer("Game active nahi hai!", show_alert=True); return

        current_p = game["players"][game["turn"]]
        if current_p["id"] != u.id:
            await q.answer(
                f"Abhi {current_p['name']} ki baari hai!", show_alert=True
            ); return

        dice  = random.randint(1, 6)
        game["dice"]      = dice
        game["last_roll"] = int(time.time())
        _ludo_games[gid]  = game

        # Which pieces can move?
        color    = current_p["color"]
        movable  = [
            i for i, pos in enumerate(current_p["pieces"])
            if _can_move(pos, dice, color)
        ]

        dice_emoji = ["", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣"][dice]

        if not movable:
            # No valid move — pass turn
            msg = (
                f"{COLORS[color]['emoji']} <b>{current_p['name']}</b> ne {dice_emoji} roll kiya!\n"
                f"❌ Koi piece move nahi kar sakti! Turn pass.\n\n"
                f"{_render_scoreboard(game)}"
            )
            _next_turn(game)
            _ludo_games[gid] = game
            next_p = game["players"][game["turn"]]
            await _safe_edit(
                q,
                msg + f"\n\n🎯 Ab: {COLORS[next_p['color']]['emoji']} <b>{next_p['name']}</b>",
                reply_markup=_roll_kb(gid)
            )
            asyncio.create_task(_turn_timer(context, gid))
            return

        if len(movable) == 1:
            # Auto-move the only movable piece
            await _do_move(q, context, gid, movable[0], dice_emoji)
            return

        # Show piece selection
        pieces_txt = ""
        for i, pos in enumerate(current_p["pieces"]):
            if i in movable:
                status = "Yard" if pos == 0 else (f"Ghar" if pos == WINNING_POS else f"Cell {pos}")
                pieces_txt += f"\n  {'①②③④'[i]} Piece {i+1}: {status} → +{dice}"

        txt = (
            f"{COLORS[color]['emoji']} <b>{current_p['name']}</b> ne {dice_emoji} roll kiya!\n"
            f"Kaunsi piece move karni hai?{pieces_txt}\n\n"
            f"{_render_scoreboard(game)}"
        )
        await _safe_edit(q, txt, reply_markup=_piece_kb(gid, movable))
        return

    # ── MOVE PIECE ────────────────────────────────────────────
    if data.startswith("ludo_move_"):
        parts     = data.split("_")
        piece_idx = int(parts[-1])
        gid       = "_".join(parts[2:-1])
        game      = _ludo_games.get(gid)
        if not game or game["status"] != "playing":
            await q.answer("Game active nahi!", show_alert=True); return

        current_p = game["players"][game["turn"]]
        if current_p["id"] != u.id:
            await q.answer("Tumhari baari nahi!", show_alert=True); return

        dice_emoji = ["", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣"][game["dice"]]
        await _do_move(q, context, gid, piece_idx, dice_emoji)
        return


async def _do_move(q, context, gid: str, piece_idx: int, dice_emoji: str):
    """Execute a piece move and update game state."""
    game     = _ludo_games.get(gid)
    if not game: return

    current_p = game["players"][game["turn"]]
    color     = current_p["color"]
    dice      = game["dice"]
    old_pos   = current_p["pieces"][piece_idx]
    new_pos   = _calc_new_pos(old_pos, dice, color)

    if new_pos > WINNING_POS:
        new_pos = old_pos  # no-op safety

    current_p["pieces"][piece_idx] = new_pos
    events = []

    # Check if leaving yard
    if old_pos == 0 and new_pos > 0:
        events.append(f"🚀 Piece {piece_idx+1} yard se nikal gayi!")

    # Check capture
    captured = _check_capture(game, color, new_pos)
    for name in captured:
        events.append(f"💥 {name} ki piece capture! Wापस yard.")
        current_p["score"] += 50  # bonus score

    # Check home
    if new_pos == WINNING_POS:
        current_p["score"] += 100
        home_count = current_p["pieces"].count(WINNING_POS)
        events.append(f"🏠 Piece {piece_idx+1} GHAR PAHUNCH GAYI! ({home_count}/4)")

    # Check win
    won = _has_won(current_p["pieces"])
    _ludo_games[gid] = game

    if won:
        await _end_game(q, context, gid, current_p)
        return

    # Extra turn on 6 or capture
    extra = (dice == 6 or bool(captured))
    if not extra:
        _next_turn(game)

    _ludo_games[gid] = game
    next_p = game["players"][game["turn"]]

    score_bar = _render_scoreboard(game)
    events_txt = "\n".join(events) if events else ""
    if extra and dice == 6:
        events_txt += f"\n🎲 6 aaya! {current_p['name']} ko ek aur baari mili!"
    elif extra and captured:
        events_txt += f"\n🎲 Capture bonus! Ek aur baari!"

    txt = (
        f"{COLORS[color]['emoji']} <b>{current_p['name']}</b> ne {dice_emoji} roll kiya!\n"
        f"{events_txt}\n\n"
        f"{score_bar}\n\n"
        f"🎯 Ab: {COLORS[next_p['color']]['emoji']} <b>{next_p['name']}</b>"
    )
    await _safe_edit(q, txt, reply_markup=_roll_kb(gid))

    asyncio.create_task(_turn_timer(context, gid))


def _next_turn(game: dict):
    game["turn"] = (game["turn"] + 1) % len(game["players"])


async def _end_game(q, context, gid: str, winner_p: dict):
    """Handle game end - pay out winner."""
    game = _ludo_games.pop(gid, None)
    if not game: return

    bet    = game["bet"]
    total  = bet * len(game["players"])
    prize  = int(total * 0.95)  # 5% fee

    # Pay winner
    if prize > 0:
        await add_balance(winner_p["id"], prize)

    # Build podium
    by_home = sorted(game["players"], key=lambda p: p["pieces"].count(WINNING_POS), reverse=True)
    medals  = ["🥇", "🥈", "🥉", "4️⃣"]
    podium  = "\n".join(
        f"{medals[i]} {COLORS[p['color']]['emoji']} {p['name']} — 🏠 {p['pieces'].count(WINNING_POS)}/4"
        for i, p in enumerate(by_home)
    )

    result = (
        f"🏆 <b>{sc('Iota Ludo — Game Over')}</b>\n\n"
        f"👑 <b>WINNER: {COLORS[winner_p['color']]['emoji']} {winner_p['name']}</b>\n"
        f"💰 Prize: <b>{fmt(prize)}</b> coins!\n\n"
        f"🏅 Final Standings:\n{podium}\n\n"
        f"🎮 Phir khelne ke liye: /ludo"
    )

    share_rows = [
        (f"{COLORS[p['color']]['emoji']} {p['name']}", f"{p['pieces'].count(WINNING_POS)}/4")
        for p in by_home
    ]
    try:
        await context.bot.send_animation(
            game["chat_id"],
            animation=WIN_GIF,
            caption=result,
            parse_mode="HTML"
        )
    except Exception:
        await context.bot.send_message(game["chat_id"], result, parse_mode="HTML")
    await _send_art(context, game["chat_id"],
                   lambda: _render_ludo_share(share_rows),
                   caption="🏆 ʟᴜᴅᴏ — ғɪɴᴀʟ ʙᴏᴀʀᴅ")

    await _safe_edit(q, f"🏆 {winner_p['name']} JEET GAYE! Details upar dekho.")


async def _turn_timer(context, gid: str):
    """Auto-skip turn after 60 seconds of inactivity."""
    await asyncio.sleep(60)
    game = _ludo_games.get(gid)
    if not game or game["status"] != "playing":
        return

    # Check if the roll hasn't happened recently
    if int(time.time()) - game.get("last_roll", 0) < 55:
        return  # Player acted

    current_p = game["players"][game["turn"]]
    _next_turn(game)
    _ludo_games[gid] = game
    next_p = game["players"][game["turn"]]

    try:
        await context.bot.send_message(
            game["chat_id"],
            f"⏱️ <b>{current_p['name']}</b> ne 60 seconds mein roll nahi kiya!\n"
            f"Turn skip — Ab: {COLORS[next_p['color']]['emoji']} <b>{next_p['name']}</b>\n"
            f"🎲 Roll karo!",
            parse_mode="HTML",
            reply_markup=_roll_kb(gid)
        )
    except Exception:
        pass

    asyncio.create_task(_turn_timer(context, gid))
