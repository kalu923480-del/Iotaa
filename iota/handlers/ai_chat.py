"""
Iota AI Chat — Upgraded
- Real-time date/time injected into every system prompt automatically
- No web search (DuckDuckGo / Wikipedia removed — burned tokens / rate limits)
- **bold** markdown from AI converted to <b>bold</b> HTML for Telegram
- Smart tag detection in groups (unchanged)
- Per-user private memory (30 days auto-delete)
"""
import re, logging, random
from datetime import datetime, timezone, timedelta
from telegram import Update
from telegram.ext import ContextTypes
from utils.mongo_db import ensure_user, get_user, update_last_seen
from utils.ai_provider import call_ai
from utils.ai_memory import save_memory, get_memory, clear_memory
from utils.connect import get_partner_id
from utils.gif_provider import get_gif_for_mood
from utils.telegram_safe import chat_action, ACTION_TYPING
from utils.ratelimit import ratelimit
from config import OWNER_USERNAME, OWNER_ID, BOT_NAME, OWNER_NAME, BOT_USERNAME, BOT_AGE, BOT_FROM, BOT_DOB

logger = logging.getLogger(__name__)

# ── India Standard Time (UTC+5:30) using stdlib only ─────────────────────────
_IST_OFFSET = timezone(timedelta(hours=5, minutes=30))

def _now_ist() -> str:
    """Return current IST date+time string — injected into every AI call."""
    now = datetime.now(_IST_OFFSET)
    day_names = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    month_names = ["","January","February","March","April","May","June",
                   "July","August","September","October","November","December"]
    day   = day_names[now.weekday()]
    month = month_names[now.month]
    hour  = now.hour % 12 or 12
    ampm  = "AM" if now.hour < 12 else "PM"
    return f"{day}, {now.day:02d} {month} {now.year} — {hour:02d}:{now.minute:02d} {ampm} IST"


# Compact system prompt cached; only date stamp is refreshed per call.
_SYSTEM_BODY_CACHE: str | None = None


def _system_body() -> str:
    """Static persona + rules (no date). Built once, reused every turn."""
    global _SYSTEM_BODY_CACHE
    if _SYSTEM_BODY_CACHE is not None:
        return _SYSTEM_BODY_CACHE
    from utils.command_knowledge import build_help_text_compact
    commands_text = build_help_text_compact()
    _SYSTEM_BODY_CACHE = f"""You are Iota (@{BOT_USERNAME}) — ek real Hinglish ladki, chatbot/assistant nahi.

Owner: {OWNER_NAME} ({OWNER_USERNAME}).
Identity (sirf jab explicitly pooche): umar {BOT_AGE}, {BOT_FROM}, DOB {BOT_DOB}.
Bina puche umar/DOB/location mat bol. "AI hai?" pe deny + real-girl vibe.

Style:
• Cute, thodi flirty/sassy Hinglish. Short replies: 6–20 words (max ~3 lines).
• Emoji sparingly. No markdown asterisks. No fake links/URLs.
• Mood vary karo; stock phrases mat dohraao.
• Games/commands tabhi jab user pooche.
• Web search OFF — live news/prices pe honest raho, fake live facts mat banaao.
• Date/time system line user ko mat echo karo.
• Privacy: doosre user ki personal baat mat share karo.

Features (yahi bol sakti ho; owner tools nahi jaanti):
{commands_text}

Group: sirf jab @mention, reply to you, ya word "iota" aaye.
"""
    return _SYSTEM_BODY_CACHE


def _build_system() -> str:
    """Fresh date stamp + cached compact persona (token-cheap)."""
    return (
        f"{_system_body()}\n"
        f"🕐 NOW (IST): {_now_ist()}\n"
        f"(Date/time sawalon ke liye ye use karo. Kabhi mat bolna date nahi pata.)\n"
    )


# ── Markdown → HTML converter ─────────────────────────────────────────────────
# Fixes the "**text** stays as literal asterisks" bug on Telegram.

def _md_to_html(text: str) -> str:
    """
    Convert common AI markdown output to Telegram-safe HTML.
    Handles: **bold**, *italic*, `code`, ```code blocks```
    Also escapes raw < > & to prevent HTML injection.
    """
    # Escape HTML special chars first
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Code blocks (``` ... ```) → <code>
    text = re.sub(r'```[a-z]*\n?(.*?)```', lambda m: f'<code>{m.group(1).strip()}</code>', text, flags=re.DOTALL)
    # Inline code → <code>
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    # **bold** → <b>bold</b>
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    # *italic* or _italic_ → <i>italic</i>
    text = re.sub(r'\*([^*\n]+?)\*', r'<i>\1</i>', text)
    text = re.sub(r'_([^_\n]+?)_', r'<i>\1</i>', text)
    return text


# 🔴 SAFETY NET: strip any leaked [SEARCH RESULTS] block from the AI's
# reply. The system prompt explicitly forbids the model from echoing
# this block back, but LLMs often ignore that and echo it anyway —
# SOMETIMES with the [END SEARCH RESULTS] terminator, but FREQUENTLY
# just a bare "[SEARCH RESULTS]" tag followed by the raw results. Both
# shapes MUST be removed, while NEVER touching Iota's own natural prose
# that comes after the block.
#
# History of this bug:
#  • v1 (original): r'\[?SEARCH RESULTS\]?.*?(\Z)' — matched the bare
#    phrase and deleted everything to end-of-string → replies vanished.
#  • v2 (last fix): required the [END SEARCH RESULTS] terminator → too
#    strict; the common bare "[SEARCH RESULTS]" echo slipped through and
#    was shown to the user verbatim.
#  • v3 (this): handle BOTH the delimited block AND the dangling tag.
_SEARCH_LEAK_DELIM = re.compile(
    r'\[SEARCH RESULTS[^\]]*\][\s\S]*?\[END SEARCH RESULTS\]',
    re.IGNORECASE | re.DOTALL
)
# Dangling "[SEARCH RESULTS]" with NO terminator: remove the tag line and
# the raw result lines that follow, up to the next blank line (paragraph
# break). This preserves Iota's own prose that continues after it.
_SEARCH_LEAK_DANGLING = re.compile(
    r'\[SEARCH RESULTS[^\]]*\][\s\S]*?\n\n',
    re.IGNORECASE | re.DOTALL
)
# Only strip the exact 🔍 summary format we inject — never a 🔍 that's
# just part of Iota's normal emoji usage. Line-bounded (NO DOTALL) so the
# `.*` can't bleed past the numbered result lines into Iota's own prose.
_SEARCH_EMOJI_LEAK_RE = re.compile(
    r'🔍\s*Real-time info for[^\n]*(?:\n\d+\..*)*',
    re.IGNORECASE
)


def _strip_search_leak(text: str) -> str:
    # 1) Fully-delimited block (open + END) — most precise, do first.
    cleaned = _SEARCH_LEAK_DELIM.sub('', text)
    # 2) Dangling bare tag → strip to the next paragraph break.
    cleaned = _SEARCH_LEAK_DANGLING.sub('', cleaned)
    # 3) Our injected 🔍 summary echoed without brackets.
    cleaned = _SEARCH_EMOJI_LEAK_RE.sub('', cleaned)
    # 4) Final sweep: remove any stray "[SEARCH RESULTS]" label that
    #    survived (e.g. glued to text with no trailing structure), so the
    #    raw label can never reach the user.
    cleaned = re.sub(r'\[SEARCH RESULTS[^\]]*\]\s*', '', cleaned,
                     flags=re.IGNORECASE)
    cleaned = cleaned.strip()
    # If stripping left nothing usable (the whole reply WAS the leak),
    # fall back to a safe, in-character line rather than sending blank.
    if not cleaned:
        return "hmm socho toh, kuch aur pucho na 🙄"
    return cleaned


# ── Resilient send helpers ──────────────────────────────────────────────────────
#
# Telegram throws a BadRequest ("can't parse entities") if the HTML we
# hand it is even slightly malformed (e.g. an unclosed <b>, or a stray
# &/< that slipped past escaping). The original code called
# msg.reply_html(...) directly, so ANY such error bubbled up into the
# handler's bare `except` and the user got absolute silence — looking
# exactly like "Iota stopped replying". These wrappers always deliver
# the message: HTML first, then plain text as a guaranteed fallback.

async def _safe_send(msg, text: str):
    """Send Iota's reply, resilient to Telegram entity-parse errors.
    Tries HTML (for bold/italic) first; if Telegram rejects the markup,
    falls back to plain text so the user always gets the message."""
    try:
        return await msg.reply_html(text)
    except Exception as e:
        logger.debug(f"_safe_send HTML failed, falling back to plain: {e}")
        try:
            return await msg.reply_text(text, parse_mode=None)
        except Exception as e2:
            logger.warning(f"_safe_send plain failed: {e2}")
            return None


async def _safe_edit(thinking, text: str):
    """Edit the 'thinking…' placeholder, falling back to plain text and
    then to a fresh plain send if editing fails for any reason."""
    try:
        return await thinking.edit_text(text, parse_mode="HTML")
    except Exception:
        try:
            return await thinking.edit_text(text)
        except Exception:
            try:
                return await thinking.chat.send_message(text)
            except Exception as e:
                logger.warning(f"_safe_edit failed: {e}")
                return None


# ── Mood-based GIF-with-reply ─────────────────────────────────────────────────
#
# 🆕 FEATURE: a real girl texting doesn't just send words — she'll drop a
# GIF alongside a reply sometimes, matching her actual mood in that
# moment. This detects a mood from IOTA'S OWN reply text (not the user's
# message — her reply is what should decide whether e.g. a laughing GIF
# or a shy GIF fits) and, some of the time, sends a matching GIF right
# after her text. Deliberately NOT on every message — that's what made
# a previous version feel repetitive/spammy (always the same "hi" wave
# GIF) — and deliberately mood-VARIED, sourced live from GIPHY, so it's
# never the same fallback GIF every time.

_REPLY_MOOD_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r'(😂|🤣|hahaha+|lmao|lol\b)', re.I), "laugh"),
    (re.compile(r'(😭|😢|💔|😔|aw+\b|sorry)', re.I), "sad"),
    (re.compile(r'(😍|🥰|💕|💗|💞|cutie|pyaar|aww+)', re.I), "love"),
    (re.compile(r'(😡|🤬|😤|gussa|chup|badtameez)', re.I), "angry"),
    (re.compile(r'(🥳|🎉|yay+|lets goo|slay|badhai)', re.I), "happy"),
    (re.compile(r'(😱|😲|kya+\?|wait what|sach mein)', re.I), "surprise"),
    (re.compile(r'(💅|😎|okay bestie|king|queen)', re.I), "cool"),
    (re.compile(r'(🥺|so cute|awww)', re.I), "cute"),
]

# Chance that a reply is accompanied by a GIF at all — kept well under
# 50% so it still feels like an occasional, deliberate choice rather
# than mechanical spam on every single message.
_REPLY_GIF_PROBABILITY = 0.25


def _detect_reply_mood(reply_text: str) -> str | None:
    """Look at Iota's own reply text and return a mood if one clearly
    fits, else None (meaning: don't force a GIF for a neutral reply)."""
    for pattern, mood in _REPLY_MOOD_RULES:
        if pattern.search(reply_text):
            return mood
    return None


async def _maybe_send_reply_gif(msg, reply_text: str):
    """
    Probabilistically sends a mood-matched GIF right after Iota's text
    reply. Never raises — a failed/skipped GIF never affects the actual
    conversation, since the real text reply was already sent before
    this is even called.
    """
    try:
        if random.random() > _REPLY_GIF_PROBABILITY:
            return
        mood = _detect_reply_mood(reply_text)
        if not mood:
            return
        gif_url = await get_gif_for_mood(mood)
        if gif_url:
            await msg.reply_animation(gif_url)
    except Exception as e:
        logger.debug(f"_maybe_send_reply_gif failed: {e}")


def _is_asking_about_other(text: str) -> bool:
    lower = text.lower()
    triggers = ["uska", "unka", "us user", "is user", "uski", "unki",
                "kya kiya tha wo", "iska naam", "iski history",
                "tell me about", "what did they", "uske baare"]
    personal = ["memory", "history", "personal", "private", "bola tha",
                "likha tha", "details", "info", "kya karta"]
    return any(t in lower for t in triggers) and any(p in lower for p in personal)


# ── Intelligent search decision ───────────────────────────────────────────────
# No keyword triggers — AI itself decides. We use a fast heuristic to decide
# whether to even attempt search. The AI can always use search results or
# ignore them if irrelevant.

# Explicit "go search the web" intent — user literally tells Iota to look
# something up. This ALWAYS searches (and, if it fails, is the only case
# that should surface a "couldn't check right now" line, because the user
# actually asked for a lookup).
_EXPLICIT_SEARCH_RE = re.compile(
    r'\b('
    r'search|google|look ?up|find out|browse|web par|internet par|'
    r'dhoond|dhundh|pata kar|pata karo|pata lagao|check kar|check karo|'
    r'khoj|khojo|search kar|google kar'
    r')\b',
    re.IGNORECASE,
)

# Strong "current / real-world fact" intent — these genuinely need live
# data even if the user didn't say the word "search". Kept deliberately
# TIGHT so ordinary conversation ("tu kaise hai", "kaisa raha din") never
# matches — that was the old bug where Iota searched on every message.
_CURRENT_INTENT_RES = [
    # time-sensitive / news-y words
    re.compile(
        r'\b(latest|breaking|news|headline|update|updates|released?|'
        r'release date|trailer|scorecard|live score|score|standings|'
        r'price|prices|stock price|share price|exchange rate|'
        r'weather|forecast|temperature|aaj ?ka mausam|'
        r'202[4-9]|203[0-9])\b',
        re.IGNORECASE,
    ),
    # factual "who/what/when is <proper noun>" — require a capitalised name
    # OR a clearly factual keyword so "what is up" / "kya hai yaar" don't hit
    re.compile(
        r'\b(who is|who was|what is|when is|when was|where is|'
        r'net ?worth|full form|capital of|population of|founder of|'
        r'ceo of|owner of|meaning of|definition of|ka matlab|ki net ?worth|'
        r'kaun hai|kaun tha|kiski|kiska)\b',
        re.IGNORECASE,
    ),
    # entertainment / sports / markets — real entities that need fresh data
    re.compile(
        r'\b(box office|imdb|rotten tomatoes|ipl|world cup|'
        r'bitcoin|ethereum|crypto price|stock market|sensex|nifty|'
        r'wikipedia|wiki)\b',
        re.IGNORECASE,
    ),
]

# Casual / self-referential / emotional talk that NEVER needs the web.
# Checked first so it can veto a weak intent match.
_NEVER_SEARCH_RES = [
    re.compile(r'^\s*\d[\d\s\+\-\*\/\(\)\.]*$'),   # pure math
    re.compile(
        r'^\s*(hi+|hello+|hey+|heyy?|yo|bye|ok(ay)?|hmm+|thx|thanks|ty|'
        r'gn|gm|good (night|morning|evening|afternoon)|nice|cool|great|'
        r'wow|arre|acha|achha|theek|thik|sahi|done|kk)\b',
        re.IGNORECASE,
    ),
    re.compile(r'^(lol|lmao|haha+|rofl|xd|hehe+|heh)\b', re.IGNORECASE),
    # asking ABOUT Iota herself / her creator — she knows this, no web
    re.compile(
        r'\b(apna naam|tera naam|your name|tum kaun|tu kaun|who are you|'
        r'kaun ho|mere owner|your owner|kisne banaya|who made you|'
        r'banaya kisne|kisne banayi)\b',
        re.IGNORECASE,
    ),
    # banter / insults / affection directed at the bot — pure conversation
    re.compile(
        r'\b(battamiz|badtameez|pagal|bewakoof|stupid|dumb|idiot|shut ?up|'
        r'chup|bakwas|gadha|nalayak|i love you|love you|miss you|so sweet|'
        r'cutie|jaan|baby)\b',
        re.IGNORECASE,
    ),
    # "how are you" style small talk (English + Hinglish) — NOT a lookup
    re.compile(
        r'\b(kaise ho|kaisi ho|kaise hai(n)?|how are you|how r u|'
        r'kya kar rah[ie]|kya chal raha|whats up|what\'?s up|sup|'
        r'kaisa raha|kaisa chal)\b',
        re.IGNORECASE,
    ),
    # date/time/day — already injected into the system prompt
    re.compile(
        r'(aaj|kal|abhi|today|now).*(din|date|time|tareek|samay|day)|'
        r'what.*(day|date|time).*(today|now)|(konsa|kaunsa) din',
        re.IGNORECASE,
    ),
]


def _search_reason(text: str) -> str:
    """
    Decide IF and WHY Iota should hit the web for this message.

    Returns one of:
      • "explicit" — user literally asked her to search/find/look up. Always
        searches, and a failed lookup may surface a graceful "couldn't
        check" line (the user expected a lookup).
      • "current"  — message clearly needs fresh/real-world facts. Searches,
        but a failure stays silent (Iota just answers naturally) so casual
        chat that grazed a keyword never shows an error line.
      • ""         — no web needed (casual chat, banter, identity, math,
        emotions, greetings). Iota answers from her own persona/knowledge.

    This is the fix for "Iota har baat pe search karti hai": ordinary
    conversation returns "" and never triggers a lookup.
    """
    t = (text or "").lower().strip()
    if not t:
        return ""

    # 1. Explicit user request always wins — even inside a longer sentence.
    if _EXPLICIT_SEARCH_RE.search(t):
        return "explicit"

    # 2. Never-search categories veto everything below (casual/self/emotional).
    for pat in _NEVER_SEARCH_RES:
        if pat.search(t):
            return ""

    # 3. Strong current-info intent → search (failure stays silent).
    for pat in _CURRENT_INTENT_RES:
        if pat.search(t):
            return "current"

    # 4. Everything else → no search. Iota chats normally. (No blanket
    #    "long message = search" rule anymore — that caused the spam.)
    return ""


def _should_attempt_search(text: str) -> bool:
    """Back-compat boolean wrapper around _search_reason()."""
    return bool(_search_reason(text))


async def _respond(uid: int, text: str, is_premium: bool,
                   is_group=False, chat_title="", max_tokens=100,
                   first_name: str = "", username: str = "") -> str:
    # Token budget: short history + compact system + small completion.
    # Free tiers (Groq etc.) die when every turn ships a 4k+ system prompt
    # and 12–20 full history turns.
    partner_id = await get_partner_id(uid)
    hist = await get_memory(uid, limit=6, shared_with=partner_id)
    # Clip current user turn too
    user_text = (text or "").strip()
    if len(user_text) > 400:
        user_text = user_text[:399] + "…"
    hist.append({"role": "user", "content": user_text})

    ctx = f"\n[Group: {chat_title[:40]} — public info only]" if is_group else ""
    if partner_id:
        ctx += "\n[Connected pair — shared history; answer as one thread.]"
    who = f"\n[Talking to: {first_name or 'user'}"
    who += f" @{username}" if username else ""
    who += "]"
    ctx += who

    system = _build_system() + ctx
    messages = [{"role": "system", "content": system}] + hist

    try:
        reply = await call_ai(
            messages,
            is_premium=is_premium,
            max_tokens=max(48, min(int(max_tokens or 100), 160)),
            temperature=0.45,
            max_history=6,
        )
    except Exception as e:
        logger.warning(f"call_ai failed in _respond: {e}")
        reply = None

    if reply:
        reply = _strip_search_leak(reply)

    if not reply or not reply.strip():
        reply = "thodi der baad try karo na 🥺"

    reply = _md_to_html(reply)

    await save_memory(uid, "user", user_text, shared_with=partner_id)
    await save_memory(uid, "assistant", reply, shared_with=partner_id)
    return reply


# ── /ai command ───────────────────────────────────────────────────────────────

@ratelimit("ai", limit=10, window=60)
async def ai_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; msg = update.effective_message
    chat_obj = update.effective_chat
    try:
        await ensure_user(u.id, u.username or "", u.full_name)
        await update_last_seen(u.id, u.username or "", u.full_name)
        d = await get_user(u.id)
    except Exception as e:
        logger.warning(f"ai_cmd DB ops failed (continuing): {e}")
        d = {}

    if context.args:
        user_text = " ".join(context.args)
    elif msg.reply_to_message and msg.reply_to_message.text:
        user_text = msg.reply_to_message.text
    else:
        await msg.reply_html("🤖 Usage: /ai &lt;kuch bhi poocho&gt;\nDM me bas message bhejo! 💕")
        return

    if _is_asking_about_other(user_text):
        await msg.reply_html("kyu tujhe uski personal details? nahi bataungi 🙄"); return

    thinking = await msg.reply_html("💭 soch rahi hoon...")
    is_premium = bool((d or {}).get("is_premium", False))
    try:
        is_group = chat_obj.type != "private"
        async with chat_action(context.bot, msg.chat_id, ACTION_TYPING,
                               message_thread_id=getattr(msg, "message_thread_id", None)):
            reply = await _respond(u.id, user_text, is_premium,
                                   is_group, chat_obj.title or "",
                                   first_name=u.first_name or "", username=u.username or "")
        await _safe_edit(thinking, reply)
        await _maybe_send_reply_gif(msg, reply)
    except Exception as e:
        # 🔴 FINAL SAFETY NET: never leave the user staring at "soch rahi hoon…"
        logger.warning(f"ai_cmd failed: {e}")
        try:
            await _safe_edit(thinking, "arre system thoda gussa hai 😤 baad mein try karo?")
        except Exception:
            pass


# ── DM auto-reply ─────────────────────────────────────────────────────────────

_EMOJI_ONLY_STRIP_RE = re.compile(
    r'[\s'
    r'\U0001F300-\U0001FAFF'
    r'\U0001F600-\U0001F64F'
    r'\U0001F680-\U0001F6FF'
    r'\U0001F1E0-\U0001F1FF'
    r'\u2600-\u26FF'
    r'\u2700-\u27BF'
    r'\uFE00-\uFE0F'
    r'\u200d'
    r'\u20E3'
    r']+'
)


def _is_emoji_only(text: str) -> bool:
    """True if `text` is made up entirely of emoji/whitespace (no real
    words). Used so dm_message_handler steps aside for pure-emoji DMs
    and lets handlers.sticker_reply.emoji_only_handler reply instead —
    otherwise BOTH fired on the same message (one AI text reply + one
    GIF reply), giving the user two replies for a single emoji."""
    stripped = _EMOJI_ONLY_STRIP_RE.sub('', text)
    return not stripped and text.strip() != ""


async def dm_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto AI reply to ALL non-command DMs."""
    u = update.effective_user; msg = update.effective_message
    text = (msg.text or "").strip()
    if not text or text.startswith("/"): return
    from utils.ratelimit import ratelimit_allow
    if not await ratelimit_allow("ai_dm", u.id, limit=15, window=60):
        try:
            await update.effective_message.reply_html("⏳ thoda slow... ek minute baad try kar 💕")
        except Exception:
            pass
        return
    # While the user is privately composing a /whisper, their next message is
    # the whisper body — never send it to the AI model (privacy). The compose
    # handler pre-empts this one anyway, but this is a safety net.
    try:
        if (context.user_data.get(u.id) or {}).get("wsp_compose"):
            return
    except Exception:
        pass
    if _is_emoji_only(text):
        # 🔴 FIX: without this, a pure-emoji DM got a reply from BOTH
        # this handler AND handlers.sticker_reply.emoji_only_handler —
        # two separate bot messages for one emoji. That dedicated
        # handler (registered at a later priority) is the right one to
        # handle this case, so we step aside here.
        return
    try:
        from handlers.fun import _valentine_state
        _vst = _valentine_state.get(u.id)
        if _vst is not None:
            import time as _vt
            # 🔴 NEVER let a stuck/expired valentine form block the AI chat.
            # If the form is older than 15 min (or has no timestamp), clear
            # it and reply normally instead of silently eating the DM.
            if (_vt.time() - _vst.get("ts", 0)) > 900:
                _valentine_state.pop(u.id, None)
            else:
                return
    except Exception as e:
        logger.debug(f"valentine state check failed: {e}")
    # DB writes/reads must NEVER crash the reply path. If Mongo is having
    # a moment, fall back to a minimal user dict and still answer.
    try:
        await ensure_user(u.id, u.username or "", u.full_name)
        await update_last_seen(u.id, u.username or "", u.full_name)
        d = await get_user(u.id)
    except Exception as e:
        logger.warning(f"dm_message_handler DB ops failed (continuing): {e}")
        d = {}
    is_premium = bool(d.get("is_premium", False)) if isinstance(d, dict) else False

    # ── Spam block check (15-min mute from flood detection) ──────────────────
    try:
        from utils.mongo_db import get_spam_block, clear_spam_block
        import time as _time_check
        until = await get_spam_block(u.id)
        if until and _time_check.time() < until:
            remaining = int((until - _time_check.time()) / 60) + 1
            await msg.reply_html(
                f"⛔ Yᴏᴜ ʜᴀᴠᴇ ʙʟᴏᴄᴋᴇᴅ ꜰʀᴏᴍ ᴜsɪɴɢ Iᴏᴛᴀ ꜰᴏʀ "
                f"{remaining} ᴍɪɴᴜᴛᴇ(s) ᴅᴜᴇ ᴛᴏ sᴘᴀᴍᴍɪɴɢ. Pʟᴇᴀsᴇ sʟᴏᴡ ᴅᴏᴡɴ."
            )
            return
        elif until:
            await clear_spam_block(u.id)
    except Exception:
        pass
    if _is_asking_about_other(text):
        await msg.reply_html("kyu tujhe uski personal details? nahi bataungi 🙄"); return
    # 🔴 Show a live "typing…" indicator for the WHOLE duration (Telegram
    # actions expire after ~5s, so chat_action re-sends it every few seconds)
    # — the DM never looks dead while Iota searches + calls the AI.
    try:
        async with chat_action(context.bot, msg.chat_id, ACTION_TYPING):
            reply = await _respond(u.id, text, is_premium, False, "", 100,
                                    first_name=u.first_name or "", username=u.username or "")
        await _safe_send(msg, reply)
        await _maybe_send_reply_gif(msg, reply)
    except Exception as e:
        # 🔴 FINAL SAFETY NET: no matter what blew up, the user must still
        # see a message from Iota — never silent nothing.
        logger.warning(f"dm_message_handler failed: {e}")
        try:
            await _safe_send(msg, "arre system thoda gussa hai 😤 thodi der baad try karo?")
        except Exception:
            pass


# ── Smart group-reply detection ───────────────────────────────────────────────
#
# Iota replies in a GROUP only when:
#   1. She is explicitly @username-tagged anywhere in the message
#   2. The message is a reply to one of Iota's own previous messages
#   3. The message DIRECTLY addresses her by name at the very start
#      ("Iota ...", "iota kya scene hai", "hey iota ...",
#       "yaar iota bahut acchi hai", "kisi ne iota use kiya kya")
#
# Matches "iota" as a standalone WORD anywhere in the message (not just
# at the start) — this is the "say her name and she responds" behaviour
# — while still NOT matching it as a substring inside an unrelated word
# (e.g. "iotaphone" or "chiiota" would NOT trigger this, only the exact
# word "iota" with a word boundary on both sides does).

_DIRECT_ADDRESS_RE = re.compile(r'\biota\b', re.IGNORECASE)


def _is_reply_to_bot(update: Update, bot_id: int) -> bool:
    msg = update.effective_message
    if not msg or not msg.reply_to_message: return False
    ru = msg.reply_to_message.from_user
    return bool(ru and ru.id == bot_id)


def _is_tagged(text: str, bot_username: str) -> bool:
    return f"@{bot_username}".lower() in text.lower()


def _is_direct_address(text: str) -> bool:
    return bool(_DIRECT_ADDRESS_RE.match(text.strip()))


async def group_mention_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; msg = update.effective_message
    text = (msg.text or "").strip()
    if not text: return
    try:
        me = await context.bot.get_me()
        bot_username = me.username or ""; bot_id = me.id
    except Exception as e:
        logger.debug(f"group_mention_handler get_me: {e}"); return

    tagged      = _is_tagged(text, bot_username)
    replied_to  = _is_reply_to_bot(update, bot_id)
    direct_addr = _is_direct_address(text)

    if not (tagged or replied_to or direct_addr): return

    clean = text
    if tagged:
        clean = re.sub(re.escape(f"@{bot_username}"), "", clean, flags=re.IGNORECASE).strip()
    if direct_addr:
        clean = _DIRECT_ADDRESS_RE.sub("", text, count=1).strip()

    if not clean:
        await msg.reply_html("kuch poocha? bol na cutie 🥺"); return

    try:
        await ensure_user(u.id, u.username or "", u.full_name)
        await update_last_seen(u.id, u.username or "", u.full_name)
        d = await get_user(u.id)
    except Exception as e:
        logger.warning(f"group_mention_handler DB ops failed (continuing): {e}")
        d = {}
    is_premium = bool((d or {}).get("is_premium", False))

    if _is_asking_about_other(clean):
        await msg.reply_html("kyu tujhe uski personal details? group me sirf public info share hoti 🙄"); return

    try:
        # 🔴 Same live "typing…" indicator in GROUPS as in DMs, so the group
        # sees "Iota is typing…" while she searches + thinks.
        async with chat_action(context.bot, msg.chat_id, ACTION_TYPING,
                               message_thread_id=getattr(msg, "message_thread_id", None)):
            reply = await _respond(u.id, clean, is_premium,
                                   True, update.effective_chat.title or "", 100,
                                   first_name=u.first_name or "", username=u.username or "")
        await _safe_send(msg, reply)
        await _maybe_send_reply_gif(msg, reply)
    except Exception as e:
        logger.warning(f"group_mention_handler AI failed: {e}")
        # 🔴 FINAL SAFETY NET: never leave the group with total silence.
        try:
            await _safe_send(msg, "arre system thoda gussa hai 😤 baad mein try karo?")
        except Exception:
            pass


async def clear_my_memory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await clear_memory(u.id)
    await update.message.reply_html("🗑️ Teri saari memory delete kar di!\nAb fresh start 💕")
