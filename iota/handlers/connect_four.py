"""
Iota Bot — Connect-4 🟡🔴 (net-new strategy game)

Two-player, played in a group via inline buttons. Board is 7 cols x 6 rows.
State is in-memory (like the other multiplayer games). Callback data stays
well under Telegram's 64-byte limit: cf_<gid8>_<col> / cf_<gid8>_join.
"""
import logging
import uuid
import asyncio
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from utils.mongo_db import ensure_user, get_user
from utils.helpers import mention, mention_id
from utils.fonts import sc
from utils.system_gate import games_gate
from utils.spectator import notify_watchers, add_watcher, remove_watcher

logger = logging.getLogger(__name__)

COLS, ROWS = 7, 6
_EMPTY, _P1, _P2 = 0, 1, 2
_CF_GAMES: dict = {}


def _new_board():
    return [[_EMPTY] * COLS for _ in range(ROWS)]


def _drop(board, col, player):
    for r in range(ROWS - 1, -1, -1):
        if board[r][col] == _EMPTY:
            board[r][col] = player
            return r
    return -1


def _win(board, player):
    for r in range(ROWS):
        for c in range(COLS):
            if board[r][c] != player:
                continue
            for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                cnt = 0
                rr, cc = r, c
                while 0 <= rr < ROWS and 0 <= cc < COLS and board[rr][cc] == player:
                    cnt += 1
                    rr += dr
                    cc += dc
                if cnt >= 4:
                    return True
    return False


def _render(board, turn_name):
    cell = {_EMPTY: "⚪", _P1: "🟡", _P2: "🔴"}
    lines = []
    for r in range(ROWS):
        lines.append(" ".join(cell[board[r][c]] for c in range(COLS)))
    lines.append("1️⃣2️⃣3️⃣4️⃣5️⃣6️⃣7️⃣")
    return "🔴🟡 Cᴏɴɴᴇᴄᴛ 4 🟡🔴\n" + "\n".join(lines) + f"\n\n🎯 {turn_name}'s turn"


def _board_kb(gid, game_over=False):
    if game_over:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 ɴᴇᴡ ɢᴀᴍᴇ", callback_data=f"cf_{gid}_new")
        ]])
    row = [InlineKeyboardButton(str(c + 1), callback_data=f"cf_{gid}_{c}")
           for c in range(COLS)]
    return InlineKeyboardMarkup([
        row,
        [InlineKeyboardButton("🔄 ʀᴇsᴛᴀʀᴛ", callback_data=f"cf_{gid}_new")],
    ])


def _active_game_for_chat(chat_id):
    for g in _CF_GAMES.values():
        if g["chat_id"] == chat_id and g["status"] in ("waiting", "playing"):
            return g
    return None


async def _notify(context, game):
    try:
        p = await get_user(game["turn"])
        await notify_watchers(context.bot, game,
                              _render(game["board"], mention_id(game["turn"], p.get("full_name", "P"))))
    except Exception as e:
        logger.debug(f"cf spectator notify failed: {e}")


@games_gate
async def connect4_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_html("🟡 Connect-4 sirf groups mein khelo!")
        return
    await ensure_user(u.id, u.username or "", u.full_name)
    gid = uuid.uuid4().hex[:8]
    _CF_GAMES[gid] = {
        "chat_id": chat.id, "board": _new_board(),
        "players": [u.id], "turn": u.id, "status": "waiting",
        "msg_id": None, "watchers": set(),
    }
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟡 Join!", callback_data=f"cf_{gid}_join"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"cf_{gid}_cancel"),
        ],
        [InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="gh_home")],
    ])
    msg = await update.message.reply_html(
        f"🟡 <b>Connect-4 Challenge!</b>\nHost: {mention(u)}\nWaiting for a rival…",
        reply_markup=kb
    )
    _CF_GAMES[gid]["msg_id"] = msg.message_id


async def connect4_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    if not data.startswith("cf_"):
        return
    rest = data[3:]
    gid, _, act = rest.partition("_")
    game = _CF_GAMES.get(gid)
    if not game:
        await q.edit_message_text("❌ Game expire ho gaya.")
        return
    u = q.from_user

    if act == "cancel":
        _CF_GAMES.pop(gid, None)
        await q.edit_message_text("❌ Connect-4 cancel kar diya.")
        return

    if act == "join":
        if game["status"] != "waiting":
            await q.answer("Game already started!", show_alert=True)
            return
        if u.id in game["players"]:
            await q.answer("Tu already hai!", show_alert=True)
            return
        game["players"].append(u.id)
        game["status"] = "playing"
        p1 = await get_user(game["players"][0])
        p2 = await get_user(u.id)
        await q.edit_message_text(
            _render(game["board"], mention_id(game["turn"], p1.get("full_name", "P1"))),
            reply_markup=_board_kb(gid)
        )
        await _notify(context, game)
        return

    if act == "new":
        _CF_GAMES.pop(gid, None)
        await q.edit_message_text("🔄 Naya game /connect4 se shuru karo!")
        return

    if act.isdigit():
        if game["status"] != "playing":
            await q.answer("Game shuru nahi hua!", show_alert=True)
            return
        if u.id != game["turn"]:
            await q.answer("Teri turn nahi!", show_alert=True)
            return
        col = int(act)
        player = _P1 if u.id == game["players"][0] else _P2
        if _drop(game["board"], col, player) == -1:
            await q.answer("Column full hai!", show_alert=True)
            return
        other = game["players"][1] if u.id == game["players"][0] else game["players"][0]
        if _win(game["board"], player):
            _CF_GAMES.pop(gid, None)
            await q.edit_message_text(
                _render(game["board"], mention(u)) + f"\n\n🏆 {mention(u)} JEET GAYA! 🎉",
                reply_markup=_board_kb(gid, game_over=True)
            )
            await _notify(context, game)
            return
        # draw?
        if all(game["board"][0][c] != _EMPTY for c in range(COLS)):
            _CF_GAMES.pop(gid, None)
            await q.edit_message_text(
                _render(game["board"], mention(u)) + "\n\n🤝 Draw!",
                reply_markup=_board_kb(gid, game_over=True)
            )
            await _notify(context, game)
            return
        game["turn"] = other
        ot = await get_user(other)
        await q.edit_message_text(
            _render(game["board"], mention_id(other, ot.get("full_name", "P2"))),
            reply_markup=_board_kb(gid)
        )
        await _notify(context, game)
