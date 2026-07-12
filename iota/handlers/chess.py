"""
Iota Bot — Chess ♟️ (net-new strategy game, uses utils/chess_engine)

Two players in a group. Host is White, challenger is Black. Moves are
made by REPLYING in the group with standard algebraic notation
(e.g. "e2e4", "e7e8q" for promotion). State is in-memory.
"""
import logging
import uuid
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, filters

from utils.mongo_db import ensure_user, get_user
from utils.helpers import mention, mention_id
from utils.system_gate import games_gate
from utils.chess_engine import (
    initial_board, parse_move, parse_square, sq_name, color_of,
    legal_moves, apply_move, in_check, game_status, render_board,
    WHITE, BLACK,
)

logger = logging.getLogger(__name__)
_CHESS: dict = {}


def _new_game(chat_id, host_id, host_name):
    return {
        "gid": uuid.uuid4().hex[:8], "chat_id": chat_id,
        "board": initial_board(), "turn": WHITE, "castling": "KQkq",
        "ep": None, "players": {WHITE: host_id, BLACK: None},
        "names": {WHITE: host_name}, "status": "waiting", "msg_id": None,
    }


def _rights_after(board, frm, to, rights):
    piece = board[frm]
    if piece is None:
        return rights
    r = list(rights)
    if piece in ("K", "k"):
        if piece == "K":
            r = [c for c in r if c not in ("K", "Q")]
        else:
            r = [c for c in r if c not in ("k", "q")]
    # rook moves from home corner
    corners = {0: "Q", 7: "K", 56: "q", 63: "k"}
    if frm in corners:
        r = [c for c in r if c != corners[frm]]
    # rook captured on a corner
    if to in corners:
        r = [c for c in r if c != corners[to]]
    return "".join(r) if r else "-"


@games_gate
async def chess_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_html("♟️ Chess sirf groups mein khelo!")
        return
    await ensure_user(u.id, u.username or "", u.full_name)
    name = (await get_user(u.id)).get("full_name", "White")
    g = _new_game(chat.id, u.id, name)
    _CHESS[g["gid"]] = g
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("♟️ Join (Black)", callback_data=f"ch_{g['gid']}_join"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"ch_{g['gid']}_cancel"),
    ]])
    msg = await update.message.reply_html(
        f"♟️ <b>Chess Challenge!</b>\nWhite: {mention(u)}\nWaiting for Black…",
        reply_markup=kb
    )
    g["msg_id"] = msg.message_id


async def chess_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    if not data.startswith("ch_"):
        return
    gid, _, act = data[3:].partition("_")
    g = _CHESS.get(gid)
    if not g:
        await q.edit_message_text("❌ Chess game expire ho gaya.")
        return
    u = q.from_user
    if act == "cancel":
        _CHESS.pop(gid, None)
        await q.edit_message_text("❌ Chess cancel.")
        return
    if act == "join":
        if g["status"] != "waiting":
            await q.answer("Shuru ho chuka!", show_alert=True)
            return
        if u.id == g["players"][WHITE]:
            await q.answer("Tu already White hai!", show_alert=True)
            return
        g["players"][BLACK] = u.id
        g["names"][BLACK] = (await get_user(u.id)).get("full_name", "Black")
        g["status"] = "playing"
        await q.edit_message_text(
            render_board(g["board"]) + f"\n\n⚪ {mention(u)} is Black. White ki turn — "
            f"reply with a move (e.g. <code>e2e4</code>).",
        )
        return


async def chess_move_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    u = update.effective_user
    if chat.type == "private" or not update.message or not update.message.text:
        return
    g = next((x for x in _CHESS.values()
              if x["chat_id"] == chat.id and x["status"] == "playing"), None)
    if not g:
        return
    if u.id != g["players"][g["turn"]]:
        return
    mv = parse_move(update.message.text)
    if not mv:
        return
    frm, to, promo = mv
    legal = legal_moves(g["board"], g["turn"], g["castling"], g["ep"])
    match = None
    for m in legal:
        mf, mt, mp = m[0], m[1], m[2] if len(m) > 2 else None
        if mf == frm and mt == to and (mp == promo or (mp is None and promo is None)):
            match = m
            break
    if match is None:
        await update.message.reply_html("♟️ Invalid move! Try again.")
        return
    # apply (with castling flag if present)
    castle = match[3] if len(match) > 3 else None
    new_board = apply_move(g["board"], frm, to, promo, g["ep"], castle)
    g["castling"] = _rights_after(g["board"], frm, to, g["castling"])
    g["ep"] = to + 8 if g["board"][frm] in ("P", "p") and abs((to // 8) - (frm // 8)) == 2 else None
    g["board"] = new_board
    g["turn"] = BLACK if g["turn"] == WHITE else WHITE

    chk, mate, stale = game_status(new_board, g["turn"], g["castling"], g["ep"])
    flip = (g["players"][WHITE] != u.id)
    text = render_board(new_board, flip=flip)
    if mate:
        winner = g["players"][WHITE] if g["turn"] == BLACK else g["players"][BLACK]
        _CHESS.pop(g["gid"], None)
        await update.message.reply_html(
            f"{text}\n\n🏆 <b>Checkmate!</b> {mention_id(winner, g['names'][WHITE if g['turn']==BLACK else BLACK])} JEET GAYA! ♟️")
        return
    if stale:
        _CHESS.pop(g["gid"], None)
        await update.message.reply_html(f"{text}\n\n🤝 <b>Stalemate!</b> Draw.")
        return
    note = "⚠️ Check!" if chk else ""
    turn_name = g["names"][g["turn"]]
    await update.message.reply_html(
        f"{text}\n\n{'⚫' if g['turn']==BLACK else '⚪'} {mention_id(g['players'][g['turn']], turn_name)} ki turn{note} — reply move (e.g. <code>e2e4</code>)."
    )
