"""
Iota Bot — UNO 🎴 (net-new card game)

2–4 players, played in a group via inline buttons. Standard simplified
rules: match colour/number/action, draw when stuck, +2/+4 wilds, reverse,
skip. State is in-memory. Callback data: uno_<gid8>_<action>.
"""
import logging
import uuid
import random
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from utils.mongo_db import ensure_user, get_user
from utils.helpers import mention, mention_id
from utils.system_gate import games_gate
from utils.spectator import notify_watchers

logger = logging.getLogger(__name__)

COLORS = ["R", "G", "B", "Y"]
COLOR_EMOJI = {"R": "🔴", "G": "🟢", "B": "🔵", "Y": "🟡"}
_WILD = "⚫"
_UNO: dict = {}
_DIRS = [1, -1]


def _build_deck():
    deck = []
    for c in COLORS:
        deck.append((c, "0"))
        for _ in range(2):
            for n in "123456789":
                deck.append((c, n))
            deck.append((c, "skip"))
            deck.append((c, "rev"))
            deck.append((c, "+2"))
    for _ in range(4):
        deck.append(("W", "wild"))
        deck.append(("W", "+4"))
    random.shuffle(deck)
    return deck


def _fmt_card(card):
    col, val = card
    if col == "W":
        return f"⚫{val}"
    return f"{COLOR_EMOJI[col]}{val}"


def _top(game):
    return game["pile"][-1]


def _playable(card, top):
    col, val = card
    tcol, tval = top
    if col == "W":
        return True
    if col == tcol or val == tval:
        return True
    return False


def _draw(game, n=1):
    drawn = []
    for _ in range(n):
        if not game["deck"]:
            game["deck"] = _build_deck()
        drawn.append(game["deck"].pop())
    return drawn


def _next_turn(game):
    order = game["order"]
    idx = order.index(game["turn"])
    idx = (idx + game["dir"]) % len(order)
    game["turn"] = order[idx]


def _board_kb(game):
    top = _top(game)
    hand = game["hands"][game["turn"]]
    row = []
    for i, card in enumerate(hand[:8]):
        if _playable(card, top):
            row.append(InlineKeyboardButton(_fmt_card(card),
                                             callback_data=f"uno_{game['gid']}_p{i}"))
    kb = []
    if row:
        kb.append(row)
    kb.append([
        InlineKeyboardButton("🎴 Draw", callback_data=f"uno_{game['gid']}_draw"),
        InlineKeyboardButton("🔄 ʀᴇsᴛᴀʀᴛ", callback_data=f"uno_{game['gid']}_new"),
    ])
    return InlineKeyboardMarkup(kb)


def _render(game):
    top = _top(game)
    lines = [f"🎴 <b>UNO</b> — {COLOR_EMOJI.get(top[0], _WILD)} Top: {_fmt_card(top)}"]
    for uid in game["order"]:
        u = game["names"][uid]
        mark = "▶️" if uid == game["turn"] else "  "
        lines.append(f"{mark} {u}: {len(game['hands'][uid])} cards")
    return "\n".join(lines)


@games_gate
async def uno_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_html("🎴 UNO sirf groups mein khelo!")
        return
    await ensure_user(u.id, u.username or "", u.full_name)
    gid = uuid.uuid4().hex[:8]
    _UNO[gid] = {
        "gid": gid, "chat_id": chat.id, "deck": _build_deck(),
        "pile": [], "players": [u.id], "order": [u.id],
        "names": {}, "hands": {}, "turn": u.id, "dir": 1,
        "status": "waiting", "pending_wild": None, "watchers": set(),
    }
    _UNO[gid]["names"][u.id] = (await get_user(u.id)).get("full_name", "P1")
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎴 Join!", callback_data=f"uno_{gid}_join"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"uno_{gid}_cancel"),
        ],
        [InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data="gh_home")],
    ])
    msg = await update.message.reply_html(
        f"🎴 <b>UNO Challenge!</b>\nHost: {mention(u)}\nWaiting for players…",
        reply_markup=kb
    )
    _UNO[gid]["msg_id"] = msg.message_id


async def uno_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    if not data.startswith("uno_"):
        return
    gid = data[4:12]
    act = data[13:]
    game = _UNO.get(gid)
    if not game:
        await q.edit_message_text("❌ UNO game expire ho gaya.")
        return
    u = q.from_user

    if act == "cancel":
        _UNO.pop(gid, None)
        await q.edit_message_text("❌ UNO cancel.")
        return
    if act == "new":
        _UNO.pop(gid, None)
        await q.edit_message_text("🔄 Naya UNO /uno se shuru karo!")
        return
    if act == "join":
        if game["status"] != "waiting":
            await q.answer("Shuru ho chuka!", show_alert=True)
            return
        if u.id in game["players"]:
            await q.answer("Tu already hai!", show_alert=True)
            return
        if len(game["players"]) >= 4:
            await q.answer("Full (4 players)!", show_alert=True)
            return
        game["players"].append(u.id)
        game["names"][u.id] = (await get_user(u.id)).get("full_name", f"P{len(game['players'])}")
        if len(game["players"]) >= 2:
            await _start_uno(q, game)
        else:
            await q.edit_message_text(
                f"🎴 UNO — {len(game['players'])} players joined. Aur ek join karo!",
                reply_markup=q.message.reply_markup
            )
        return
    if act.startswith("color_"):
        color = act[6:]
        if game.get("pending_wild") == u.id:
            game["pile"][-1] = (color, game["pile"][-1][1])
            game["pending_wild"] = None
            _after_play(q, game, skip=True)
        return
    if game["status"] != "playing" or u.id != game["turn"]:
        await q.answer("Teri turn nahi!", show_alert=True)
        return
    if act == "draw":
        card = _draw(game, 1)[0]
        game["hands"][u.id].append(card)
        _next_turn(game)
        await _refresh(q, game, extra=f"{mention(u)} ne draw kiya 🎴")
        return
    if act.startswith("p"):
        idx = int(act[1:])
        hand = game["hands"][u.id]
        if idx >= len(hand):
            return
        card = hand[idx]
        if not _playable(card, _top(game)):
            await q.answer("Nahi chal sakta!", show_alert=True)
            return
        hand.pop(idx)
        game["pile"].append(card)
        if not hand:
            _UNO.pop(gid, None)
            await q.edit_message_text(
                _render(game) + f"\n\n🏆 {mention(u)} UNO JEET GAYA! 🎉",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 ɴᴇᴡ", callback_data=f"uno_{gid}_new")]])
            )
            return
        col, val = card
        if col == "W":
            game["pending_wild"] = u.id
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(COLOR_EMOJI[c], callback_data=f"uno_{gid}_color_{c}")
                for c in COLORS
            ]])
            await q.edit_message_text(
                _render(game) + f"\n\n🌈 {mention(u)} wild choose karo:",
                reply_markup=kb
            )
            return
        _after_play(q, game, skip=(val == "skip"), rev=(val == "rev"),
                   draw=(int(val[1:]) if val.startswith("+") else 0))


async def _start_uno(q, game):
    game["status"] = "playing"
    game["order"] = list(game["players"])
    for pid in game["players"]:
        game["hands"][pid] = _draw(game, 7)
    first = _draw(game, 1)[0]
    if first[0] == "W":
        first = ("R", "0")
    game["pile"] = [first]
    game["turn"] = game["order"][0]
    await _refresh(q, game, extra="🎴 Game shuru!")


def _after_play(q, game, skip=False, rev=False, draw=0):
    if rev and len(game["order"]) > 2:
        game["dir"] *= -1
    if draw:
        nxt = game["order"][(game["order"].index(game["turn"]) + game["dir"]) % len(game["order"])]
        _draw(game, draw)
        game["turn"] = nxt
        skip = True
    _next_turn(game)
    import asyncio as _a
    _a.create_task(_refresh_async(q, game))


async def _refresh(q, game, extra=""):
    await _refresh_async(q, game, extra)


def _active_game_for_chat(chat_id):
    for g in _UNO.values():
        if g["chat_id"] == chat_id and g["status"] in ("waiting", "playing"):
            return g
    return None


async def _refresh_async(q, game, extra=""):
    try:
        text = _render(game)
        if extra:
            text += f"\n\n{extra}"
        await q.edit_message_text(text, reply_markup=_board_kb(game))
        try:
            await notify_watchers(q.bot, game, text)
        except Exception as e:
            logger.debug(f"uno spectator notify failed: {e}")
    except Exception as e:
        logger.debug(f"uno refresh failed: {e}")
