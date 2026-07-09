"""
Iota Font System - Iota-style smallcaps/unicode fonts
"""

import re

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

def sc(text: str) -> str:
    """
    Small-caps converter.

    Currently a PASS-THROUGH: small-caps styling is turned OFF so all bot
    responses, /command references and other user-facing text render as
    normal (plain) text. The _SC mapping is retained so this can be
    re-enabled later if desired — but for now callers get their text back
    unchanged, which keeps every output normal.
    """
    return text

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
    return "".join(out)


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
