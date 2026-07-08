"""
Iota — 10 Extra Fun Commands (text toys + social ratings)
─────────────────────────────────────────────────────────
Pure, self-contained, no external API — every command is wrapped so a
missing argument or odd input is reported gracefully (never crashes).

TEXT TOYS (use args)
  /clap <text>      → adds 👏 between words
  /uwu <text>       → uwu-ify the text
  /vapor <text>     → full-width "vaporwave" text
  /bubble <text>    → circled / bubble letters
  /regional <text>  → 🇷🇪🇬🇮🇴🇳🇦🇱 regional-indicator emojis
  /leet <text>      → leetspeak
  /zalgo <text>     → glitchy "zalgo" text (capped, safe)

SOCIAL RATINGS (reply to a user, or uses you)
  /hot    → hotness percentage (stable per user)
  /rate   → rate out of 10 (stable per user)
  /nhie   → random "Never Have I Ever" prompt
"""
import logging
import random
import hashlib

from telegram import Update
from telegram.ext import ContextTypes

from utils.helpers import mention

logger = logging.getLogger(__name__)


# ── Stable per-user pseudo-random (so a person keeps the same rating) ──

def _stable_pct(uid: int, salt: str = "") -> int:
    h = hashlib.sha256(f"{uid}:{salt}".encode()).hexdigest()
    return int(h[:8], 16) % 100  # 0..99


# ── Text toys ───────────────────────────────────────────────────────────────

async def clap_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args or [])
    if not text:
        await update.effective_message.reply_html("👏 <b>Usage:</b> <code>/clap your text here</code>")
        return
    await update.effective_message.reply_text(f"👏 {text} 👏".replace(" ", " 👏 "))


async def uwu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args or [])
    if not text:
        await update.effective_message.reply_html("🐾 <b>Usage:</b> <code>/uwu your text here</code>")
        return
    out = text
    out = out.replace("r", "w").replace("R", "W")
    out = out.replace("l", "w").replace("L", "W")
    out = out.replace("ove", "uv")
    if out.endswith("!"):
        out = out + " uwu"
    elif "!" in out:
        out = out.replace("!", "! uwu")
    else:
        out = out + " uwu~"
    await update.effective_message.reply_text(out)


async def vapor_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args or [])
    if not text:
        await update.effective_message.reply_html("🌐 <b>Usage:</b> <code>/vapor your text here</code>")
        return
    out = []
    for ch in text:
        o = ord(ch)
        if 33 <= o <= 126:
            out.append(chr(o + 0xFEE0))
        elif ch == " ":
            out.append("\u3000")  # ideographic space
        else:
            out.append(ch)
    await update.effective_message.reply_text("".join(out))


async def bubble_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args or [])
    if not text:
        await update.effective_message.reply_html("🫧 <b>Usage:</b> <code>/bubble your text here</code>")
        return
    out = []
    for ch in text:
        if "a" <= ch <= "z":
            out.append(chr(ord(ch) - ord("a") + 0x24D0))
        elif "A" <= ch <= "Z":
            out.append(chr(ord(ch) - ord("A") + 0x24B6))
        elif "0" <= ch <= "9":
            if ch == "0":
                out.append("\u24EA")
            else:
                out.append(chr(ord(ch) - ord("1") + 0x2460))
        else:
            out.append(ch)
    await update.effective_message.reply_text("".join(out))


async def regional_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args or [])
    if not text:
        await update.effective_message.reply_html("🇮🇴 <b>Usage:</b> <code>/regional your text</code> (a-z only)")
        return
    out = []
    for ch in text.lower():
        if "a" <= ch <= "z":
            out.append(chr(ord(ch) - ord("a") + 0x1F1E6))
        else:
            out.append(ch)
    await update.effective_message.reply_text("".join(out))


async def leet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args or [])
    if not text:
        await update.effective_message.reply_html("1337 <b>Usage:</b> <code>/leet your text here</code>")
        return
    table = str.maketrans({
        "a": "4", "A": "4", "e": "3", "E": "3", "i": "1", "I": "1",
        "o": "0", "O": "0", "t": "7", "T": "7", "s": "5", "S": "5",
        "b": "8", "B": "8", "g": "6", "G": "6", "l": "1", "L": "1",
    })
    await update.effective_message.reply_text(text.translate(table))


async def zalgo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args or [])
    if not text:
        await update.effective_message.reply_html("⚡ <b>Usage:</b> <code>/zalgo your text here</code>")
        return
    marks = [c for c in range(0x0300, 0x036F + 1)]
    out = []
    for ch in text:
        out.append(ch)
        if ch.strip() and len(out) < 1500:  # keep total size sane
            for _ in range(random.randint(1, 3)):
                out.append(chr(random.choice(marks)))
    await update.effective_message.reply_text("".join(out))


# ── Social ratings ───────────────────────────────────────────────────────────

def _target_user(update: Update):
    msg = update.effective_message
    if msg.reply_to_message and msg.reply_to_message.from_user:
        return msg.reply_to_message.from_user
    return update.effective_user


async def hot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = _target_user(update)
    pct = _stable_pct(u.id, "hot")
    bar_len = 10
    filled = round(pct / 100 * bar_len)
    bar = "🔥" * filled + "░" * (bar_len - filled)
    await update.effective_message.reply_html(
        f"🔥 {mention(u)} is <b>{pct}%</b> hot!\n{bar}"
    )


async def rate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = _target_user(update)
    score = _stable_pct(u.id, "rate") % 11  # 0..10
    stars = "⭐" * score + "☆" * (10 - score)
    await update.effective_message.reply_html(
        f"⭐ I rate {mention(u)}: <b>{score}/10</b>\n{stars}"
    )


_NHIE_PROMPTS = [
    "Never have I ever sent a text to the wrong person.",
    "Never have I ever lied about my age online.",
    "Never have I ever stalked my crush's social media.",
    "Never have I ever faked being sick to skip something.",
    "Never have I ever eaten food off the floor.",
    "Never have I ever forgotten someone's name right after meeting them.",
    "Never have I ever sang in the shower.",
    "Never have I ever binge-watched a whole series in one day.",
    "Never have I ever blamed the wifi for my own mistake.",
    "Never have I ever secretly recorded a voice memo of myself singing.",
    "Never have I ever cried during a movie and denied it.",
    "Never have I ever pretended to be asleep to avoid talking.",
    "Never have I ever Googled myself.",
    "Never have I ever laughed at a meme I didn't understand.",
    "Never have I ever kept a screenshot of a chat 'just in case'.",
]


async def nhie_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = random.choice(_NHIE_PROMPTS)
    await update.effective_message.reply_html(
        f"🙅‍♂️ <b>Never Have I Ever</b>\n\n{prompt}"
    )
