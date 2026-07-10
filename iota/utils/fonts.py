"""
Iota Font System - Iota-style smallcaps/unicode fonts
"""

import re

# Tags Telegram's Bot API HTML parser actually supports. Anything else that
# looks like "<foo>" (e.g. a usage placeholder "<amount>") is an UNKNOWN tag
# and makes Telegram reject the whole message with "unsupported start tag".
_ALLOWED_TAGS = (
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
    "code", "pre", "a", "tg-spoiler", "blockquote", "span",
)
_ALLOWED_TAG_RE = re.compile(
    r"</?(?:" + "|".join(sorted(_ALLOWED_TAGS, key=len, reverse=True)) + r")"
    r"(?:\s[^>]*)?>"
)


def _escape_unsupported_tags(text: str) -> str:
    """
    Escape any '<...>' that is NOT one of Telegram's allowed HTML tags, so
    innocent usage placeholders like "<amount>" / "<model>" render as literal
    text instead of crashing reply_html() with a BadRequest.

    Allowed tags (and their attributes, e.g. <a href="...">) are preserved
    verbatim so bold/code/links keep working. Idempotent: already-escaped
    entities (&lt; &gt;) contain no raw '<' so they pass straight through.
    """
    if "<" not in text:
        return text
    out = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "<":
            end = text.find(">", i)
            if end == -1:
                out.append("&lt;")
                i += 1
                continue
            tag = text[i:end + 1]
            if _ALLOWED_TAG_RE.fullmatch(tag):
                out.append(tag)                 # real Telegram tag — keep it
            else:
                out.append("&lt;" + tag[1:-1].replace("<", "&lt;") + "&gt;")
            i = end + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


# Smallcaps alphabet (Iota style)
_SC = {
    'a':'ᴀ','b':'ʙ','c':'ᴄ','d':'ᴅ','e':'ᴇ','f':'ꜰ','g':'ɢ','h':'ʜ',
    'i':'ɪ','j':'ᴊ','k':'ᴋ','l':'ʟ','m':'ᴍ','n':'ɴ','o':'ᴏ','p':'ᴘ',
    'q':'ǫ','r':'ʀ','s':'ꜱ','t':'ᴛ','u':'ᴜ','v':'ᴠ','w':'ᴡ','x':'x',
    'y':'ʏ','z':'ᴢ'
}

# Anything that must NOT be transliterated when styling a whole message:
#   - HTML tags:            <b>, </code>, <a href="...">, etc.
#   - URLs:                 https://..., t.me/..., tg://...
#   - HTML entities:        &amp; &#123; &#x1F600;
# These are captured (kept verbatim) so small-caps conversion only touches
# the human-visible text and never breaks markup, links, or entities.
_PROTECT_RE = re.compile(
    r'(<[^>]+>'
    r'|https?://\S+'
    r'|t\.me/\S+'
    r'|tg://\S+'
    r'|&(?:[a-zA-Z]+|#\d+|#x[0-9a-fA-F]+);)'
)

# Tokens that must NEVER be small-caps styled — they are literal and
# user/code-facing:
#   - Telegram commands:  /start, /addapikey, /ludo ...
#   - Versioned AI model names: gpt-4o, llama-3.1, claude-3, gemini-1.5 ...
#   - URLs: https://..., t.me/..., tg://... (so links stay clickable)
# (a leading ":" is excluded so https:// URLs are never touched)
_PROTECT_OUT = re.compile(
    r"(?<!:)"
    r"(/[A-Za-z_][A-Za-z0-9_]*"
    r"|https?://\S+"
    r"|t\.me/\S+"
    r"|tg://\S+"
    r"|gpt[-0-9][\w.\-]*"
    r"|llama[-0-9][\w.\-]*"
    r"|claude[-0-9][\w.\-]*"
    r"|gemini[-0-9][\w.\-]*"
    r"|mixtral[-0-9][\w.\-]*"
    r"|mistral[-0-9][\w.\-]*"
    r"|qwen[-0-9][\w.\-]*"
    r"|deepseek[-0-9][\w.\-]*"
    r"|gemma[-0-9][\w.\-]*"
    r"|phi[-0-9][\w.\-]*"
    r"|grok[-0-9][\w.\-]*"
    r"|command[-0-9][\w.\-]*)",
    re.IGNORECASE,
)


def sc(text: str) -> str:
    """
    Small-caps converter for Iota's BRANDED OUTPUT only (headers, labels,
    status badges, menus, game boards — the spots the handlers already
    call this at). NOT for whole replies, /command names, or model names.

      - every LOWERCASE letter (a-z) -> small-caps unicode (ᴀʙᴄᴅ)
      - every UPPERCASE letter (A-Z) -> stays a normal LARGE capital (A B C)
      - /commands and versioned model names are LEFT NORMAL on purpose
      - everything else (digits, punctuation, emoji, spaces, HTML, URLs,
        entities) passes through untouched.

    Per-character, so word shape/casing is preserved (e.g. "Balance" ->
    "ʙᴀʟᴀɴᴄᴇ", but "Usage: /addapikey" -> "ᴜꜱᴀɢᴇ: /addapikey").
    Idempotent: small-caps glyphs pass through unchanged.
    """
    if not isinstance(text, str):
        return text
    parts = _PROTECT_OUT.split(text)
    out = []
    for part in parts:
        if not part:
            continue
        if _PROTECT_OUT.fullmatch(part):
            out.append(part)          # command / model name — keep literal
        else:
            out.append("".join(_SC.get(c, c) for c in part))
    return _escape_unsupported_tags("".join(out))

def bold_sc(text: str) -> str:
    """Smallcaps wrapped in HTML bold."""
    return f"<b>{sc(text)}</b>"

def header(text: str) -> str:
    """Bold smallcaps header (lowercase -> small caps, uppercase -> large)."""
    return f"<b>{sc(text)}</b>"


def sc_all(text: str) -> str:
    """
    Convert a FULL message to Iota-style smallcaps while leaving markup,
    links and HTML entities completely untouched. Use this for whole
    bot outputs (it is what the global output wrapper applies).

    - Splits the text on any protected token (HTML tag / URL / entity).
    - Applies the existing `sc()` (first-letter-cap, rest-smallcaps)
      style ONLY to the unprotected, human-visible text between them.
    - Idempotent: already-smallcaps text passes through unchanged, so a
      message that was pre-styled with `sc()` won't double-transform.
    - Length-preserving (each ASCII letter maps to one smallcaps glyph),
      so any MessageEntity offsets in the original remain valid.
    """
    if not isinstance(text, str):
        return text
    parts = _PROTECT_RE.split(text)
    out = []
    for part in parts:
        if not part:
            continue
        if (part.startswith("<") and part.endswith(">")) \
           or _PROTECT_RE.fullmatch(part):
            out.append(part)          # tag / url / entity — keep verbatim
        else:
            out.append(sc(part))      # visible text — style it
    return _escape_unsupported_tags("".join(out))


# Alias used by the global outbound wrapper.
sc_out = sc_all

# Preset styled texts
PROTECTED    = "🛡️ " + sc("You Are Now Protected")
ALREADY_PROT = "🛡️ " + sc("You Are Already Protected")
DEAD_STATUS  = "💀 " + sc("Dead")
ALIVE_STATUS = "✅ " + sc("Alive")
BALANCE_HDR  = "💰 " + sc("Balance")
RANK_HDR     = "🏆 " + sc("Global Rank")
KILLS_HDR    = "⚔️ " + sc("Kills")
STATUS_HDR   = "🛡️ " + sc("Status")
NAME_HDR     = "👤 " + sc("Name")
LEVEL_HDR    = "🟤 " + sc("Level")
REMAINING    = "⏳ " + sc("Remaining")
ALERT        = "⚠️  " + sc("Alert!")
