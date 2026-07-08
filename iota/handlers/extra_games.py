"""
Iota Extra Games & Fun Features
- Tic Tac Toe
- Rock Paper Scissors
- Hangman
- Quiz
- Ship (compatibility)
- Horoscope
- Shayari
- Meme
- Work (earn coins)
- Group Story
- Compliment / Roast
- WhatIf (AI)
"""
import random
import asyncio
import json
import logging
import aiohttp
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from utils.mongo_db import ensure_user, get_user, update_user, add_balance
from utils.helpers import mention, fmt
from utils.ai_provider import call_ai
from utils.system_gate import games_gate
from config import SARVAM_API_KEY, SARVAM_CHAT_URL

logger = logging.getLogger(__name__)

# ── Active game state ─────────────────────────────────────────────────────────
_ttt_games: dict = {}   # chat_id -> game
_rps_games: dict = {}   # chat_id -> game
_hangman_games: dict = {}
_quiz_games: dict = {}
_stories: dict = {}     # chat_id -> story text

# ═══════════════════════════════════════════════════════════════════════
#  TIC TAC TOE
# ═══════════════════════════════════════════════════════════════════════

def _empty_board(): return [" "] * 9

def _check_winner(b):
    wins = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
    for a,c,d in wins:
        if b[a] == b[c] == b[d] != " ":
            return b[a]
    if " " not in b:
        return "draw"
    return None

def _ttt_kb(gid, board):
    symbols = {"X": "❌", "O": "⭕", " ": "⬜"}
    rows = []
    for r in range(3):
        row = []
        for c in range(3):
            i = r*3+c
            row.append(InlineKeyboardButton(
                symbols[board[i]],
                callback_data=f"ttt_{gid}_{i}" if board[i]==" " else f"ttt_noop"
            ))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


@games_gate
async def tictactoe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; msg = update.effective_message
    chat = update.effective_chat
    if chat.type == "private":
        await msg.reply_html("🚫 Use in a group!"); return
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_html("❌ Reply to someone to challenge them!\n/tictactoe [reply]"); return
    p2 = msg.reply_to_message.from_user
    if p2.id == u.id or p2.is_bot:
        await msg.reply_html("❌ Invalid opponent!"); return

    await ensure_user(u.id, u.username or "", u.full_name)
    await ensure_user(p2.id, p2.username or "", p2.full_name)

    import uuid
    gid = str(uuid.uuid4())[:6]
    _ttt_games[gid] = {
        "p1": u.id, "p2": p2.id, "p1_name": u.first_name, "p2_name": p2.first_name,
        "board": _empty_board(), "turn": u.id, "chat_id": chat.id
    }
    board = _empty_board()
    await msg.reply_html(
        f"⭕❌ <b>Tic Tac Toe!</b>\n\n"
        f"❌ {mention(u)} vs ⭕ {mention(p2)}\n\n"
        f"{mention(u)}'s turn (❌)",
        reply_markup=_ttt_kb(gid, board)
    )


async def ttt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; u = q.from_user
    if q.data == "ttt_noop":
        await q.answer("Already played!"); return
    await q.answer()
    parts = q.data.split("_")
    gid = parts[1]; pos = int(parts[2])
    game = _ttt_games.get(gid)
    if not game: await q.edit_message_text("❌ Game expired!"); return
    if u.id != game["turn"]:
        await q.answer("Not your turn!", show_alert=True); return

    board = game["board"]
    symbol = "X" if u.id == game["p1"] else "O"
    board[pos] = symbol
    winner = _check_winner(board)

    if winner:
        _ttt_games.pop(gid, None)
        if winner == "draw":
            await q.edit_message_text(
                f"⭕❌ <b>Tic Tac Toe — Draw!</b>\n\nGreat game both! 🤝",
                parse_mode="HTML"
            )
        else:
            win_id = game["p1"] if symbol == "X" else game["p2"]
            win_name = game["p1_name"] if symbol == "X" else game["p2_name"]
            reward = 200
            await add_balance(win_id, reward)
            await q.edit_message_text(
                f"⭕❌ <b>Tic Tac Toe!</b>\n\n"
                f"🏆 Winner: <b>{win_name}</b> ({symbol})\n💰 +{fmt(reward)}",
                parse_mode="HTML"
            )
    else:
        game["turn"] = game["p2"] if u.id == game["p1"] else game["p1"]
        next_name = game["p2_name"] if game["turn"] == game["p2"] else game["p1_name"]
        next_sym  = "⭕" if game["turn"] == game["p2"] else "❌"
        game["board"] = board
        await q.edit_message_text(
            f"⭕❌ <b>Tic Tac Toe!</b>\n\n"
            f"❌ {game['p1_name']} vs ⭕ {game['p2_name']}\n\n"
            f"{next_name}'s turn ({next_sym})",
            parse_mode="HTML",
            reply_markup=_ttt_kb(gid, board)
        )


# ═══════════════════════════════════════════════════════════════════════
#  ROCK PAPER SCISSORS
# ═══════════════════════════════════════════════════════════════════════

RPS_EMOJI = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
RPS_WINS  = {"rock": "scissors", "paper": "rock", "scissors": "paper"}


@games_gate
async def rps_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; msg = update.effective_message
    await ensure_user(u.id, u.username or "", u.full_name)
    import uuid; gid = str(uuid.uuid4())[:6]
    _rps_games[gid] = {"player": u.id, "chat_id": update.effective_chat.id}
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🪨 Rock",     callback_data=f"rps_{gid}_rock"),
        InlineKeyboardButton("📄 Paper",    callback_data=f"rps_{gid}_paper"),
        InlineKeyboardButton("✂️ Scissors", callback_data=f"rps_{gid}_scissors"),
    ]])
    await msg.reply_html(
        f"✊ <b>Rock Paper Scissors!</b>\n\n{mention(u)}, choose your move!",
        reply_markup=kb
    )


async def rps_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; u = q.from_user; await q.answer()
    parts = q.data.split("_"); gid = parts[1]; move = parts[2]
    game = _rps_games.pop(gid, None)
    if not game: await q.edit_message_text("❌ Expired!"); return
    if game["player"] != u.id:
        await q.answer("Not your game!", show_alert=True); return

    bot_move = random.choice(["rock", "paper", "scissors"])
    p_e = RPS_EMOJI[move]; b_e = RPS_EMOJI[bot_move]

    if move == bot_move:
        result = "🤝 Draw!"
    elif RPS_WINS[move] == bot_move:
        result = "🏆 You Win! +$150"
        await add_balance(u.id, 150)
    else:
        result = "😢 You Lose!"

    await q.edit_message_text(
        f"✊ <b>RPS Result!</b>\n\n"
        f"You: {p_e} vs Bot: {b_e}\n\n<b>{result}</b>",
        parse_mode="HTML"
    )


# ═══════════════════════════════════════════════════════════════════════
#  HANGMAN
# ═══════════════════════════════════════════════════════════════════════

HANGMAN_WORDS = [
    "python","telegram","economy","premium","kingdom","warrior","diamond",
    "rainbow","mystery","journey","balance","fantasy","villain","captain",
    "monster","thunder","gravity","whisper","quantum","phoenix"
]
HANGMAN_STAGES = ["😵","😦","😟","😨","😰","😱","💀"]


@games_gate
async def hangman_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; chat = update.effective_chat
    await ensure_user(u.id, u.username or "", u.full_name)
    word = random.choice(HANGMAN_WORDS)
    _hangman_games[chat.id] = {
        "word": word, "guessed": [], "wrong": 0,
        "max_wrong": 6, "player": u.id, "reward": len(word) * 150
    }
    masked = " ".join("_" if c not in [] else c for c in word)
    await update.message.reply_html(
        f"🎭 <b>Hangman!</b>\n\n{mention(u)} started!\n"
        f"Word: <code>{masked}</code>  ({len(word)} letters)\n"
        f"Lives: {'❤️' * 6}\n\n"
        f"Reply with a single letter to guess!\n"
        f"💰 Reward: {fmt(len(word)*150)}"
    )


async def hangman_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; u = update.effective_user
    game = _hangman_games.get(chat.id)
    if not game: return
    text = (update.message.text or "").strip().lower()
    if len(text) != 1 or not text.isalpha(): return

    word = game["word"]; guessed = game["guessed"]
    if text in guessed:
        await update.message.reply_html(f"Already guessed '{text}'!"); return

    guessed.append(text)
    if text in word:
        masked = " ".join(c if c in guessed else "_" for c in word)
        if "_" not in masked:
            _hangman_games.pop(chat.id, None)
            await add_balance(u.id, game["reward"])
            await update.message.reply_html(
                f"🎉 {mention(u)} guessed: <b>{word}</b>!\n💰 +{fmt(game['reward'])}"
            )
        else:
            await update.message.reply_html(
                f"✅ '{text}' found!\n<code>{masked}</code>\n"
                f"Lives: {'❤️' * (game['max_wrong']-game['wrong'])}"
            )
    else:
        game["wrong"] += 1
        masked = " ".join(c if c in guessed else "_" for c in word)
        stage = HANGMAN_STAGES[min(game["wrong"]-1, 6)]
        if game["wrong"] >= game["max_wrong"]:
            _hangman_games.pop(chat.id, None)
            await update.message.reply_html(
                f"{stage} Game Over! Word was: <b>{word}</b>"
            )
        else:
            lives_left = game["max_wrong"] - game["wrong"]
            await update.message.reply_html(
                f"❌ '{text}' not in word! {stage}\n"
                f"<code>{masked}</code>\n"
                f"Lives: {'❤️' * lives_left}  |  Wrong: {game['wrong']}/{game['max_wrong']}"
            )


# ═══════════════════════════════════════════════════════════════════════
#  QUIZ
# ═══════════════════════════════════════════════════════════════════════

QUIZ_QUESTIONS = [
    {"q": "What is the capital of India?", "opts": ["Mumbai","Delhi","Kolkata","Chennai"], "ans": 1},
    {"q": "Who invented the telephone?", "opts": ["Edison","Tesla","Bell","Marconi"], "ans": 2},
    {"q": "What is 15 × 15?", "opts": ["200","215","225","235"], "ans": 2},
    {"q": "Largest planet in solar system?", "opts": ["Saturn","Neptune","Uranus","Jupiter"], "ans": 3},
    {"q": "In what year did WW2 end?", "opts": ["1943","1944","1945","1946"], "ans": 2},
    {"q": "Chemical symbol for Gold?", "opts": ["Go","Gd","Au","Ag"], "ans": 2},
    {"q": "Which country has the most population?", "opts": ["USA","India","China","Russia"], "ans": 1},
    {"q": "Speed of light (approx km/s)?", "opts": ["200000","300000","400000","500000"], "ans": 1},
    {"q": "How many sides does a hexagon have?", "opts": ["5","6","7","8"], "ans": 1},
    {"q": "What language does 'Bot' come from?", "opts": ["Latin","Greek","Robot","German"], "ans": 2},
]

QUIZ_CATEGORIES = [
    "general knowledge", "science", "history", "geography", "sports",
    "movies", "technology", "space", "animals", "food", "math", "music",
]


async def _ai_generate_quiz(category: str = None) -> dict:
    """
    Use AI to generate a fresh, unique quiz question on the fly.
    Returns dict: {"q": str, "opts": [4 strings], "ans": int 0-3}
    Falls back to None on failure (caller uses static list instead).
    """
    cat = category or random.choice(QUIZ_CATEGORIES)
    prompt = (
        f"Generate ONE unique, interesting trivia quiz question about "
        f"{cat}. Make it medium difficulty, fun, and not overly common.\n\n"
        f"Respond with ONLY valid JSON, no markdown, no extra text, in "
        f"exactly this format:\n"
        f'{{"question": "...", "options": ["A","B","C","D"], '
        f'"correct_index": 0}}\n\n'
        f"correct_index must be 0, 1, 2, or 3 (zero-based index into options)."
    )
    try:
        messages = [
            {"role": "system", "content": "You are a quiz question generator. Output ONLY valid JSON, nothing else."},
            {"role": "user", "content": prompt}
        ]
        raw = await call_ai(messages, is_premium=False, max_tokens=300, temperature=1.0)
        raw = raw.strip()
        # Strip markdown fences if model added them anyway
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        q       = data["question"]
        opts    = data["options"]
        ans_idx = int(data["correct_index"])
        if len(opts) != 4 or not (0 <= ans_idx <= 3) or not q:
            return None
        return {"q": q, "opts": opts, "ans": ans_idx, "category": cat}
    except Exception:
        return None


@games_gate
async def quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)

    category = " ".join(context.args).lower() if context.args else None

    thinking = await update.message.reply_html("🧠 Quiz bana rahi hoon...")

    # Try AI-generated question first (fresh, unique every time)
    q_data = await _ai_generate_quiz(category)
    ai_generated = q_data is not None

    # Fallback to static bank if AI fails
    if not q_data:
        q_data = random.choice(QUIZ_QUESTIONS)

    import uuid; gid = str(uuid.uuid4())[:6]
    reward = random.randint(250, 500)
    _quiz_games[gid] = {"chat_id": chat.id, "answer": q_data["ans"], "reward": reward}

    opts = q_data["opts"]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"A) {opts[0]}", callback_data=f"quiz_{gid}_0"),
         InlineKeyboardButton(f"B) {opts[1]}", callback_data=f"quiz_{gid}_1")],
        [InlineKeyboardButton(f"C) {opts[2]}", callback_data=f"quiz_{gid}_2"),
         InlineKeyboardButton(f"D) {opts[3]}", callback_data=f"quiz_{gid}_3")],
    ])

    tag = "🤖 AI Quiz" if ai_generated else "❓ Quiz"
    cat_txt = f"\n📂 Category: <i>{q_data.get('category', category or 'general')}</i>" if ai_generated else ""

    await thinking.edit_text(
        f"{tag}!{cat_txt}\n\n<b>{q_data['q']}</b>\n\n💰 Reward: {fmt(reward)}",
        parse_mode="HTML",
        reply_markup=kb
    )

    # Auto-close after 45s if no one answers
    asyncio.create_task(_quiz_timeout(context, gid, 45))


async def _quiz_timeout(context, gid: str, secs: int):
    await asyncio.sleep(secs)
    game = _quiz_games.pop(gid, None)
    if not game:
        return  # already answered
    try:
        await context.bot.send_message(
            game["chat_id"],
            "⏱️ Quiz time khatam! Koi nahi jeeta. /quiz se phir try karo!",
            parse_mode="HTML"
        )
    except Exception:
        pass


async def quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; u = q.from_user; await q.answer()
    parts = q.data.split("_"); gid = parts[1]; choice = int(parts[2])
    game = _quiz_games.pop(gid, None)
    if not game: return
    if choice == game["answer"]:
        await add_balance(u.id, game["reward"])
        await q.edit_message_text(
            f"✅ <b>Correct!</b> {mention(u)} wins!\n💰 +{fmt(game['reward'])}",
            parse_mode="HTML"
        )
    else:
        await q.edit_message_text(
            f"❌ Wrong! Better luck next time {mention(u)}!",
            parse_mode="HTML"
        )


# ═══════════════════════════════════════════════════════════════════════
#  SHIP (Compatibility)
# ═══════════════════════════════════════════════════════════════════════

async def ship_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; msg = update.effective_message
    if msg.reply_to_message and msg.reply_to_message.from_user:
        p2 = msg.reply_to_message.from_user
    elif context.args:
        await msg.reply_html("❌ Reply to someone to ship!"); return
    else:
        await msg.reply_html("❌ Reply to someone to /ship!"); return

    pct = random.randint(0, 100)
    # Ship name = first half of p1 name + second half of p2 name
    n1 = u.first_name; n2 = p2.first_name
    ship_name = n1[:len(n1)//2] + n2[len(n2)//2:]

    bar = "💗" * (pct//10) + "🖤" * (10 - pct//10)
    if pct < 30:   verdict = "😬 Not really compatible..."
    elif pct < 60: verdict = "🙂 Maybe? Worth a try!"
    elif pct < 80: verdict = "💕 Great match!"
    else:          verdict = "💘 SOULMATES! 🔥"

    await msg.reply_html(
        f"💕 <b>Ship Name: {ship_name}</b>\n\n"
        f"{mention(u)} + {mention(p2)}\n\n"
        f"Compatibility: <b>{pct}%</b>\n"
        f"{bar}\n\n"
        f"{verdict}"
    )


# ═══════════════════════════════════════════════════════════════════════
#  COMPLIMENT & ROAST (AI powered)
# ═══════════════════════════════════════════════════════════════════════

# 🔴 UPGRADED: compliment/roast/meme now generate fresh AI content every
# time instead of picking from a small fixed list (the same handful of
# lines repeating gets old fast, and users notice). Static lists kept
# ONLY as an offline fallback if the AI call fails, so these commands
# still always reply with something rather than erroring out.

COMPLIMENTS = [
    "You light up every room you walk into! ✨",
    "Your smile could end wars! 💫",
    "You're basically a human sunshine! ☀️",
    "Anyone who knows you is incredibly lucky! 💖",
    "You have the energy of a thousand stars! 🌟",
    "You're not just smart — you're beautifully smart! 🧠💕",
    "The world is genuinely better with you in it! 🌍",
]

ROASTS = [
    "Your WiFi password is probably 'ilovemyself' isn't it? 😂",
    "You're the reason they put instructions on shampoo bottles! 🧴",
    "If laziness was a sport, you'd finally win something! 🏆",
    "You're not stupid — you just have bad luck thinking! 💭",
    "I'd roast you harder but my mom said I can't burn trash! 🗑️",
    "You're proof that evolution can go in reverse! 🐒",
]

_COMPLIMENT_SYSTEM = (
    "You are Iota, a cute Hinglish-speaking Telegram bot girl. Give a "
    "short, warm, genuine compliment — 1-2 lines, playful and sweet. "
    "Plain text with emojis, no markdown asterisks. Make it fresh and "
    "different each time, not generic."
)
_ROAST_SYSTEM = (
    "You are Iota, a sassy Hinglish-speaking Telegram bot girl. Give a "
    "short, playful, light-hearted roast — 1-2 lines, teasing but never "
    "genuinely mean (no slurs, no real insults about appearance/family/"
    "sensitive topics). Plain text with emojis, no markdown asterisks."
)


async def compliment_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; u = update.effective_user
    target = msg.reply_to_message.from_user if (msg.reply_to_message and msg.reply_to_message.from_user) else u
    try:
        text = await call_ai(
            [{"role": "system", "content": _COMPLIMENT_SYSTEM},
             {"role": "user", "content": f"Give {target.first_name} a compliment."}],
            is_premium=False, max_tokens=100, temperature=1.0,
        )
    except Exception as e:
        logger.debug(f"compliment_cmd AI failed, using fallback: {e}")
        text = random.choice(COMPLIMENTS)
    await msg.reply_html(f"💖 {mention(target)}\n\n{text}")


async def roast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; u = update.effective_user
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_html("❌ Reply to someone to roast them!"); return
    target = msg.reply_to_message.from_user
    try:
        text = await call_ai(
            [{"role": "system", "content": _ROAST_SYSTEM},
             {"role": "user", "content": f"Roast {target.first_name} playfully."}],
            is_premium=False, max_tokens=100, temperature=1.0,
        )
    except Exception as e:
        logger.debug(f"roast_cmd AI failed, using fallback: {e}")
        text = random.choice(ROASTS)
    await msg.reply_html(f"🔥 {mention(target)}\n\n{text}")


# ═══════════════════════════════════════════════════════════════════════
#  HOROSCOPE (AI)
# ═══════════════════════════════════════════════════════════════════════

SIGNS = ["aries","taurus","gemini","cancer","leo","virgo",
         "libra","scorpio","sagittarius","capricorn","aquarius","pisces"]
SIGN_EMOJI = {
    "aries":"♈","taurus":"♉","gemini":"♊","cancer":"♋","leo":"♌",
    "virgo":"♍","libra":"♎","scorpio":"♏","sagittarius":"♐",
    "capricorn":"♑","aquarius":"♒","pisces":"♓"
}


async def horoscope_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_html(
            "🌟 Usage: /horoscope &lt;sign&gt;\n\n"
            "Signs: " + " | ".join(SIGNS)
        ); return
    sign = args[0].lower()
    if sign not in SIGNS:
        await update.message.reply_html(f"❌ Unknown sign! Try: {', '.join(SIGNS)}"); return

    thinking = await update.message.reply_html(f"🔮 Reading {SIGN_EMOJI.get(sign,'')} {sign.title()}...")
    prompt = (
        f"Give a fun, sassy, and personalized daily horoscope for {sign.title()}. "
        f"Keep it under 5 lines. Be specific, witty, and encouraging. "
        f"End with a lucky number and lucky color."
    )
    headers = {"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"}
    payload = {"model": "sarvam-m", "messages": [{"role":"user","content":prompt}], "max_tokens":200}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(SARVAM_CHAT_URL, json=payload, headers=headers,
                              timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status == 200:
                    d = await r.json()
                    text = d["choices"][0]["message"]["content"].strip()
                    safe = text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
                    await thinking.edit_text(
                        f"{SIGN_EMOJI.get(sign,'')} <b>{sign.title()} Horoscope</b>\n\n{safe}",
                        parse_mode="HTML"
                    )
                else:
                    await thinking.edit_text("❌ Couldn't read the stars today!")
    except Exception as e:
        await thinking.edit_text(f"❌ Error: {e}")


# ═══════════════════════════════════════════════════════════════════════
#  SHAYARI (Hindi poem)
# ═══════════════════════════════════════════════════════════════════════

SHAYARIS = [
    "Mohabbat mein dil lagana seekh lo,\nZindagi ko pyaar se sajana seekh lo.\nJo milta hai woh khushi se lo,\nJo nahi milta usse bhul jaana seekh lo. 💕",
    "Dil ne kaha ek baat sunao,\nTum bin jeena nahi aata.\nAankhen teri yaad mein rooti hain,\nTumhe bhulana nahi aata. 🥀",
    "Zindagi ek safar hai suhana,\nYahan kal kya ho kisne jaana.\nDil se jiyo, pyaar se jiyo,\nKyunki waqt nahi lautata dobaara. ✨",
    "Tere bina yeh raatein adhuri hain,\nTere bina yeh baatein adhuri hain.\nKya karein dil ne yeh kis ko batayein,\nTere bina yeh saansein bhi adhuri hain. 🌙",
    "Khwab dekhna band mat karo,\nTaaron se mohabbat band mat karo.\nZindagi chhoti hai lekin sapne bade hain,\nIn sapno ko jeena band mat karo. 🌟",
]


async def shayari_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        f"🥀 <b>Shayari</b>\n\n<i>{random.choice(SHAYARIS)}</i>"
    )


# ═══════════════════════════════════════════════════════════════════════
#  MEME
# ═══════════════════════════════════════════════════════════════════════

MEME_CAPTIONS = [
    "Me trying to adult 😂",
    "My brain at 3am be like...",
    "POV: You sent a message and immediately regret it",
    "Nobody:\nAbsolutely nobody:\nMe at 2am:",
    "When someone calls during a game 😤",
    "That feeling when the bot actually works 🎉",
    "When Iota sends you a compliment 💕",
]

MEME_GIFS = [
    "https://media.giphy.com/media/3ohzdIuqJoo8QdKlnW/giphy.gif",
    "https://media.giphy.com/media/xT9IgG50Lg7rusXgqU/giphy.gif",
]

_MEME_CAPTION_SYSTEM = (
    "You are Iota, a witty Hinglish-speaking Telegram bot girl. Write a "
    "single short, funny meme-style caption — 1 line, relatable Telegram/"
    "group-chat humor. Plain text with emojis, no markdown asterisks. "
    "Make it fresh and different each time."
)


async def meme_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 🔴 FIX: MEME_GIFS above contained the same hardcoded giphy.com media
    # IDs that had rotted across this whole bot (confirmed dead — HTTP 403
    # on every one). Captions now come fresh from the AI each time
    # instead of a small repeating list, and the GIF comes from the live
    # GIPHY search already used everywhere else in the bot.
    try:
        caption = await call_ai(
            [{"role": "system", "content": _MEME_CAPTION_SYSTEM},
             {"role": "user", "content": "Give me a funny meme caption."}],
            is_premium=False, max_tokens=60, temperature=1.1,
        )
    except Exception as e:
        logger.debug(f"meme_cmd AI caption failed, using fallback: {e}")
        caption = random.choice(MEME_CAPTIONS)

    gif_url = None
    try:
        from utils.gif_provider import get_gif_for_mood
        gif_url = await get_gif_for_mood("laugh")
    except Exception as e:
        logger.debug(f"meme_cmd live GIF fetch failed: {e}")

    if gif_url:
        try:
            await update.message.reply_animation(gif_url, caption=f"😂 {caption}")
            return
        except Exception as e:
            logger.debug(f"meme_cmd GIF send failed: {e}")
    await update.message.reply_html(f"😂 <b>Meme</b>\n\n{caption}")


# ═══════════════════════════════════════════════════════════════════════
#  WORK (Earn coins)
# ═══════════════════════════════════════════════════════════════════════

WORK_MESSAGES = [
    ("worked as a hacker and stole some coins! 💻", (100, 400)),
    ("delivered pizza in the virtual world! 🍕", (80, 300)),
    ("mined some crypto! ⛏️", (150, 500)),
    ("streamed on Telegram TV! 📺", (100, 350)),
    ("sold memes online! 😂", (50, 250)),
    ("won a mini tournament! 🏆", (200, 600)),
    ("wrote code for Iota Bot! 🤖", (300, 700)),
    ("became a group admin for 1 hour! 👮", (100, 400)),
]

_work_cooldowns: dict = {}  # user_id -> last_work_ts


async def work_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    import time
    now = int(time.time()); cd = 3600
    last = _work_cooldowns.get(u.id, 0)
    if now - last < cd:
        rem = cd - (now - last)
        await update.message.reply_html(
            f"⏳ {mention(u)}, next work in <b>{rem//3600}h {(rem%3600)//60}m</b>!"
        ); return
    _work_cooldowns[u.id] = now
    job, (lo, hi) = random.choice(WORK_MESSAGES)
    earned = random.randint(lo, hi)
    await add_balance(u.id, earned)
    await update.message.reply_html(
        f"💼 {mention(u)} {job}\n💰 Earned: <b>{fmt(earned)}</b>"
    )


# ═══════════════════════════════════════════════════════════════════════
#  PROFILE (Shreya-style)
# ═══════════════════════════════════════════════════════════════════════

async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if msg.reply_to_message and msg.reply_to_message.from_user:
        tu = msg.reply_to_message.from_user
    else:
        tu = update.effective_user
    await ensure_user(tu.id, tu.username or "", tu.full_name)
    d = await get_user(tu.id)
    from utils.helpers import xp_level, rank_title, send_profile_photo_or_text
    lv = xp_level(d["xp"])
    from utils.mongo_db import get_card_rank, get_user_rank
    cr   = await get_card_rank(tu.id)
    rank = await get_user_rank(tu.id)
    icon = d.get("premium_emoji") or ("💓" if d["is_premium"] else "👤")
    caption = (
        f"🌟 <b>USER PROFILE: {mention(tu)}</b> 🌟\n\n"
        f"════ 💰 Global Wallet ════\n"
        f"💰 Balance: <b>{fmt(d['balance'])}</b>\n"
        f"💎 Gems: <b>{d['gems']}</b>\n"
        f"🏦 Wallet: <b>{fmt(d['wallet'])}</b>\n\n"
        f"════ 📊 Stats ════\n"
        f"🌍 Global Rank: <b>#{rank}</b>\n"
        f"⚡ XP: <b>{d['xp']}</b>  |  Level: <b>{lv}</b>\n"
        f"👑 Title: <b>{rank_title(lv)}</b>\n"
        f"💀 Kills: <b>{d['kills']}</b>  |  🔫 Robs: <b>{d['robs']}</b>\n\n"
        f"════ 🃏 Card Rank ════\n"
        f"🏅 Wins: <b>{cr['wins']}</b>  |  Losses: <b>{cr['losses']}</b>\n"
        f"🔥 Best Streak: <b>{cr['best_streak']}</b>\n"
        f"💵 Total Won: <b>{fmt(cr['won_amount'])}</b>\n\n"
        f"💓 Premium: <b>{'Yes ✅' if d['is_premium'] else 'No'}</b>\n"
        f"(Use /shop to spend coins)"
    )
    try:
        from handlers.gems_store import owned_perks_line
        perks = owned_perks_line(d)
        if perks:
            caption += f"\n\n✨ {perks}"
    except Exception:
        pass
    # Show the user's actual Telegram profile picture alongside their
    # stats. Falls back to text-only automatically if no PFP is set.
    await send_profile_photo_or_text(msg, context, tu.id, caption)


# ═══════════════════════════════════════════════════════════════════════
#  SHOP (Titles)
# ═══════════════════════════════════════════════════════════════════════

SHOP_TITLES = {
    "Legend":    500,
    "Champion":  800,
    "Warrior":   300,
    "Emperor":   2000,
    "God Mode":  5000,
    "Villain":   600,
    "Hero":      400,
    "Dark Lord": 1500,
}


async def shop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)
    args = context.args
    if not args:
        text = f"🛍️ <b>Iota Shop</b>\n\nYour balance: {fmt(d['balance'])}\n\n"
        text += "🏷️ <b>Custom Titles</b>\n"
        for title, price in SHOP_TITLES.items():
            text += f"• <b>{title}</b> — {fmt(price)}\n"
        text += "\nUse: /shop buy &lt;title&gt;"
        await update.message.reply_html(text); return

    if args[0].lower() == "buy":
        title = " ".join(args[1:])
        price = SHOP_TITLES.get(title)
        if not price:
            await update.message.reply_html(f"❌ Unknown title! Use /shop to see list."); return
        if d["balance"] < price:
            await update.message.reply_html(
                f"❌ You don't have enough TalkCoins for this!\n"
                f"Need: {fmt(price)} | Have: {fmt(d['balance'])}"
            ); return
        await update_user(u.id, balance=d["balance"]-price, premium_emoji=f"[{title}]")
        await update.message.reply_html(
            f"✅ {mention(u)} bought title: <b>{title}</b>! {fmt(price)} spent."
        )


# ═══════════════════════════════════════════════════════════════════════
#  GROUP STORY (AI collaborative)
# ═══════════════════════════════════════════════════════════════════════

async def story_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; u = update.effective_user
    if chat.type == "private":
        await update.message.reply_html("🚫 Use in a group!"); return
    args = context.args
    if not args:
        _stories.pop(chat.id, None)
        await update.message.reply_html(
            "📖 Story cleared! Start a new one:\n/story Once upon a time..."
        ); return
    start = " ".join(args)
    _stories[chat.id] = start
    # AI continues the story
    headers = {"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"}
    prompt = f"Continue this story in 2-3 lines, keeping it fun and engaging. Story so far: {start}"
    payload = {"model":"sarvam-m","messages":[{"role":"user","content":prompt}],"max_tokens":150}
    thinking = await update.message.reply_html("📖 Writing story...")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(SARVAM_CHAT_URL, json=payload, headers=headers,
                              timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status == 200:
                    d = await r.json()
                    cont = d["choices"][0]["message"]["content"].strip()
                    full = start + "\n\n" + cont
                    _stories[chat.id] = full
                    safe = full.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
                    await thinking.edit_text(
                        f"📖 <b>Group Story</b>\n\n{safe}\n\n"
                        f"<i>Continue: /story [your part]</i>",
                        parse_mode="HTML"
                    )
                else:
                    await thinking.edit_text(f"📖 <b>Story so far:</b>\n\n{start}")
    except Exception as e:
        await thinking.edit_text(f"📖 <b>Story:</b>\n\n{start}")


# ═══════════════════════════════════════════════════════════════════════
#  WHATIF (AI)
# ═══════════════════════════════════════════════════════════════════════

async def whatif_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_html("Usage: /whatif &lt;scenario&gt;\nExample: /whatif aliens landed today?"); return
    scenario = " ".join(args)
    thinking = await update.message.reply_html("🤔 Imagining...")
    headers = {"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"}
    prompt = f"Answer this 'what if' scenario in a fun, creative, and slightly dramatic way in 3-4 lines: What if {scenario}?"
    payload = {"model":"sarvam-m","messages":[{"role":"user","content":prompt}],"max_tokens":200}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(SARVAM_CHAT_URL, json=payload, headers=headers,
                              timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status == 200:
                    d = await r.json()
                    ans = d["choices"][0]["message"]["content"].strip()
                    safe = ans.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
                    await thinking.edit_text(
                        f"🤔 <b>What if {scenario}?</b>\n\n{safe}",
                        parse_mode="HTML"
                    )
                else:
                    await thinking.edit_text("❌ Couldn't imagine that!")
    except Exception as e:
        await thinking.edit_text(f"❌ Error: {e}")


# ═══════════════════════════════════════════════════════════════════════
#  SETTITLE (custom title in-chat)
# ═══════════════════════════════════════════════════════════════════════

async def settitle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)
    if not context.args:
        await update.message.reply_html(
            f"Current title: <b>{d.get('custom_title','None')}</b>\n"
            "Usage: /settitle &lt;your title&gt;\nCost: $500"
        ); return
    title = " ".join(context.args)[:20]
    cost = 500
    if d["balance"] < cost:
        await update.message.reply_html(f"❌ Need {fmt(cost)} coins!"); return
    await update_user(u.id, balance=d["balance"]-cost, custom_title=title)
    await update.message.reply_html(f"✅ Title set to: <b>{title}</b>! 💰 -{fmt(cost)}")


# ═══════════════════════════════════════════════════════════════════════
#  TOP (leaderboard with kills+rich+card combined)
# ═══════════════════════════════════════════════════════════════════════

async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.mongo_db import get_top_rich, get_top_kill, get_card_leaders
    rich_rows = await get_top_rich(5)
    kill_rows = await get_top_kill(5)
    card_rows = await get_card_leaders(5)
    medals = ["🥇","🥈","🥉","4️⃣","5️⃣"]

    text = "🏆 <b>Iota Leaderboards</b>\n\n"

    text += "💰 <b>Top Rich:</b>\n"
    for i, r in enumerate(rich_rows):
        name = r.get("full_name") or r.get("username") or "User"
        text += f"{medals[i]} {name} — {fmt(r.get('tb', r.get('balance', 0)))}\n"

    text += "\n💀 <b>Top Killers:</b>\n"
    for i, r in enumerate(kill_rows):
        name = r.get("full_name") or r.get("username") or "User"
        text += f"{medals[i]} {name} — {r['kills']} kills\n"

    text += "\n🃏 <b>Card Leaders:</b>\n"
    for i, r in enumerate(card_rows):
        try:
            u = await context.bot.get_chat(r["_id"])
            name = u.first_name
        except Exception:
            name = str(r["_id"])
        text += f"{medals[i]} {name} — {fmt(r['won_amount'])} won\n"

    await update.message.reply_html(text)


# ═══════════════════════════════════════════════════════════════════════
#  OCR (Image text extraction via AI vision)
# ═══════════════════════════════════════════════════════════════════════

async def ocr_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg.reply_to_message:
        await msg.reply_html("❌ Reply to an image to extract text from it!"); return
    photo = None
    if msg.reply_to_message.photo:
        photo = msg.reply_to_message.photo[-1]
    elif msg.reply_to_message.document and msg.reply_to_message.document.mime_type.startswith("image"):
        photo = msg.reply_to_message.document
    if not photo:
        await msg.reply_html("❌ No image found in replied message!"); return

    thinking = await msg.reply_html("🔍 Reading image...")
    try:
        file = await context.bot.get_file(photo.file_id)
        import io
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        buf.seek(0)
        import base64
        img_b64 = base64.b64encode(buf.read()).decode()

        headers = {"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"}
        payload = {
            "model": "sarvam-m",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                    {"type": "text", "text": "Extract and return ALL text visible in this image. Return only the text, nothing else."}
                ]
            }],
            "max_tokens": 500
        }
        async with aiohttp.ClientSession() as s:
            async with s.post(SARVAM_CHAT_URL, json=payload, headers=headers,
                              timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 200:
                    d = await r.json()
                    result = d["choices"][0]["message"]["content"].strip()
                    safe = result.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
                    await thinking.edit_text(f"📄 <b>Extracted Text:</b>\n\n{safe}", parse_mode="HTML")
                else:
                    await thinking.edit_text("❌ Could not extract text!")
    except Exception as e:
        await thinking.edit_text(f"❌ OCR failed: {e}")


# ═══════════════════════════════════════════════════════════════════════
#  REMINDME
# ═══════════════════════════════════════════════════════════════════════

async def remindme_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; args = context.args
    if len(args) < 2:
        await update.message.reply_html(
            "⏰ Usage: /remindme &lt;time&gt; &lt;message&gt;\n"
            "Time: 10m, 1h, 2h, 30m etc\n"
            "Example: /remindme 30m Check the oven!"
        ); return
    from utils.helpers import parse_duration
    secs = parse_duration(args[0])
    if not secs:
        await update.message.reply_html("❌ Invalid time! Use: 10m, 1h, 2h etc"); return
    reminder_text = " ".join(args[1:])
    chat_id = update.effective_chat.id

    await update.message.reply_html(
        f"⏰ Reminder set for <b>{args[0]}</b>!\n📝 {reminder_text}"
    )

    async def _remind():
        await asyncio.sleep(secs)
        try:
            await context.bot.send_message(
                chat_id,
                f"⏰ <b>Reminder!</b>\n\n{mention(u)}: {reminder_text}",
                parse_mode="HTML"
            )
        except Exception:
            pass

    asyncio.create_task(_remind())


# ═══════════════════════════════════════════════════════════════════════
#  STASH (Save messages privately)
# ═══════════════════════════════════════════════════════════════════════

_stash: dict = {}   # user_id -> [messages]


async def stash_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; msg = update.effective_message
    if msg.reply_to_message and msg.reply_to_message.text:
        text = msg.reply_to_message.text
        if u.id not in _stash: _stash[u.id] = []
        _stash[u.id].append(text[:200])
        await msg.reply_html(f"✅ Saved! Total stashed: {len(_stash[u.id])}")
    else:
        await msg.reply_html("❌ Reply to a message to save it!")


async def mystash_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    items = _stash.get(u.id, [])
    if not items:
        await update.message.reply_html("📦 Your stash is empty!"); return
    text = "📦 <b>Your Stash:</b>\n\n"
    for i, item in enumerate(items[-10:], 1):
        text += f"{i}. {item[:100]}\n\n"
    await update.message.reply_html(text)
