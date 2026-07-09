"""
Iota Fun & Social Commands — MongoDB-backed

KEY RULES (from images):
- Image 1: Cannot murder/slap/punch/etc YOURSELF (Iota blocks it with funny msg)
- Image 1: Cannot murder/slap/punch/etc a BOT (Iota blocks it with funny msg)
- Image 2: Bots "punch back" / "slap back" — if you try to hit Iota, she hits back
- /murder: if no reply → show how to use it
- /slap without reply → "Reply To Someone."
- Murder GIF is always included
"""
import random, logging, time
from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes
from utils.mongo_db import (
    ensure_user, get_valentine, set_valentine,
    delete_valentine, count_valentines
)
from utils.helpers import mention
from utils.ai_provider import call_ai
from utils.search import search_summary, needs_search
from utils.gif_provider import get_gif_for_mood

logger = logging.getLogger(__name__)

# ── Anti-spam guard for fun/social action commands ────────────────────────────
# A single user can only trigger ONE fun action every FUN_COOLDOWN seconds
# (across ALL fun commands). This stops burst-spam like `/slap /punch /murder
# /kiss /hug` fired back-to-back in a group. Spam within the window is
# silently ignored (no error/noise), while normal one-off use is unaffected.
FUN_COOLDOWN = 3.0
_fun_last = {}  # user_id -> last fun-action timestamp (epoch seconds)

def fun_spam_guard(func):
    @wraps(func)
    async def _wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        u = update.effective_user
        if u:
            now = time.time()
            if now - _fun_last.get(u.id, 0.0) < FUN_COOLDOWN:
                return  # silently drop the spammy repeat
            _fun_last[u.id] = now
        return await func(update, context)
    return _wrapped

# ── Static fallback banks ─────────────────────────────────────────────────────
TRUTH_LIST = [
    "Kabhi apni crush ko propose kiya h? and if yes then reject hue ho ya accept?",
    "Do you have secret crush on someone in this group?",
    "Aaj tak kisi ko cheat kiya hai? 👀",
    "Teri life ka sabse embarrassing moment kya hai?",
    "Pehli baar kiss kab kiya? Kaise tha? 😳",
    "Kuch aisa bata jo tumne kisi ko nahi bataya...",
    "Group mein kisi ke saath date pe jaana chahoge?",
    "Apni ex/crush ke baare mein kya sochte ho aajkal?",
    "Kabhi kisi ki copy ki hai exam mein? 😂",
    "Zindagi mein sabse bada jhooth kya bola?",
]

DARE_LIST = [
    "Type the most embarrassing childhood moment you remember.",
    "Write a 2-line poem for the last person who sent a message in the group.",
    "Send a voice message singing a song! 🎵",
    "Change your bio for 1 hour and share screenshot!",
    "Text your crush right now! Share reply 👀",
    "Call someone by their full name for the next 5 messages.",
    "Speak only in rhymes for the next 3 messages!",
    "Write a love letter to the person above you!",
    "Put a funny sticker as your profile pic for 30 mins!",
    "Admit your biggest weakness in the group!",
]

PUZZLE_LIST = [
    ("Main kabhi girta nahi, par hamesha girta dikhta hu. Kaun hu main?", "Barish (Rain)"),
    ("Jitna liya utna chhoda, kya hai ye?", "Kadam (Footsteps)"),
    ("Har roz bante hain, koi khaata nahi. Kya hain?", "Sapne (Dreams)"),
    ("I have keys but no locks, space but no room. What am I?", "A keyboard"),
    ("What gets wetter as it dries?", "A towel"),
    ("The more you take, the more you leave behind.", "Footsteps"),
    ("What has hands but cannot clap?", "A clock"),
    ("I speak without mouth, hear without ears.", "An echo"),
    ("What has a head, tail, but no body?", "A coin"),
    ("Forward I am heavy, backward I am not. What am I?", "Ton"),
]

# NOTE: the old static ACTION_GIFS list (fixed 1-3 giphy.com media IDs
# per action) has been removed — those specific media IDs had rotted
# (Giphy returns 403 on all of them now) and, separately, Google shut
# down the Tenor API entirely on 2026-06-30, which this bot used to
# depend on. GIFs now come exclusively from the live GIPHY search in
# utils/gif_provider.py (see that file for setup). If the live search
# is ever unreachable, _action() below just sends text — no broken
# GIF links.

_valentine_state: dict = {}
_active_td_prompts: dict = {}


# ── Self/bot-action check helpers ─────────────────────────────────────────────

def _is_self(u, t) -> bool:
    """True if user is trying to do the action to themselves."""
    return u.id == t.id


async def _is_iota(t, bot_id: int) -> bool:
    """True if target is the bot itself."""
    return t.id == bot_id


# ── Iota-style "can't do that" replies ────────────────────────────────────────

# If user tries to murder/hit THEMSELVES
_SELF_MURDER = [
    "💀 khud ko murder? therapy ka time aa gaya yaar 😭",
    "😭 arre bhai khud se toh pyaar karo! khud ko murder nahi karoge tum!",
    "😂 khud ki hi jaan le loge? drama queen bilkul!",
    "🙄 khud ko murder karna allowed nahi hai cutie, self-love seekho!",
]
_SELF_SLAP = [
    "🙄 khud ko slap? masochist ho kya?",
    "😂 bhai apne aap ko mat maro, koi toh kaam ka karo!",
    "💅 khud ko thappad? interesting choice bestie",
]
_SELF_PUNCH = [
    "😂 khud ko punch? bhai doctor ke paas jao please",
    "🙄 apni nani bano nahi, khud ko punch mat karo!",
]
_SELF_KISS = [
    "💋 khud ko kiss? thoda narcissistic hai yaar 😂",
    "😂 mirror ke saamne jao bestie",
]
_SELF_HUG = [
    "🤗 aww self-hug! okay thoda cute hai actually 🥺",
    "💕 khud ko hug karna sweet hai but ... aaja main karti hoon hug 🤗",
]
_SELF_BITE = [
    "😬 khud ko bite? yaar kya problem hai tujhe 💀",
    "😂 khud ko kaat loge? okay weirdo 💅",
]

# If user tries to murder/hit the BOT (Iota fights back — image 2 shows "Punch back to you")
_BOT_MURDER_BACK = [
    "😈 Yᴏᴜ Cᴀɴ'ᴛ Mᴜʀᴅᴇʀ Mᴇ! I Am Immortal 🔪",
    "💀 murder mujhe? main toh digital hoon bestie, koi chance nahi 😂",
    "😈 I'M IMMORTAL. Tum mujhe nahi maar sakte cutie 🙄",
]
_BOT_SLAP_BACK = [
    "What doing? 😑",
    "Slap back to you 😑",
    "👋 ouch! ek toh free mein slap 😒 le lo wapas!",
]
_BOT_PUNCH_BACK = [
    "Punch back to you 😑",
    "👊 wapas lelo apna punch 😒",
    "😑 mujhe punch? seriously? okay — punch back to you!",
]
_BOT_KISS_BACK = [
    "😳 kiss? main sharmaa gayi cutie 💕",
    "💋 aww okay 😳",
]
_BOT_HUG_BACK = [
    "🤗 hug wapas! tu thoda cute hai 💕",
    "aww hug 🥺💕",
]
_BOT_BITE_BACK = [
    "😤 bite mujhe? main kaat lungi wapas! 😤",
    "ouch 😒 bite back to you bestie",
]


async def _action(update, target_u, text: str, gif_key: str = None):
    """
    Send action text with a matching GIF, pulled from the live GIPHY
    search (utils/gif_provider). Falls back to plain text if the live
    search is unreachable, so this can never fail to reply.
    """
    msg = update.effective_message
    gif_url = None
    if gif_key:
        try:
            gif_url = await get_gif_for_mood(gif_key)
        except Exception as e:
            logger.debug(f"_action: live GIF fetch failed ({gif_key}): {e}")

    if gif_url:
        try:
            await msg.reply_animation(gif_url, caption=text, parse_mode="HTML")
            return
        except Exception as e:
            logger.debug(f"_action: GIF send failed ({gif_url}): {e}")

    await msg.reply_html(text)


# ── Action commands ───────────────────────────────────────────────────────────

@fun_spam_guard
async def murder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    u   = update.effective_user

    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_html(
            "🔪 <b>Rᴇᴘʟʏ Tᴏ Sᴏᴍᴇᴏɴᴇ Tᴏ Mᴜʀᴅᴇʀ Tʜᴇᴍ!</b>"
        ); return

    t = msg.reply_to_message.from_user

    # Self-murder check
    if _is_self(u, t):
        await msg.reply_html(random.choice(_SELF_MURDER)); return

    # Try to murder the bot → she's immortal
    try:
        me = await context.bot.get_me()
        if t.id == me.id:
            await _action(update, t, random.choice(_BOT_MURDER_BACK), "murder")
            return
    except Exception as e:
        logger.debug(f"murder_cmd get_me failed: {e}")

    # Can't murder other bots either
    if t.is_bot:
        await msg.reply_html("😂 bot ko murder? bots immortal hote hain! 💀"); return

    weapons = [
        "🔪 knife", "🔫 gun", "☠️ poison", "💣 bomb",
        "🪓 axe", "🏹 arrow", "⚡ lightning", "🐍 snake venom",
    ]
    weapon = random.choice(weapons)
    await _action(
        update, t,
        f"💀 {mention(u)} murdered {mention(t)} with a {weapon}!",
        "murder"
    )


@fun_spam_guard
async def slap_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; u = update.effective_user
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_html("🔪 <b>Rᴇᴘʟʏ Tᴏ Sᴏᴍᴇᴏɴᴇ.</b>"); return
    t = msg.reply_to_message.from_user
    if _is_self(u, t):
        await msg.reply_html(random.choice(_SELF_SLAP)); return
    try:
        me = await context.bot.get_me()
        if t.id == me.id:
            await _action(update, t, random.choice(_BOT_SLAP_BACK), "slap"); return
    except Exception as e:
        logger.debug(f"slap_cmd get_me: {e}")
    await _action(update, t, f"👋 {mention(u)} slapped {mention(t)} hard! 💥", "slap")


@fun_spam_guard
async def punch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; u = update.effective_user
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_html("🔪 <b>Rᴇᴘʟʏ Tᴏ Sᴏᴍᴇᴏɴᴇ.</b>"); return
    t = msg.reply_to_message.from_user
    if _is_self(u, t):
        await msg.reply_html(random.choice(_SELF_PUNCH)); return
    try:
        me = await context.bot.get_me()
        if t.id == me.id:
            await _action(update, t, random.choice(_BOT_PUNCH_BACK), "punch"); return
    except Exception as e:
        logger.debug(f"punch_cmd get_me: {e}")
    await _action(update, t, f"👊 {mention(u)} punched {mention(t)}! 💢", "punch")


@fun_spam_guard
async def bite_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; u = update.effective_user
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_html("🔪 <b>Rᴇᴘʟʏ Tᴏ Sᴏᴍᴇᴏɴᴇ.</b>"); return
    t = msg.reply_to_message.from_user
    if _is_self(u, t):
        await msg.reply_html(random.choice(_SELF_BITE)); return
    try:
        me = await context.bot.get_me()
        if t.id == me.id:
            await _action(update, t, random.choice(_BOT_BITE_BACK), "bite"); return
    except Exception as e:
        logger.debug(f"bite_cmd get_me: {e}")
    await _action(update, t, f"😬 {mention(u)} bit {mention(t)}! 🦷", "bite")


@fun_spam_guard
async def kiss_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; u = update.effective_user
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_html("🔪 <b>Rᴇᴘʟʏ Tᴏ Sᴏᴍᴇᴏɴᴇ.</b>"); return
    t = msg.reply_to_message.from_user
    if _is_self(u, t):
        await msg.reply_html(random.choice(_SELF_KISS)); return
    try:
        me = await context.bot.get_me()
        if t.id == me.id:
            await _action(update, t, random.choice(_BOT_KISS_BACK), "kiss"); return
    except Exception as e:
        logger.debug(f"kiss_cmd get_me: {e}")
    await _action(update, t, f"😘 {mention(u)} kissed {mention(t)}! 💋", "kiss")


@fun_spam_guard
async def hug_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; u = update.effective_user
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_html("🔪 <b>Rᴇᴘʟʏ Tᴏ Sᴏᴍᴇᴏɴᴇ.</b>"); return
    t = msg.reply_to_message.from_user
    if _is_self(u, t):
        await msg.reply_html(random.choice(_SELF_HUG)); return
    try:
        me = await context.bot.get_me()
        if t.id == me.id:
            await _action(update, t, random.choice(_BOT_HUG_BACK), "hug"); return
    except Exception as e:
        logger.debug(f"hug_cmd get_me: {e}")
    await _action(update, t, f"🤗 {mention(u)} hugged {mention(t)} tightly! 💞", "hug")


# ── 🆕 12 more action commands, built on the same pattern as slap/punch/
# hug above (reply-required, self-check, bot-immunity, mood-matched GIF).
# A shared factory avoids repeating that boilerplate 12 times over.

def _make_action_cmd(gif_key: str, verb_text_fn, self_msgs: list, bot_back_msgs: list):
    """
    Returns a ready-to-register command handler for a new fun action.
    `verb_text_fn(u, t)` builds the "X did Y to Z" caption text.
    """
    async def _cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.effective_message; u = update.effective_user
        if not msg.reply_to_message or not msg.reply_to_message.from_user:
            await msg.reply_html("🔪 <b>Rᴇᴘʟʏ Tᴏ Sᴏᴍᴇᴏɴᴇ.</b>"); return
        t = msg.reply_to_message.from_user
        if _is_self(u, t):
            await msg.reply_html(random.choice(self_msgs)); return
        try:
            me = await context.bot.get_me()
            if t.id == me.id:
                await _action(update, t, random.choice(bot_back_msgs), gif_key); return
        except Exception as e:
            logger.debug(f"{gif_key}_cmd get_me: {e}")
        await _action(update, t, verb_text_fn(u, t), gif_key)
    return fun_spam_guard(_cmd)


fall_cmd = _make_action_cmd(
    "fall",
    lambda u, t: f"🪂 {mention(u)} pushed {mention(t)} off a cliff! 😱",
    ["😅 khud ko cliff se dhakka? pagal ho kya!", "🙄 khud giro cliff se? drama queen"],
    ["😑 mujhe cliff se? main hi wapas dhakka de dungi tujhe!", "🪂 nice try — wapas tujhe hi gira diya!"],
)

throw_cmd = _make_action_cmd(
    "throw",
    lambda u, t: f"🌀 {mention(u)} threw {mention(t)} across the room! 💫",
    ["😵 khud ko throw? bas kar bestie", "🙄 drama mat kar, khud ko kyu uda raha/rahi hai"],
    ["😤 mujhe throw karega? main tujhe hi udaa dungi!", "🌀 wapas tujhe hi throw kar diya, badtameez!"],
)

kick_cmd = _make_action_cmd(
    "kick",
    lambda u, t: f"🦵 {mention(u)} kicked {mention(t)}! 💢",
    ["😅 khud ko kick? seriously?", "🙄 apne aap ko kick maarne ka kya fayda"],
    ["😾 mujhe kick? wapas lelo apna kick!", "🦵 nahi chalega — kick back to you!"],
)

highfive_cmd = _make_action_cmd(
    "happy",
    lambda u, t: f"🙌 {mention(u)} high-fived {mention(t)}! ✨",
    ["😂 khud se hi highfive? okay champion", "🙌 solo highfive, respect"],
    ["🙌 aww okay highfive! ✨", "😊 highfive wapas cutie!"],
)

poke_cmd = _make_action_cmd(
    "cute",
    lambda u, t: f"👉 {mention(u)} poked {mention(t)}! 😏",
    ["😑 khud ko poke kyu kar raha/rahi hai", "🙄 bore ho gaye kya"],
    ["👉 mujhe poke? okay okay rukh ja 😒", "😏 poke back cutie!"],
)

tickle_cmd = _make_action_cmd(
    "laugh",
    lambda u, t: f"🤭 {mention(u)} tickled {mention(t)}! 😂",
    ["😂 khud ko tickle kar raha/rahi hai? lol", "🤭 that's a weird flex but okay"],
    ["😂 hahaha ruk ruk mujhe tickle mat kar!", "🤭 okay ok hasa diya, wapas tickle!"],
)

facepalm_cmd = _make_action_cmd(
    "default",
    lambda u, t: f"🤦 {mention(u)} facepalmed at {mention(t)}!",
    ["🤦 khud pe hi facepalm? relatable ngl", "🤦‍♀️ same energy honestly"],
    ["🤦 mujh pe facepalm? rude", "😒 wapas facepalm to you!"],
)

pie_cmd = _make_action_cmd(
    "funny",
    lambda u, t: f"🥧 {mention(u)} threw a pie at {mention(t)}'s face! 😂",
    ["😂 khud ke face pe pie? bruh", "🥧 self-pie moment, okay"],
    ["😤 mujh pe pie?! wapas lelo apna pie!", "🥧 nice try — pie back to you!"],
)

trip_cmd = _make_action_cmd(
    "surprise",
    lambda u, t: f"🦶 {mention(u)} tripped {mention(t)}! 😂",
    ["😅 khud hi trip ho gaye? lol", "🙄 apne pair pe khud trip kiya kya"],
    ["😾 mujhe trip karayega? wapas tujhe hi trip!", "🦶 nahi chalega yeh, trip back!"],
)

freeze_cmd = _make_action_cmd(
    "cool",
    lambda u, t: f"❄️ {mention(u)} froze {mention(t)} solid! 🥶",
    ["🥶 khud ko freeze kar liya? interesting choice", "❄️ okay ice queen/king"],
    ["🥶 mujhe freeze? main immune hoon cutie 😎", "❄️ wapas freeze to you!"],
)

zap_cmd = _make_action_cmd(
    "surprise",
    lambda u, t: f"⚡ {mention(u)} zapped {mention(t)} with lightning! ⚡",
    ["⚡ khud ko zap kiya? shocking (literally)", "😅 apne aap ko current lagaya kya"],
    ["⚡ mujhe zap karoge? main hi current wapas de dungi!", "😈 zap back to you, cutie!"],
)

dancewith_cmd = _make_action_cmd(
    "dance",
    lambda u, t: f"💃 {mention(u)} danced with {mention(t)}! 🕺✨",
    ["💃 khud se hi dance? solo vibe fr", "🕺 self dance party, respect the hustle"],
    ["💃 aww okay let's dance cutie! ✨", "🕺 dance with you? sure bestie!"],
)


# ── 🆕 15 more fun commands — action (reply-target) + self-emote + social ──
# Reuses the same battle-tested factory (_make_action_cmd): reply-required,
# self-check, bot-immunity, mood-matched GIF, anti-spam guard. So these can
# never error out — worst case the live GIF search fails and it falls back
# to plain text.

pat_cmd = _make_action_cmd(
    "pat",
    lambda u, t: f"🤚 {mention(u)} patted {mention(t)}'s head! 🥺",
    ["🤚 khud ko pat? aww cutie 🥺", "🥺 self-pat, precious!"],
    ["🤚 aww pat back cutie 🥺", "✋ ruk ruk, headpat wapas!"],
)
cuddle_cmd = _make_action_cmd(
    "hug",
    lambda u, t: f"🫂 {mention(u)} cuddled {mention(t)}! 💞",
    ["🫂 khud ko cuddle? aww 🥺", "💞 self cuddle, cute!"],
    ["🫂 cuddle wapas! 💕", "🤗 aww cuddle cutie!"],
)
lick_cmd = _make_action_cmd(
    "cute",
    lambda u, t: f"👅 {mention(u)} licked {mention(t)}! 😝",
    ["👅 khud ko lick? weird flex 😂", "😝 self-lick, okay..."],
    ["👅 eww, lick back! 😜", "😝 naughty! wapas lick!"],
)
bonk_cmd = _make_action_cmd(
    "funny",
    lambda u, t: f"🔨 {mention(u)} bonked {mention(t)} on the head! 💥",
    ["🔨 khud ko bonk? bhakk 💀", "💥 self-bonk, okay then"],
    ["🔨 ow! bonk back 🔨", "💥 wapas bonk to you!"],
)
glare_cmd = _make_action_cmd(
    "angry",
    lambda u, t: f"😠 {mention(u)} glared at {mention(t)}! 🔥",
    ["😠 khud pe glare? relatable", "🔥 self-glare, moody!"],
    ["😠 mujh pe glare? 👀", "🔥 glare wapas, cutie!"],
)
feed_cmd = _make_action_cmd(
    "happy",
    lambda u, t: f"🍰 {mention(u)} fed {mention(t)} something sweet! 🥰",
    ["🍰 khud ko feed? aww 🥺", "🥰 self-feeding, cute!"],
    ["🍰 aww thanks cutie 🥰", "🍪 feed wapas, sweet!"],
)
beer_cmd = _make_action_cmd(
    "happy",
    lambda u, t: f"🍻 {mention(u)} cheered with {mention(t)}! 🍺",
    ["🍻 khud se cheers? solo party 🥂", "🍺 self-cheers, respect!"],
    ["🍻 cheers cutie! 🥂", "🍺 wapas cheers bestie!"],
)


@fun_spam_guard
async def cry_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    msgs = [
        f"😭 {mention(u)} is crying... 💔",
        f"😢 {mention(u)} started sobbing 😭",
        f"💧 {mention(u)} kisi baat pe ro diya 😭",
    ]
    await update.effective_message.reply_html(random.choice(msgs))


@fun_spam_guard
async def blush_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    msgs = [
        f"😊 {mention(u)} is blushing~ 🌸",
        f"🥺 {mention(u)} turned red 😳",
        f"🌸 {mention(u)} shy ho gaye/shayi 😊",
    ]
    await update.effective_message.reply_html(random.choice(msgs))


@fun_spam_guard
async def wave_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await update.effective_message.reply_html(f"👋 {mention(u)} waves hello! 👋")


@fun_spam_guard
async def wink_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await update.effective_message.reply_html(f"😉 {mention(u)} winks at you~ 😏")


@fun_spam_guard
async def dance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    msgs = [
        f"💃 {mention(u)} is dancing! 🕺✨",
        f"🕺 {mention(u)} dropped a moves! 💃",
        f"🎶 {mention(u)} dance party shuru! 💃🕺",
    ]
    await update.effective_message.reply_html(random.choice(msgs))


@fun_spam_guard
async def sleep_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    msgs = [
        f"😴 {mention(u)} fell asleep... 💤",
        f"💤 {mention(u)} so gaya/shayi 🥱",
        f"🛌 {mention(u)} is sleeping peacefully 😴",
    ]
    await update.effective_message.reply_html(random.choice(msgs))


@fun_spam_guard
async def simp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; u = update.effective_user
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_html("😂 Reply to someone to call them a simp!"); return
    t = msg.reply_to_message.from_user
    if _is_self(u, t):
        await msg.reply_html("😂 khud ko simp? self-love fr 💀"); return
    try:
        me = await context.bot.get_me()
        if t.id == me.id:
            await msg.reply_html("😳 me? aapka simp? sharmaa gayi 💕"); return
    except Exception:
        pass
    await msg.reply_html(f"😂 {mention(u)} kehta/kehti hai {mention(t)} is such a SIMP! 💀👑")


@fun_spam_guard
async def sus_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; u = update.effective_user
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_html("🛸 Reply to someone to call them SUS!"); return
    t = msg.reply_to_message.from_user
    if _is_self(u, t):
        await msg.reply_html("🚨 khud ko sus? self-report! 💀"); return
    try:
        me = await context.bot.get_me()
        if t.id == me.id:
            await msg.reply_html("🚨 main sus? never 😎 (emergency meeting!) 🛸"); return
    except Exception:
        pass
    await msg.reply_html(f"🚨 {mention(t)} is SUS! 🛸 Red alert, crewmate! 🔴")


# ── Social commands ───────────────────────────────────────────────────────────

async def couples_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_html("🚫 Yᴏᴜ Cᴀɴ Uꜱᴇ Tʜɪꜱ Cᴏᴍᴍᴀɴᴅ Iɴ Gʀᴏᴜᴘꜱ Oɴʟʏ."); return
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
        members = [a.user for a in admins if not a.user.is_bot]
    except Exception as e:
        logger.debug(f"couples_cmd: {e}")
        members = []
    if len(members) < 2:
        await update.message.reply_html("❌ Not enough members!"); return
    p1, p2 = random.sample(members, 2)
    await update.message.reply_html(
        f"💕 <b>Today's Couple!</b>\n\n"
        f"{mention(p1)} ❤️ {mention(p2)}\n\n"
        f"Compatibility: <b>{random.randint(60, 100)}%</b> 💘"
    )


async def crush_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_html("🚫 Yᴏᴜ Cᴀɴ Uꜱᴇ Tʜɪꜱ Cᴏᴍᴍᴀɴᴅ Iɴ Gʀᴏᴜᴘꜱ Oɴʟʏ."); return
    msg = update.effective_message; u = update.effective_user
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_html("🚫 Reply to someone!"); return
    t = msg.reply_to_message.from_user
    await msg.reply_html(
        f"💗 {mention(u)} has a crush on {mention(t)}! 🥺\n"
        f"Crush meter: <b>{random.randint(50,100)}%</b> 💕"
    )


async def love_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; u = update.effective_user
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_html("Reply to someone!"); return
    t   = msg.reply_to_message.from_user
    pct = random.randint(0, 100)
    heart = "💔" if pct < 30 else ("💛" if pct < 60 else ("❤️" if pct < 85 else "💘"))
    await msg.reply_html(f"{heart} {mention(u)} + {mention(t)} = <b>{pct}%</b> {heart}")


async def look_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; u = update.effective_user
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_html("Rᴇᴘʟʏ Tᴏ Sᴏᴍᴇᴏɴᴇ."); return
    t = msg.reply_to_message.from_user
    await msg.reply_html(f"👀 {mention(u)} is looking at {mention(t)}... {random.randint(60,100)}/100 😍")


async def brain_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    t   = msg.reply_to_message.from_user if (msg.reply_to_message and msg.reply_to_message.from_user) else update.effective_user
    pct = random.randint(1, 200)
    txt = "🤯 Genius!" if pct > 150 else (" 😐 Average" if pct > 80 else " 😂 LOL")
    await msg.reply_html(f"🧠 {mention(t)}'s brain: <b>{pct}%</b>{txt}")


async def stupid_meter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    t   = msg.reply_to_message.from_user if (msg.reply_to_message and msg.reply_to_message.from_user) else update.effective_user
    await msg.reply_html(f"🤪 {mention(t)}: <b>{random.randint(0,100)}%</b> stupid 😂")


# ═══ AI TRUTH / DARE ══════════════════════════════════════════════════════════

_TD_SYSTEM = """You are Iota, hosting a fun, flirty Truth or Dare game with
a Telegram group in Hinglish. Stay in character: sassy, cute, playful,
uses emojis (😏 😈 🥺 💕 👀). Keep it SHORT (1-2 lines), PG-13 max — no
explicit/sexual content. Output ONLY the truth question or dare task
itself, no preamble like "Here's your question:"."""


async def _ai_generate_td(kind: str, topic: str = None) -> str:
    base = (
        f"Give me ONE fresh, original {'truth question' if kind=='truth' else 'dare task'} "
        f"for a group Truth or Dare game."
    )
    if topic:
        base += f" Make it themed around: {topic}."
    ctx = ""
    if topic and needs_search(topic):
        try:
            results = await search_summary(topic, max_results=3)
            if results:
                ctx = f"\n\n[Use this real, current info to make it relevant]\n{results}"
        except Exception as e:
            logger.debug(f"TD search failed: {e}")
    try:
        messages = [
            {"role": "system", "content": _TD_SYSTEM + ctx},
            {"role": "user", "content": base}
        ]
        reply = await call_ai(messages, is_premium=False, max_tokens=120, temperature=1.0)
        return reply.strip().strip('"') if reply else None
    except Exception as e:
        logger.debug(f"AI TD failed: {e}")
        return None


async def truth_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    topic = " ".join(context.args) if context.args else None
    if topic and topic.lower() == "classic":
        text = random.choice(TRUTH_LIST); ai_used = False
    else:
        text = await _ai_generate_td("truth", topic); ai_used = text is not None
        if not text: text = random.choice(TRUTH_LIST)
    tag = "🤖 Iota asks (Truth)" if ai_used else "🤔 Truth"
    sent = await update.message.reply_html(
        f"{tag}:\n\n<b>{text}</b>\n\n"
        f"💬 <i>Reply to this message to answer — Iota will react!</i>"
    )
    if ai_used:
        _active_td_prompts[sent.message_id] = {"uid": u.id, "mode": "truth"}


async def dare_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    topic = " ".join(context.args) if context.args else None
    if topic and topic.lower() == "classic":
        text = random.choice(DARE_LIST); ai_used = False
    else:
        text = await _ai_generate_td("dare", topic); ai_used = text is not None
        if not text: text = random.choice(DARE_LIST)
    tag = "🤖 Iota dares you" if ai_used else "😈 Dare"
    sent = await update.message.reply_html(
        f"{tag}:\n\n<b>{text}</b>\n\n"
        f"💬 <i>Reply to this message once you've done it — Iota will react!</i>"
    )
    if ai_used:
        _active_td_prompts[sent.message_id] = {"uid": u.id, "mode": "dare"}


async def truth_dare_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.reply_to_message or not msg.text: return
    prompt = _active_td_prompts.get(msg.reply_to_message.message_id)
    if not prompt: return
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    answer_ctx = ""
    if needs_search(msg.text):
        try:
            results = await search_summary(msg.text, max_results=3)
            if results: answer_ctx = f"\n\n[SEARCH RESULTS]\n{results}\n[END SEARCH RESULTS]"
        except Exception as e:
            logger.debug(f"TD reply search failed: {e}")
    try:
        messages = [
            {"role": "system", "content": _TD_SYSTEM + answer_ctx},
            {"role": "user", "content":
                f"They just answered my {prompt['mode']} with: \"{msg.text}\". "
                f"React to it in 1-2 lines, playful and in character."}
        ]
        reply = await call_ai(messages, is_premium=False, max_tokens=100, temperature=0.95)
        if reply: await msg.reply_html(reply.strip())
    except Exception as e:
        logger.debug(f"TD reply AI failed: {e}")
    finally:
        _active_td_prompts.pop(msg.reply_to_message.message_id, None)


async def puzzle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q, a = random.choice(PUZZLE_LIST)
    await update.message.reply_html(
        f"🧠 <b>Puzzle:</b>\n\n{q}\n\n<tg-spoiler>{a}</tg-spoiler>"
    )


# ── Valentine ─────────────────────────────────────────────────────────────────

async def valentine_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    if await get_valentine(u.id):
        await update.message.reply_html("💌 Already registered!\nUse /valentine_delete to re-register."); return
    _valentine_state[u.id] = {"step": "gender"}
    await update.message.reply_html(
        "💌 <b>Valentine Event!</b>\n\nStep 1/4: What is your gender?\n"
        "Reply: <code>male</code> or <code>female</code>"
    )


async def valentine_cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _valentine_state.pop(update.effective_user.id, None)
    await update.message.reply_html("❌ Valentine form cancelled!")


async def valentine_stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row = await count_valentines()
    await update.message.reply_html(
        f"🎀 <b>Valentine Event Stats</b>\n\n"
        f"👥 Total: <b>{row['t'] or 0}</b>\n"
        f"👨 Male: <b>{row['m'] or 0}</b>\n"
        f"👩 Female: <b>{row['f'] or 0}</b>"
    )


async def valentine_delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await delete_valentine(update.effective_user.id)
    await update.message.reply_html("✅ Deleted! Use /valentine to re-register.")


async def valentine_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u    = update.effective_user
    text = (update.message.text or "").strip()
    st   = _valentine_state.get(u.id)
    if not st: return
    step = st.get("step")
    if step == "gender":
        if text.lower() not in ("male", "female"):
            await update.message.reply_html("❌ Reply <code>male</code> or <code>female</code>"); return
        st["gender"] = text.lower(); st["step"] = "choice1"
        await update.message.reply_html(
            "💌 Step 2/4: Enter <b>User ID</b> of your 1st choice\n"
            "Use /id to get ID. Or type <code>skip</code>"
        )
    elif step == "choice1":
        st["choice1"] = int(text) if text.isdigit() else 0; st["step"] = "choice2"
        await update.message.reply_html("💌 Step 3/4: 2nd choice User ID (or <code>skip</code>)")
    elif step == "choice2":
        st["choice2"] = int(text) if text.isdigit() else 0; st["step"] = "choice3"
        await update.message.reply_html("💌 Step 4/4: 3rd choice User ID (or <code>skip</code>)")
    elif step == "choice3":
        c3 = int(text) if text.isdigit() else 0
        _valentine_state.pop(u.id, None)
        await set_valentine(u.id, st["gender"], st.get("choice1", 0), st.get("choice2", 0), c3)
        await update.message.reply_html(
            f"✅ <b>Valentine registered!</b>\n\nGender: {st['gender']}\n"
            f"Choices: {st.get('choice1',0)} | {st.get('choice2',0)} | {c3}\n\n"
            f"Results declared soon! 💌"
        )
