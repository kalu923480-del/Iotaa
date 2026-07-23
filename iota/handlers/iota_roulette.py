"""
Iota Bot — Iota Roulette 🎰 (Iota mini-game series)

A bid-elimination tournament, played in a group chat:

  /roulette <amount> [coins|gems]   start a game (host pays the stake)
  /rjoin <amount>                  join during the 2-minute window
  /bid <amount>                    DM Iota a bid during a bidding round

NOTE: every user-facing string below is wrapped with sc_all() (Iota-style
smallcaps) so the game's output matches the rest of the bot. (The global
output wrapper in bot.py also applies smallcaps, so this is idempotent and
just makes the styling explicit + robust.)

HOW IT WORKS
─────────────
1. Host runs /roulette <stake>. A 2-minute join window opens.
2. Friends run /rjoin <stake> (same amount) to enter. Everyone's
   stake goes into a single POT.
3. When the window closes, (players-1) bidding rounds run. Each round
   every surviving player DMs Iota a /bid. The player with the LOWEST
   bid that round is eliminated. (If you don't bid, you're auto-counted
   as 0 → automatically the lowest → eliminated.)
4. The last player standing wins the ENTIRE pot.

BIDS ARE PURE NUMBERS — they do NOT move any money. Only the entry
stake is ever moved (into the pot on join, to the winner at the end).
This keeps the economy 100% safe: a player can never "overspend" on a
bid, and the pot is always exactly the sum of everyone's stakes.

STATE is held in-memory (one active game per chat), exactly like the
other multiplayer games (bluff, ludo, werewolf) in this bot. If the
bot restarts mid-game the in-progress game is lost — acceptable, same as
those games, and the stakes are refunded on cancellation (too few players).
"""
import logging
from telegram import Update
from telegram.ext import ContextTypes

from utils.mongo_db import (
    ensure_user, get_user, add_balance, deduct_balance,
    add_gems, deduct_gems,
)
from utils.helpers import mention, mention_id, fmt
from utils.system_gate import games_gate
from utils.fonts import sc_all
from utils.game_ui import send_gif_result
from utils.game_art import send_game_art as _send_art, render_roulette as _render_roulette
from utils.game_rules import GAME_MIN_BET, GAME_MAX_BET, validate_bet

logger = logging.getLogger(__name__)

MIN_STAKE = GAME_MIN_BET
MAX_STAKE = GAME_MAX_BET
JOIN_SECONDS = 120      # 2-minute joining window (per spec)
BID_SECONDS = 60        # per-round bidding window
BETWEEN_ROUNDS = 4       # seconds between rounds so players can breathe

# chat_id -> game dict  (one active game per chat)
_GAMES = {}


def _game_by_player(uid: int) -> dict | None:
    """Find the game a user is currently part of (still running)."""
    for g in _GAMES.values():
        if uid in g.get("players", []) and g["phase"] != "done":
            return g
    return None


async def _names(ids) -> str:
    """Build a comma-separated mention string for a list of user ids."""
    parts = []
    for pid in ids:
        u = await get_user(pid)
        parts.append(mention_id(pid, u.get("full_name") or "User"))
    return ", ".join(parts) if parts else "—"


async def _pay(uid: int, amount: int, currency: str):
    if currency == "gems":
        await add_gems(uid, amount)
    else:
        await add_balance(uid, amount)


async def _refund(uid: int, amount: int, currency: str):
    await _pay(uid, amount, currency)


# ── /roulette <amount> [coins|gems] ────────────────────────────────────
@games_gate
async def roulette_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    msg = update.effective_message
    cid = update.effective_chat.id

    if update.effective_chat.type == "private":
        await msg.reply_html(sc_all("❌ <b>/roulette group mein chalao!</b> 🎰"))
        return

    if cid in _GAMES and _GAMES[cid]["phase"] != "done":
        await msg.reply_html(sc_all("❌ Is group mein pehle se ek Iota Roulette chal raha hai! ⏳"))
        return

    if not context.args:
        await msg.reply_html(sc_all(
            f"🎰 <b>Iota Roulette</b> 💙\n\n"
            f"Usage: <code>/roulette &lt;amount&gt; [coins|gems]</code>\n"
            f"Stake range: {fmt(MIN_STAKE)} – {fmt(MAX_STAKE)}\n\n"
            f"⏳ 2-minute join window shuru hoga. Doston ko bolo:\n"
            f"<code>/rjoin &lt;amount&gt;</code> (group mein)\n\n"
            f"Har round sabse kam bidder eliminate hoga. Last banda jeetega pot! 🏆"
        ))
        return

    currency = "coins"
    amount_txt = context.args[0]
    if len(context.args) > 1 and context.args[1].lower() in ("gems", "gem", "💎"):
        currency = "gems"

    try:
        stake = int(amount_txt)
    except ValueError:
        await msg.reply_html(sc_all("❌ Amount ek number hona chahiye!"))
        return
    ok, err = validate_bet(stake)
    if not ok:
        await msg.reply_html(
            f"{err}\nStake range: {fmt(MIN_STAKE)} – {fmt(MAX_STAKE)}"
        )
        return

    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)
    if currency == "gems":
        if d.get("gems", 0) < stake:
            await msg.reply_html(sc_all(f"❌ Enough gems nahi! 💎 Balance: {fmt(d.get('gems', 0))}"))
            return
        await deduct_gems(u.id, stake)
    else:
        if d.get("balance", 0) < stake:
            await msg.reply_html(sc_all(f"❌ Enough coins nahi! 💰 Balance: {fmt(d.get('balance', 0))}"))
            return
        await deduct_balance(u.id, stake)

    _GAMES[cid] = {
        "chat_id": cid,
        "host": u.id,
        "stake": stake,
        "currency": currency,
        "players": [u.id],
        "joined": {u.id: True},
        "pot": stake,
        "phase": "joining",
        "alive": [],
        "round": 0,
        "bids": {},
        "bidded": {},
        "min_bid": 0,
        "join_job": None,
        "bid_job": None,
    }
    g = _GAMES[cid]
    if getattr(context, "job_queue", None):
        g["join_job"] = context.job_queue.run_once(
            _close_joining, JOIN_SECONDS, data={"chat_id": cid}
        )
    else:
        await msg.reply_html(sc_all("⚠️ Timer system unavailable — game can't auto-start. Try later."))
        _GAMES.pop(cid, None)
        return

    await msg.reply_html(sc_all(
        f"🎰 <b>Iota Roulette shuru!</b> 💙\n\n"
        f"👑 Host: {mention(u)}\n"
        f"💰 Stake: {fmt(stake)} {currency} | Pot: {fmt(stake)} {currency}\n\n"
        f"⏳ <b>2-minute join window</b> — doston ko bolo:\n"
        f"<code>/rjoin {stake}</code> (group mein)\n\n"
        f"Round mein sabse kam bidder har round eliminate hoga. Last banda jeetega pot! 🏆"
    ))


# ── /rjoin <amount> ───────────────────────────────────────────────────────
@games_gate
async def rjoin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    msg = update.effective_message
    cid = update.effective_chat.id

    if update.effective_chat.type == "private":
        await msg.reply_html(sc_all("❌ <b>/rjoin group mein chalao!</b> 🎰"))
        return

    g = _GAMES.get(cid)
    if not g or g["phase"] != "joining":
        await msg.reply_html(sc_all("❌ Abhi koi joining window nahi khula. Pehle /roulette chalao!"))
        return
    if u.id in g["joined"]:
        await msg.reply_html(sc_all("❌ Tu pehle hi join kar chuka hai!"))
        return
    if u.id == g["host"]:
        await msg.reply_html(sc_all("❌ Host already andar hai, dobara join mat kar!"))
        return
    if not context.args:
        await msg.reply_html(sc_all(f"❌ Usage: <code>/rjoin {g['stake']}</code>"))
        return
    try:
        amt = int(context.args[0])
    except ValueError:
        await msg.reply_html(sc_all("❌ Amount ek number hona chahiye!"))
        return
    if amt != g["stake"]:
        await msg.reply_html(sc_all(
            f"❌ Stake exactly {fmt(g['stake'])} {g['currency']} hona chahiye, match kar!"
        ))
        return

    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)
    if g["currency"] == "gems":
        if d.get("gems", 0) < amt:
            await msg.reply_html(sc_all("❌ Enough gems nahi! 💎"))
            return
        await deduct_gems(u.id, amt)
    else:
        if d.get("balance", 0) < amt:
            await msg.reply_html(sc_all("❌ Enough coins nahi! 💰"))
            return
        await deduct_balance(u.id, amt)

    g["players"].append(u.id)
    g["joined"][u.id] = True
    g["pot"] += amt
    await msg.reply_html(sc_all(
        f"✅ {mention(u)} joined! 👥 Players: {len(g['players'])} | "
        f"Pot: {fmt(g['pot'])} {g['currency']}"
    ))


# ── /bid <amount>  (DM only — keep your bid secret!) ───────────────────
@games_gate
async def bid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    msg = update.effective_message

    if update.effective_chat.type != "private":
        await msg.reply_html(sc_all("🤫 <b>Bid DM mein bhejo!</b> Game ke group mein mat likho — secret rakho! 🎯"))
        return

    g = _game_by_player(u.id)
    if not g or g["phase"] != "bidding":
        await msg.reply_html(sc_all("❌ Koi active bidding round nahi hai ya tu game mein nahi hai."))
        return
    if u.id not in g["alive"]:
        await msg.reply_html(sc_all("❌ Tu eliminate ho chuka hai, bid nahi kar sakta. 💀"))
        return
    if u.id in g["bidded"]:
        await msg.reply_html(sc_all("❌ Tune is round mein already bid kar diya hai."))
        return
    if not context.args:
        await msg.reply_html(sc_all(
            "Usage: <code>/bid &lt;amount&gt;</code>\n"
            "Round mein sabse kam se ZYADA bid dal, warna eliminate! 🎯"
        ))
        return
    try:
        amt = int(context.args[0])
    except ValueError:
        await msg.reply_html(sc_all("❌ Bid ek number hona chahiye!"))
        return
    if amt <= 0:
        await msg.reply_html(sc_all("❌ Bid 0 se zyada hona chahiye!"))
        return
    if g["min_bid"] > 0 and amt <= g["min_bid"]:
        await msg.reply_html(sc_all(
            f"❌ Tera bid current lowest ({fmt(g['min_bid'])}) se ZYADA hona chahiye!"
        ))
        return

    # (Bids are pure numbers — no money moves. Balance is NOT consumed.)
    g["bids"][u.id] = amt
    g["bidded"][u.id] = True
    g["min_bid"] = amt if g["min_bid"] == 0 else min(g["min_bid"], amt)

    await msg.reply_html(sc_all(
        f"🎯 Bid lock: {fmt(amt)}. Ab wait kar round khatam hone ka! 🤞"
    ))


# ── Timers ──────────────────────────────────────────────────────────────────
async def _close_joining(context: ContextTypes.DEFAULT_TYPE):
    cid = context.job.data["chat_id"]
    g = _GAMES.get(cid)
    if not g or g["phase"] != "joining":
        return

    if len(g["players"]) < 2:
        for pid in g["players"]:
            await _refund(pid, g["stake"], g["currency"])
        await context.bot.send_message(
            cid,
            sc_all(
                f"😅 Game cancel! Sirf {len(g['players'])} player aaye. "
                f"Sabka stake wapas. 🔁"
            ),
            parse_mode="HTML",
        )
        _GAMES.pop(cid, None)
        return

    g["alive"] = list(g["players"])
    g["round"] = 1
    if getattr(context, "job_queue", None):
        context.job_queue.run_once(_begin_round, 2, data={"chat_id": cid})
    else:
        await _begin_round(context)


async def _begin_round(context: ContextTypes.DEFAULT_TYPE):
    cid = context.job.data["chat_id"]
    g = _GAMES.get(cid)
    if not g or g["phase"] == "done":
        return

    g["phase"] = "bidding"
    g["bids"] = {}
    g["bidded"] = {}
    g["min_bid"] = 0
    total_rounds = len(g["players"]) - 1

    alert = sc_all(
        f"🎯 <b>Round {g['round']} / {total_rounds}</b>\n\n"
        f"Players ({len(g['alive'])}): {await _names(g['alive'])}\n\n"
        f"Apne <b>DM</b> mein <code>/bid &lt;amount&gt;</code> bhejo — amount current "
        f"lowest se ZYADA hona chahiye, warna eliminate! ⏳ {BID_SECONDS}s hai."
    )
    await context.bot.send_message(cid, alert, parse_mode="HTML")

    # Nudge each survivor in DM so they know to bid.
    for pid in g["alive"]:
        try:
            await context.bot.send_message(
                pid,
                sc_all(
                    f"🎯 Round {g['round']} shuru! DM mein <code>/bid &lt;amount&gt;</code> bhejo "
                    f"(current lowest se zyada). {BID_SECONDS}s mein bid nahi toh eliminate! ⏳"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    if getattr(context, "job_queue", None):
        g["bid_job"] = context.job_queue.run_once(
            _end_round, BID_SECONDS, data={"chat_id": cid}
        )


async def _end_round(context: ContextTypes.DEFAULT_TYPE):
    cid = context.job.data["chat_id"]
    g = _GAMES.get(cid)
    if not g or g["phase"] != "bidding":
        return

    # Players who didn't bid this round are counted as 0 (auto-lowest).
    for pid in g["alive"]:
        if pid not in g["bidded"]:
            g["bids"][pid] = 0

    lowest_val = min(g["bids"][pid] for pid in g["alive"])
    lowest_players = [pid for pid in g["alive"] if g["bids"][pid] == lowest_val]
    loser = min(lowest_players)  # deterministic tie-break
    g["alive"].remove(loser)

    lu = await get_user(loser)
    loser_name = lu.get("full_name") or "User"
    await context.bot.send_message(
        cid,
        sc_all(
            f"🎰 <b>Round {g['round']} khatam!</b>\n\n"
            f"💀 Eliminated: {mention_id(loser, loser_name)} (bid {fmt(lowest_val)})\n"
            f"🟢 Bach gaye ({len(g['alive'])}): {await _names(g['alive'])}"
        ),
        parse_mode="HTML",
    )

    if len(g["alive"]) == 1:
        await _finish(context, cid)
    else:
        g["round"] += 1
        if getattr(context, "job_queue", None):
            context.job_queue.run_once(
                _begin_round, BETWEEN_ROUNDS, data={"chat_id": cid}
            )


async def _finish(context: ContextTypes.DEFAULT_TYPE, cid: int):
    g = _GAMES.get(cid)
    if not g:
        return
    g["phase"] = "done"
    winner = g["alive"][0]
    pot = g["pot"]
    currency = g["currency"]
    await _pay(winner, pot, currency)

    wu = await get_user(winner)
    wname = wu.get("full_name") or "User"
    await send_gif_result(
        context, cid, "roulette_win",
        sc_all(
            f"🏆 <b>Iota Roulette Winner!</b> 💙\n\n"
            f"👑 {mention_id(winner, wname)} jeet gaya pot of "
            f"<b>{fmt(pot)} {currency}</b>!\n\n"
            f"Mubarak ho! 💙"
        ),
    )
    import random as _rnd
    await _send_art(context, cid,
                   lambda: _render_roulette(_rnd.randint(0, 36)),
                   caption="🎰 ɪᴏᴛᴀ ʀᴏᴜʟᴇᴛᴛᴇ")
    _GAMES.pop(cid, None)
