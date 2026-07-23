"""
╔══════════════════════════════════════════════════════╗
║  IOTA BOT — Password Hacking Mini Game              ║
║                                                      ║
║  Based on Mastermind/Bulls & Cows logic:            ║
║  • HACKS  = digit in RIGHT position                 ║
║  • GLITCHES = digit in WRONG position               ║
║                                                      ║
║  Commands:                                           ║
║  /hack <reward> <digits 3-6>  — host starts game   ║
║  /register <amount> coins     — join game           ║
║  /guess <password>            — make a guess        ║
║  /end                         — host ends game      ║
╚══════════════════════════════════════════════════════╝
"""
import random, asyncio, time
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
import logging
logger = logging.getLogger(__name__)
from utils.mongo_db import (
    ensure_user, get_user, add_balance, deduct_balance, get_db,
    ensure_hack_rank, update_hack_rank,
)
from utils.game_ui import send_gif_result
from utils.helpers import mention, fmt
from utils.fonts import sc
from utils.system_gate import games_gate
from utils.game_rules import GAME_MIN_BET, validate_bet

# Active hack games: chat_id -> game_state
_hack_games: dict = {}

def _gen_password(length: int) -> str:
    """Generate unique-digit password of given length (no repeats for realism)."""
    digits = list("0123456789")
    random.shuffle(digits)
    return "".join(digits[:length])

def _score_guess(secret: str, guess: str) -> tuple:
    """Return (hacks, glitches) like Bulls & Cows."""
    hacks = sum(s == g for s, g in zip(secret, guess))
    glitches = sum(min(secret.count(d), guess.count(d)) for d in set(guess)) - hacks
    return hacks, glitches


@games_gate
async def hack_start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Host starts: /hack &lt;reward&gt; &lt;digit_length&gt;"""
    u    = update.effective_user
    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_html("🔐 Hack game sirf group mein khela ja sakta hai!"); return

    if len(context.args) < 2:
        await update.message.reply_html(
            "🔐 <b>Password Hacking Game</b>\n\n"
            "Usage: <code>/hack &lt;reward&gt; &lt;digits 3-6&gt;</code>\n"
            "Example: <code>/hack 1000 4</code>\n\n"
            "🔑 HACKS = sahi jagah sahi digit\n"
            "🌀 GLITCHES = galat jagah sahi digit"
        ); return

    if chat.id in _hack_games:
        await update.message.reply_html("❌ Already ek game chal raha hai! Pehle /end karo."); return

    try:
        reward = int(context.args[0])
        length = int(context.args[1])
    except ValueError:
        await update.message.reply_html("❌ Invalid format! Example: <code>/hack 1000 4</code>"); return

    if reward < GAME_MIN_BET:
        await update.message.reply_html(f"❌ Minimum reward is {GAME_MIN_BET} coins!"); return
    if not (3 <= length <= 6):
        await update.message.reply_html("❌ Password length 3 se 6 ke beech honi chahiye!"); return

    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)
    if d["balance"] < reward:
        await update.message.reply_html(
            f"❌ Tumhare paas {fmt(reward)} coins nahi!\n💰 Balance: {fmt(d['balance'])}"
        ); return

    await deduct_balance(u.id, reward)
    password = _gen_password(length)

    _hack_games[chat.id] = {
        "host_id":    u.id,
        "host_name":  u.first_name,
        "password":   password,
        "length":     length,
        "reward":     reward,
        "pot":        reward,
        "players":    {},   # user_id -> {name, coins_in, guesses: []}
        "status":     "open",
        "created":    int(time.time()),
        "attempts":   0,
    }

    await update.message.reply_html(
        f"🔐 <b>PASSWORD HACKING GAME STARTED!</b>\n\n"
        f"👤 Host: {mention(u)}\n"
        f"💰 Reward: <b>{fmt(reward)}</b> coins\n"
        f"🔢 Password Length: <b>{length} digits</b>\n\n"
        f"📋 <b>How to join:</b>\n"
        f"<code>/register {reward} coins</code>\n\n"
        f"📋 <b>How to guess:</b>\n"
        f"<code>/guess {'X'*length}</code>\n\n"
        f"🔑 HACKS = sahi position ka digit\n"
        f"🌀 GLITCHES = hai password mein par galat jagah\n\n"
        f"⏱️ Host can end with /end"
    )


async def hack_register_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/register &lt;amount&gt; coins"""
    u    = update.effective_user
    chat = update.effective_chat

    game = _hack_games.get(chat.id)
    if not game:
        await update.message.reply_html("❌ Koi active hack game nahi! Host use kare /hack"); return
    if game["status"] != "open":
        await update.message.reply_html("❌ Game already shuru ho gaya!"); return
    if u.id == game["host_id"]:
        await update.message.reply_html("❌ Host khud register nahi kar sakta!"); return
    if u.id in game["players"]:
        await update.message.reply_html("✅ Tum pehle se registered ho!"); return

    if not context.args:
        await update.message.reply_html(
            f"❌ Usage: <code>/register {game['reward']} coins</code>"
        ); return

    try:
        amount = int(context.args[0])
    except ValueError:
        await update.message.reply_html("❌ Invalid amount!"); return

    if amount < game["reward"]:
        await update.message.reply_html(
            f"❌ Minimum entry fee: {fmt(game['reward'])} coins!"
        ); return

    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)
    if d["balance"] < amount:
        await update.message.reply_html(
            f"❌ Balance kam hai! Tumhare paas {fmt(d['balance'])} coins hain."
        ); return

    await deduct_balance(u.id, amount)
    game["pot"]   += amount
    game["players"][u.id] = {
        "name":    u.first_name,
        "coins":   amount,
        "guesses": []
    }

    await update.message.reply_html(
        f"✅ <b>{mention(u)} joined the Hack Game!</b>\n\n"
        f"💎 Entry: {fmt(amount)} coins\n"
        f"💰 Total Pot: {fmt(game['pot'])} coins\n"
        f"👥 Players: {len(game['players'])}\n\n"
        f"Good luck, Iota believes in you! 💕\n"
        f"Use: <code>/guess {'?'*game['length']}</code>"
    )


async def hack_guess_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/guess &lt;password&gt;"""
    u    = update.effective_user
    chat = update.effective_chat

    game = _hack_games.get(chat.id)
    if not game:
        await update.message.reply_html("❌ Koi active hack game nahi!"); return

    if u.id not in game["players"] and u.id != game["host_id"]:
        await update.message.reply_html(
            f"❌ Pehle join karo!\n<code>/register {game['reward']} coins</code>"
        ); return

    if not context.args:
        await update.message.reply_html(
            f"❌ Usage: <code>/guess {'X'*game['length']}</code>"
        ); return

    guess = context.args[0].strip()
    if len(guess) != game["length"] or not guess.isdigit():
        await update.message.reply_html(
            f"❌ Password exactly <b>{game['length']}</b> digits ka hona chahiye!\n"
            f"Example: <code>/guess {'1'*game['length']}</code>"
        ); return

    password = game["password"]
    hacks, glitches = _score_guess(password, guess)
    game["attempts"] += 1

    # Track player's guesses
    if u.id in game["players"]:
        game["players"][u.id]["guesses"].append({
            "guess": guess, "hacks": hacks, "glitches": glitches
        })

    if hacks == game["length"]:
        # WINNER!
        winner_name = (game["players"].get(u.id) or {}).get("name", u.first_name)
        pot    = game["pot"]
        prize  = int(pot * 0.95)  # 5% fee
        winner_attempts = len((game["players"].get(u.id) or {}).get("guesses", []))
        game_data = _hack_games.pop(chat.id)

        await add_balance(u.id, prize)

        # ── Record Hack leaderboard stats (unified /leaders panel) ──
        try:
            hr = await ensure_hack_rank(u.id)
            await update_hack_rank(
                u.id,
                wins=hr["wins"] + 1,
                won_amount=hr["won_amount"] + prize,
                streak=hr["streak"] + 1,
                best_streak=max(hr["best_streak"], hr["streak"] + 1),
            )
        except Exception as e:
            logger.debug(f"hack_rank update failed: {e}")

        win_text = (
            f"💥 <b>BOOM! HACKED!</b> 💥\n\n"
            f"🏆 <b>{mention(u)} cracked the password!</b>\n"
            f"🔑 Password was: <code>{password}</code>\n"
            f"🎯 Attempts: <b>{winner_attempts}</b>\n"
            f"💰 Prize: <b>{fmt(prize)}</b> coins!\n\n"
            f"You win {fmt(prize)} coins! 🎉\n"
            f"Thanks for playing! See you next time~ 💕"
        )
        await send_gif_result(context, update.effective_chat.id, "hack_win", win_text)
        return

    # Not won yet
    history = ""
    if u.id in game["players"]:
        recent = game["players"][u.id]["guesses"][-3:]
        for g in recent:
            history += f"  <code>{g['guess']}</code> → 🔑 {g['hacks']} | 🌀 {g['glitches']}\n"

    await update.message.reply_html(
        f"🔐 <b>Guess Result</b>\n\n"
        f"👤 {mention(u)}: <code>{guess}</code>\n"
        f"🔑 <b>HACKS: {hacks}</b>  |  🌀 <b>GLITCHES: {glitches}</b>\n\n"
        f"{'📈 Recent guesses:' if history else ''}\n{history}"
        f"\n💡 Keep going! You can do it! ✨\n"
        f"Use: <code>/guess {'X'*game['length']}</code>"
    )


async def hack_end_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/end — only host can end"""
    u    = update.effective_user
    chat = update.effective_chat

    game = _hack_games.get(chat.id)
    if not game:
        await update.message.reply_html("❌ Koi active game nahi!"); return
    if u.id != game["host_id"]:
        await update.message.reply_html("❌ Sirf host game end kar sakta hai!"); return

    game = _hack_games.pop(chat.id)
    password = game["password"]

    # Refund players
    for pid, pdata in game["players"].items():
        await add_balance(int(pid), pdata["coins"])

    await update.message.reply_html(
        f"🔐 <b>Game Ended!</b>\n\n"
        f"🔑 Password was: <code>{password}</code>\n"
        f"💰 All entry fees refunded!\n\n"
        f"Thanks for playing! See you next time~ 💕"
    )
