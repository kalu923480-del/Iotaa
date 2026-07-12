"""
Iota Bot — Tournament Bracket 🏆 (single-elimination)

A generic bracket tournament for any 1v1 mini-game. The pure bracket logic
lives in utils/tournament.py (unit-tested there); this handler stores state
in memory (like the other multiplayer games) and drives it via inline
buttons. Winners are reported round-by-round until a champion is crowned.

Commands:
  /tournament start            — open a bracket in this chat (host = you)
  /tournament join             — join the open bracket (or use the button)
  /tournament begin            — host starts the bracket once enough joined
  /tournament cancel           — host cancels the bracket
  /tournament status           — show current bracket

Callback data stays < 64 bytes: tour_<tid8>_<action>.
"""
import logging
import uuid
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from utils.tournament import build_bracket, current_matches, report_winner, champion
from utils.mongo_db import ensure_user, get_user
from utils.helpers import mention, mention_id
from utils.fonts import sc
from utils.system_gate import games_gate

logger = logging.getLogger(__name__)

_TOURNAMENTS: dict = {}


def _names(tour):
    return tour["names"]


def _name(tour, uid):
    if uid is None:
        return "🅱️ (bye)"
    return tour["names"].get(uid, f"User {uid}")


def _current_round_index(rounds):
    for i, r in enumerate(rounds):
        if any(m["winner"] is None for m in r):
            return i
    return len(rounds) - 1


def _render_bracket(tour):
    rounds = tour["rounds"]
    lines = [f"🏆 <b>{sc('Iota Tournament')}</b> — {len(tour['participants'])} players", "━" * 20]
    for i, r in enumerate(rounds):
        label = "Final" if len(r) == 1 else f"Round {i + 1}"
        lines.append(f"\n🎯 <b>{label}</b>")
        for j, m in enumerate(r):
            p1 = _name(tour, m["p1"])
            p2 = _name(tour, m["p2"])
            if m["winner"] is None:
                lines.append(f"  {j + 1}. {p1}  vs  {p2}")
            else:
                w = _name(tour, m["winner"])
                lines.append(f"  {j + 1}. {p1}  vs  {p2}  →  🏅 {w}")
    return "\n".join(lines)


def _lobby_kb(tid):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("➕ Join", callback_data=f"tour_{tid}_join"),
        InlineKeyboardButton("🚀 Begin", callback_data=f"tour_{tid}_begin"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"tour_{tid}_cancel"),
    ]])


def _match_kb(tid, ri, matches):
    """Winner-report buttons for round `ri`."""
    kb = []
    for j, m in enumerate(matches):
        if m["winner"] is not None:
            continue
        if m["p1"] is None or m["p2"] is None:
            continue
        b1 = InlineKeyboardButton(
            f"🏅 {_name(_by_tid(tid), m['p1'])}",
            callback_data=f"tour_{tid}_rep_{ri}_{j}_0")
        b2 = InlineKeyboardButton(
            f"🏅 {_name(_by_tid(tid), m['p2'])}",
            callback_data=f"tour_{tid}_rep_{ri}_{j}_1")
        kb.append([b1, b2])
    return InlineKeyboardMarkup(kb)


def _by_tid(tid):
    return _TOURNAMENTS[tid]


def _auto_resolve_byes(tour):
    """A bye (None opponent) means the present player auto-advances."""
    rounds = tour["rounds"]
    for ri, r in enumerate(rounds):
        for j, m in enumerate(r):
            if m["winner"] is None and (m["p1"] is None or m["p2"] is None):
                winner = m["p1"] if m["p1"] is not None else m["p2"]
                report_winner(rounds, j, winner)


@games_gate
async def tournament_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    chat = update.effective_chat
    sub = (context.args[0] if context.args else "start").lower()

    if sub == "join":
        await _join(update, context, chat.id, u)
        return
    if sub == "cancel":
        await _cancel(update, context, chat.id, u)
        return
    if sub == "begin":
        await _begin(update, context, chat.id, u)
        return
    if sub == "status":
        await _status(update, context, chat.id)
        return

    # default: start a fresh bracket
    if chat.type == "private":
        await update.message.reply_html("🏆 Tournament sirf groups mein shuru karo!")
        return
    await ensure_user(u.id, u.username or "", u.full_name)
    tid = uuid.uuid4().hex[:8]
    _TOURNAMENTS[tid] = {
        "tid": tid, "chat_id": chat.id, "host_id": u.id,
        "status": "joining", "participants": [u.id],
        "names": {u.id: (await get_user(u.id)).get("full_name", "Host")},
        "rounds": None, "msg_id": None,
    }
    msg = await update.message.reply_html(
        f"🏆 <b>Iota Tournament</b> open!\nHost: {mention(u)}\n"
        f"Players: 1/{len(_TOURNAMENTS[tid]['participants'])}\n\n"
        f"➕ Join karo, phir host 🚀 Begin dabein (min 2 players).",
        reply_markup=_lobby_kb(tid)
    )
    _TOURNAMENTS[tid]["msg_id"] = msg.message_id


async def _join(update, context, chat_id, u):
    tour = _find_open(chat_id)
    if not tour:
        await update.message.reply_html("❌ Is chat mein koi open tournament nahi.")
        return
    if u.id in tour["participants"]:
        await update.message.reply_html("✅ Tu already joined hai!")
        return
    await ensure_user(u.id, u.username or "", u.full_name)
    tour["participants"].append(u.id)
    tour["names"][u.id] = (await get_user(u.id)).get("full_name", f"P{len(tour['participants'])}")
    await _refresh_lobby(context, tour, extra=f"➕ {mention(u)} joined! "
                          f"({len(tour['participants'])} total)")


async def _begin(update, context, chat_id, u):
    tour = _find_open(chat_id)
    if not tour:
        await update.message.reply_html("❌ Is chat mein koi open tournament nahi.")
        return
    if u.id != tour["host_id"]:
        await update.message.reply_html("❌ Sirf host bracket begin kar sakta hai.")
        return
    if len(tour["participants"]) < 2:
        await update.message.reply_html("❌ Kam se kam 2 players chahiye!")
        return
    tour["rounds"] = build_bracket(tour["participants"])
    _auto_resolve_byes(tour)
    tour["status"] = "bracket"
    await _show_round(context, tour)


async def _cancel(update, context, chat_id, u):
    tour = _find_open(chat_id)
    if not tour:
        await update.message.reply_html("❌ Is chat mein koi open tournament nahi.")
        return
    if u.id != tour["host_id"]:
        await update.message.reply_html("❌ Sirf host cancel kar sakta hai.")
        return
    _TOURNAMENTS.pop(tour["tid"], None)
    await update.message.reply_html("❌ Tournament cancel kar diya.")


async def _status(update, context, chat_id):
    tour = _find_open(chat_id) or _find_bracket(chat_id)
    if not tour:
        await update.message.reply_html("❌ Is chat mein koi tournament nahi.")
        return
    text = _render_bracket(tour)
    if tour["status"] == "joining":
        await update.message.reply_html(text, reply_markup=_lobby_kb(tour["tid"]))
    else:
        await update.message.reply_html(text)


def _find_open(chat_id):
    for t in _TOURNAMENTS.values():
        if t["chat_id"] == chat_id and t["status"] == "joining":
            return t
    return None


def _find_bracket(chat_id):
    for t in _TOURNAMENTS.values():
        if t["chat_id"] == chat_id and t["status"] == "bracket":
            return t
    return None


async def _refresh_lobby(context, tour, extra=""):
    text = (f"🏆 <b>Iota Tournament</b> open!\nHost: "
            f"{mention_id(tour['host_id'], tour['names'].get(tour['host_id'], 'Host'))}\n"
            f"Players: {len(tour['participants'])}\n")
    if extra:
        text += f"\n{extra}\n"
    text += "\n➕ Join karo, phir host 🚀 Begin dabein (min 2 players)."
    try:
        await context.bot.edit_message_text(
            chat_id=tour["chat_id"], message_id=tour["msg_id"],
            text=text, reply_markup=_lobby_kb(tour["tid"])
        )
    except Exception as e:
        logger.debug(f"tournament lobby refresh failed: {e}")


async def _show_round(context, tour):
    ri = _current_round_index(tour["rounds"])
    matches = tour["rounds"][ri]
    label = "Final" if len(matches) == 1 else f"Round {ri + 1}"
    text = (_render_bracket(tour)
            + f"\n\n🎯 <b>{label}</b> — winner report karo:")
    try:
        await context.bot.edit_message_text(
            chat_id=tour["chat_id"], message_id=tour["msg_id"],
            text=text, reply_markup=_match_kb(tour["tid"], ri, matches)
        )
    except Exception as e:
        logger.debug(f"tournament round show failed: {e}")


async def tournament_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    if not data.startswith("tour_"):
        return
    parts = data.split("_")
    # tour_<tid>_<action>  or  tour_<tid>_rep_<ri>_<j>_<slot>
    tid = parts[1]
    tour = _TOURNAMENTS.get(tid)
    if not tour:
        await q.edit_message_text("❌ Tournament expire ho gaya.")
        return
    u = q.from_user
    action = parts[2]

    if action == "join":
        await _join(q, context, tour["chat_id"], u)
        return
    if action == "cancel":
        await _cancel(q, context, tour["chat_id"], u)
        return
    if action == "begin":
        await _begin(q, context, tour["chat_id"], u)
        return
    if action == "rep":
        ri, j, slot = int(parts[3]), int(parts[4]), int(parts[5])
        m = tour["rounds"][ri][j]
        winner = m["p1"] if slot == 0 else m["p2"]
        if m["winner"] is not None:
            await q.answer("Pehle hi report ho chuka!", show_alert=True)
            return
        ok = report_winner(tour["rounds"], j, winner)
        if not ok:
            await q.answer("Invalid report!", show_alert=True)
            return
        champ = champion(tour["rounds"])
        if champ is not None:
            tour["status"] = "done"
            text = (_render_bracket(tour)
                    + f"\n\n👑 <b>CHAMPION: {_name(tour, champ)}</b> 🎉")
            try:
                await q.edit_message_text(text)
            except Exception:
                pass
            _TOURNAMENTS.pop(tid, None)
            return
        await _show_round(context, tour)
        return
