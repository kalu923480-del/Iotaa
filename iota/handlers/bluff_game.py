"""
╔══════════════════════════════════════════════════════════╗
║   IOTA BOT — Bluff Card Game (multiplayer, original)      ║
║                                                            ║
║   Host: /bluff           — open a 2-minute lobby           ║
║   Join: /enter            — join during the lobby window   ║
║   Play: /drop <a b c...>  — drop card(s), claiming the     ║
║                             current called number          ║
║   Doubt:/judge             — challenge the last play       ║
║                                                            ║
║   Deck: N players → N copies each of card values 1-4       ║
║   (so 2 players = 8 cards, 3 players = 12 cards, etc.)      ║
║   Each player is dealt 4 cards face-down.                  ║
║                                                            ║
║   Iota calls out a number each round (cycling 1→2→3→4→1…). ║
║   You must drop cards CLAIMING they match that number —    ║
║   they might be telling the truth, or bluffing! The next   ║
║   player can either play along (/drop) or call /judge to   ║
║   doubt the last play.                                     ║
║                                                            ║
║   • Judge WRONG (the play was honest) → judge takes the    ║
║     whole pile into their own hand (penalty for a bad call)║
║   • Judge RIGHT (caught a bluff!) → the bluffer takes the  ║
║     whole pile, AND the judge gets to discard one card of  ║
║     their choice as a reward for the good catch.           ║
║                                                            ║
║   First player to empty their hand wins! 🏆                ║
╚══════════════════════════════════════════════════════════╝
"""
import random, asyncio, time, uuid, logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from utils.mongo_db import ensure_user, add_balance
from utils.helpers import mention, fmt
from utils.safe_html import safe_html
from utils.system_gate import games_gate
from utils.game_ui import send_gif_result

CARD_NAMES = {1: "A", 2: "B", 3: "C", 4: "D"}
SLOT_LETTERS = "abcd"  # how a player refers to their own hand positions when dropping

# chat_id -> game state
_bluff_lobbies: dict = {}     # lobby (pre-game, collecting /enter)
_bluff_games: dict = {}       # active games


def _new_lobby(chat_id: int, host_id: int, host_name: str) -> dict:
    return {
        "host_id":   host_id,
        "host_name": host_name,
        "players":   [{"id": host_id, "name": safe_html(host_name)}],
        "opened_at": int(time.time()),
    }


@games_gate
async def bluff_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; u = update.effective_user
    if chat.type == "private":
        await update.message.reply_html("🃏 Bluff sirf group mein khela ja sakta hai!"); return
    if chat.id in _bluff_lobbies or chat.id in _bluff_games:
        await update.message.reply_html("❌ Pehle se ek Bluff game/lobby active hai!"); return

    await ensure_user(u.id, u.username or "", u.full_name)
    _bluff_lobbies[chat.id] = _new_lobby(chat.id, u.id, u.first_name)

    await update.message.reply_html(
        f"🃏 <b>BLUFF GAME STARTED!</b>\n\n"
        f"👤 Host: {mention(u)}\n"
        f"⏳ <b>2 minute</b> window to join is now open!\n\n"
        f"📋 Join with: <code>/enter</code>\n"
        f"👥 Players so far: 1"
    )
    asyncio.create_task(_close_lobby_after(context, chat.id, 120))


async def enter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; u = update.effective_user
    lobby = _bluff_lobbies.get(chat.id)
    if not lobby:
        await update.message.reply_html("❌ Koi active Bluff lobby nahi! Host /bluff use kare."); return
    if any(p["id"] == u.id for p in lobby["players"]):
        await update.message.reply_html("✅ Tum already joined ho!"); return
    if len(lobby["players"]) >= 6:
        await update.message.reply_html("❌ Lobby full hai! (Max 6 players)"); return

    await ensure_user(u.id, u.username or "", u.full_name)
    lobby["players"].append({"id": u.id, "name": safe_html(u.first_name)})

    await update.message.reply_html(
        f"✅ {mention(u)} joined the Bluff game!\n"
        f"👥 Players: {len(lobby['players'])}"
    )


async def _close_lobby_after(context, chat_id: int, secs: int):
    await asyncio.sleep(secs)
    lobby = _bluff_lobbies.pop(chat_id, None)
    if not lobby:
        return
    if len(lobby["players"]) < 2:
        try:
            await context.bot.send_message(
                chat_id,
                "❌ Bluff game cancel ho gayi — kam se kam 2 players chahiye!"
            )
        except Exception:
            pass
        return
    await _start_game(context, chat_id, lobby)


def _build_deck(num_players: int) -> list:
    deck = []
    for v in (1, 2, 3, 4):
        deck += [v] * num_players
    random.shuffle(deck)
    return deck


async def _start_game(context, chat_id: int, lobby: dict):
    players = lobby["players"]
    deck = _build_deck(len(players))

    game = {
        "chat_id":  chat_id,
        "host_id":  lobby["host_id"],
        "players":  [],
        "turn":     0,
        "call":     1,
        "pile":     [],       # all cards currently in the pile (face down)
        "last_play": None,    # {"idx", "claimed", "cards":[...], "count"}
        "status":   "playing",
        "round_started": int(time.time()),
    }
    for i, p in enumerate(players):
        hand = [deck.pop() for _ in range(4)]
        game["players"].append({"id": p["id"], "name": p["name"], "hand": hand})

    _bluff_games[chat_id] = game

    # Try to DM each player their hand
    dm_failed = []
    for p in game["players"]:
        try:
            await context.bot.send_message(
                p["id"],
                f"🃏 <b>Your Bluff Game Hand</b>\n\n" + _hand_text(p["hand"]) +
                f"\n\nGame is in your group chat. Use /drop and /judge there!",
                parse_mode="HTML"
            )
        except Exception:
            dm_failed.append(p["name"])

    order_txt = " → ".join(p["name"] for p in game["players"])
    warn = ""
    if dm_failed:
        warn = (f"\n⚠️ Couldn't DM: {', '.join(dm_failed)} — DM the bot first, "
                f"then use /myhand here to check your cards.")

    current = game["players"][0]
    await context.bot.send_message(
        chat_id,
        f"🃏 <b>BLUFF GAME BEGINS!</b>\n\n"
        f"👥 Turn order: {order_txt}\n"
        f"📬 Hands sent via DM!{warn}\n\n"
        f"🎯 <b>Iota calls: DROP {CARD_NAMES[game['call']]}!</b>\n"
        f"👉 {mention(current)}'s turn — use <code>/drop a</code> or "
        f"<code>/drop a b c</code> to play!",
        parse_mode="HTML"
    )


def _hand_text(hand: list) -> str:
    lines = []
    for i, v in enumerate(hand):
        letter = SLOT_LETTERS[i]
        lines.append(f"  {letter}) Card value: <b>{CARD_NAMES[v]}</b> ({v})")
    return "\n".join(lines)


async def myhand_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; u = update.effective_user
    game = _bluff_games.get(chat.id)
    if not game:
        await update.message.reply_html("❌ Koi active Bluff game nahi!"); return
    player = next((p for p in game["players"] if p["id"] == u.id), None)
    if not player:
        await update.message.reply_html("❌ Tum is game mein nahi ho!"); return
    if not player["hand"]:
        await update.message.reply_html("🏆 Tumhara haath khaali hai — tum jeet chuke ho!"); return
    try:
        await context.bot.send_message(
            u.id,
            f"🃏 <b>Your Hand</b>\n\n{_hand_text(player['hand'])}",
            parse_mode="HTML"
        )
        await update.message.reply_html("📬 DM mein bheja!")
    except Exception:
        await update.message.reply_html("⚠️ Pehle bot ko DM mein /start karo!")


def _current_player(game):
    return game["players"][game["turn"]]


def _advance_turn(game):
    game["turn"] = (game["turn"] + 1) % len(game["players"])
    # Skip any player who has already won (empty hand)
    tries = 0
    while not game["players"][game["turn"]]["hand"] and tries < len(game["players"]):
        game["turn"] = (game["turn"] + 1) % len(game["players"])
        tries += 1


def _advance_call(game):
    game["call"] = game["call"] % 4 + 1


async def drop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; u = update.effective_user
    game = _bluff_games.get(chat.id)
    if not game:
        await update.message.reply_html("❌ Koi active Bluff game nahi!"); return

    current = _current_player(game)
    if current["id"] != u.id:
        await update.message.reply_html(f"❌ Abhi {current['name']} ki baari hai!"); return

    if not context.args:
        await update.message.reply_html(
            "❌ Usage: <code>/drop a</code> ya <code>/drop a b c</code>\n"
            "(a, b, c, d = tumhari hand ke positions)"
        ); return

    slots = [s.lower() for s in context.args[0]] if len(context.args) == 1 and len(context.args[0]) <= 4 and all(c in SLOT_LETTERS for c in context.args[0]) else [a.lower() for a in context.args]
    # Validate slots
    if not slots or any(s not in SLOT_LETTERS for s in slots):
        await update.message.reply_html("❌ Sirf a/b/c/d use karo! Example: <code>/drop a b</code>"); return
    if len(set(slots)) != len(slots):
        await update.message.reply_html("❌ Same card do baar drop nahi kar sakte!"); return

    indices = sorted(SLOT_LETTERS.index(s) for s in slots)
    if any(i >= len(current["hand"]) for i in indices):
        await update.message.reply_html("❌ Itni cards nahi hain tumhare haath mein!"); return

    # Remove cards from hand (highest index first to not shift others)
    dropped_values = []
    for i in sorted(indices, reverse=True):
        dropped_values.insert(0, current["hand"].pop(i))

    game["pile"].extend(dropped_values)
    game["last_play"] = {
        "player_idx": game["turn"],
        "player_id":  current["id"],
        "player_name": current["name"],
        "claimed":    game["call"],
        "cards":      dropped_values,
    }

    claimed_letter = CARD_NAMES[game["call"]]
    count = len(dropped_values)

    # Check win
    if not current["hand"]:
        await _end_game(context, chat.id, current)
        return

    _advance_turn(game)
    _advance_call(game)
    next_p = _current_player(game)

    await update.message.reply_html(
        f"🃏 {mention(current)} dropped <b>{count}</b> card(s), claiming <b>{claimed_letter}</b>!\n"
        f"🂠 Pile size: <b>{len(game['pile'])}</b>\n\n"
        f"🎯 Next call: <b>DROP {CARD_NAMES[game['call']]}!</b>\n"
        f"👉 {mention(next_p)}'s turn — <code>/drop</code> to play along, "
        f"or <code>/judge</code> to doubt {current['name']}!"
    )


async def judge_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; u = update.effective_user
    game = _bluff_games.get(chat.id)
    if not game:
        await update.message.reply_html("❌ Koi active Bluff game nahi!"); return

    current = _current_player(game)
    if current["id"] != u.id:
        await update.message.reply_html(f"❌ Abhi {current['name']} ki baari hai!"); return

    last = game["last_play"]
    if not last:
        await update.message.reply_html("❌ Abhi tak koi play nahi hui jise doubt karo!"); return

    claimed = last["claimed"]
    actual  = last["cards"]
    was_honest = all(v == claimed for v in actual)
    bluffer_idx = last["player_idx"]
    bluffer = game["players"][bluffer_idx]

    revealed = ", ".join(CARD_NAMES[v] for v in actual)

    # Reveal the actual dropped cards as a composed real-card image.
    try:
        from utils.card_assets import get_card_image, compose_cards_bytes
        _suits_cycle = ["spade", "heart", "diamond", "club"]
        imgs = [get_card_image(CARD_NAMES[v], _suits_cycle[(v - 1) % 4]) for v in actual]
        art = compose_cards_bytes(imgs, label=f"Claimed: {CARD_NAMES[claimed]}")
        await context.bot.send_photo(
            chat.id, art, parse_mode="HTML",
            caption=f"🂠 Revealed cards: <b>{revealed}</b>"
        )
    except Exception as e:
        logger.debug(f"bluff reveal image failed: {e}")

    if was_honest:
        # Judge was WRONG — judge takes the whole pile
        current["hand"].extend(game["pile"])
        result_text = (
            f"😱 <b>JUDGE WRONG!</b>\n\n"
            f"{bluffer['name']} was telling the truth — cards were: <b>{revealed}</b>\n"
            f"💀 {mention(u)} takes the WHOLE pile ({len(game['pile'])} cards) into their hand!"
        )
    else:
        # Judge was RIGHT — bluffer takes the pile, judge discards 1 card
        bluffer["hand"].extend(game["pile"])
        discard_bonus = ""
        if current["hand"]:
            discarded = current["hand"].pop(random.randrange(len(current["hand"])))
            discard_bonus = f"\n🎁 {mention(u)} discards a card as reward: <b>{CARD_NAMES[discarded]}</b>!"
        result_text = (
            f"🎯 <b>CAUGHT THE BLUFF!</b>\n\n"
            f"{bluffer['name']} lied! Real cards were: <b>{revealed}</b>\n"
            f"💀 {mention_html(bluffer)} takes the WHOLE pile ({len(game['pile'])} cards)!"
            f"{discard_bonus}"
        )

    game["pile"] = []
    game["last_play"] = None

    # Check for winner after judging
    if not bluffer["hand"]:
        await update.message.reply_html(result_text)
        await _end_game(context, chat.id, bluffer)
        return
    if not current["hand"]:
        await update.message.reply_html(result_text)
        await _end_game(context, chat.id, current)
        return

    _advance_turn(game)
    _advance_call(game)
    next_p = _current_player(game)

    await update.message.reply_html(
        f"{result_text}\n\n"
        f"🎯 Next call: <b>DROP {CARD_NAMES[game['call']]}!</b>\n"
        f"👉 {mention(next_p)}'s turn!"
    )


def mention_html(p_dict) -> str:
    return f"<a href='tg://user?id={p_dict['id']}'>{p_dict['name']}</a>"


async def _end_game(context, chat_id: int, winner: dict):
    game = _bluff_games.pop(chat_id, None)
    if not game:
        return
    await add_balance(winner["id"], 750)
    standing = sorted(game["players"], key=lambda p: len(p["hand"]))
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣"]
    board = "\n".join(
        f"{medals[i]} {p['name']} — {len(p['hand'])} cards left"
        for i, p in enumerate(standing)
    )
    over_text = (
        f"🏆 <b>BLUFF GAME OVER!</b>\n\n"
        f"👑 Winner: <b>{winner['name']}</b>!\n"
        f"💰 Prize: +750 coins!\n\n"
        f"📊 Final standings:\n{board}\n\n"
        f"🎮 Play again: /bluff"
    )
    await send_gif_result(context, chat_id, "bluff_win", over_text)


async def bluffend_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allow host to forfeit/cancel an active game. (/bluffend — not /end, that's the hack game's command)"""
    chat = update.effective_chat; u = update.effective_user
    game = _bluff_games.get(chat.id) or _bluff_lobbies.get(chat.id)
    if not game:
        await update.message.reply_html("❌ Koi active Bluff game nahi!"); return
    if u.id != game.get("host_id"):
        await update.message.reply_html("❌ Sirf host cancel kar sakta hai!"); return
    _bluff_games.pop(chat.id, None)
    _bluff_lobbies.pop(chat.id, None)
    await update.message.reply_html("❌ Bluff game cancel kar di gayi!")
