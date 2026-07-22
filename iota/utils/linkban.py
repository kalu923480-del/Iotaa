"""
Iota — Linkban system (block foreign invite / spam links).

Detects Telegram invite links and generic URLs, with per-group:
  • enable/disable
  • action mode: delete | mute | warn
  • whitelist (domains, @usernames, full URLs)
  • optional allow of *this* group's own public username / invite

Pure helpers are unit-testable without Telegram API.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# Telegram invite / deep-link patterns (case-insensitive)
_INVITE_RE = re.compile(
    r"(?:"
    r"(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/"
    r"(?:joinchat/|\+)([A-Za-z0-9_-]+)"           # private invite
    r"|(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/"
    r"([A-Za-z][A-Za-z0-9_]{3,})"                 # public @username path
    r"|(?:https?://)?(?:t\.me|telegram\.me)/c/\d+"  # private channel id links
    r")",
    re.IGNORECASE,
)

# Any http(s) URL (for optional "block all links" mode — not default)
_ANY_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

# Bare t.me without scheme
_TME_BARE_RE = re.compile(
    r"(?<![\w/])(?:t\.me|telegram\.me|telegram\.dog)/[^\s<>\"']+",
    re.IGNORECASE,
)

DEFAULT_LINKBAN = {
    "linkban_enabled": False,
    "linkban_mode": "delete",       # delete | mute | warn
    "linkban_mute_secs": 300,       # 5 min default mute
    "linkban_allow_own": True,      # allow this chat's public username
    "linkban_block_urls": False,    # if True, also block any http(s) URL
    "link_allowlist": [],           # strings: domain, @user, substring
}


def normalize_allow_entry(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    s = s.lower()
    # strip schemes
    for pref in ("https://", "http://"):
        if s.startswith(pref):
            s = s[len(pref):]
    s = s.lstrip("@")
    # drop path noise for domains
    if "/" in s and not s.startswith("t.me/") and "t.me/" not in s:
        s = s.split("/")[0]
    return s.strip()


def extract_link_hits(text: str) -> list[dict]:
    """Return list of {kind, value, match} found in text."""
    if not text:
        return []
    hits = []
    seen = set()

    for m in _INVITE_RE.finditer(text):
        full = m.group(0)
        key = full.lower()
        if key in seen:
            continue
        seen.add(key)
        if m.group(1):  # private invite hash
            hits.append({"kind": "invite", "value": m.group(1).lower(), "match": full})
        elif m.group(2):  # public username
            hits.append({"kind": "username", "value": m.group(2).lower(), "match": full})
        else:
            hits.append({"kind": "invite", "value": full.lower(), "match": full})

    for m in _TME_BARE_RE.finditer(text):
        full = m.group(0)
        key = full.lower()
        if key in seen:
            continue
        seen.add(key)
        # parse username if t.me/name
        path = full.split("/", 1)[-1]
        if path.startswith("+") or path.lower().startswith("joinchat"):
            hits.append({"kind": "invite", "value": path.lower(), "match": full})
        else:
            uname = path.split("?")[0].split("/")[0].lower()
            if uname:
                hits.append({"kind": "username", "value": uname, "match": full})

    for m in _ANY_URL_RE.finditer(text):
        full = m.group(0)
        key = full.lower()
        if key in seen:
            continue
        # skip pure telegram links already captured
        if re.search(r"(?:t\.me|telegram\.me|telegram\.dog)/", full, re.I):
            continue
        seen.add(key)
        try:
            host = urlparse(full).netloc.lower().lstrip("www.")
        except Exception:
            host = full.lower()
        hits.append({"kind": "url", "value": host, "match": full})

    return hits


def is_whitelisted(hit: dict, allowlist: list, own_username: str = "") -> bool:
    """True if this hit should be allowed."""
    val = (hit.get("value") or "").lower()
    match = (hit.get("match") or "").lower()
    own = (own_username or "").lower().lstrip("@")

    if own and hit.get("kind") == "username" and val == own:
        return True

    for entry in allowlist or []:
        e = normalize_allow_entry(entry)
        if not e:
            continue
        if e in val or e in match or val in e or match.endswith(e):
            return True
        # domain host match
        if hit.get("kind") == "url" and (val == e or val.endswith("." + e)):
            return True
        if hit.get("kind") == "username" and e == val:
            return True
    return False


def should_block_links(
    text: str,
    *,
    enabled: bool,
    allowlist: list | None = None,
    own_username: str = "",
    allow_own: bool = True,
    block_urls: bool = False,
) -> tuple[bool, list[dict]]:
    """
    Returns (blocked, blocked_hits).
    - Telegram invite links always considered when enabled.
    - Public t.me/username blocked unless own/whitelist.
    - Generic URLs only if block_urls=True.
    """
    if not enabled or not text:
        return False, []

    allow = list(allowlist or [])
    hits = extract_link_hits(text)
    blocked = []
    for h in hits:
        if h["kind"] == "url" and not block_urls:
            continue
        own = own_username if allow_own else ""
        if is_whitelisted(h, allow, own_username=own):
            continue
        blocked.append(h)
    return (len(blocked) > 0), blocked


def merge_linkban_settings(prot_or_gs: dict) -> dict:
    """Overlay DEFAULT_LINKBAN with stored fields from prot/group_settings."""
    out = dict(DEFAULT_LINKBAN)
    if not prot_or_gs:
        return out
    for k in DEFAULT_LINKBAN:
        if k in prot_or_gs and prot_or_gs[k] is not None:
            out[k] = prot_or_gs[k]
    # legacy: if only anti_link True and no linkban fields, don't auto-enable linkban
    return out
