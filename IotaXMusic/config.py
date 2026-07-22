"""
IotaXMusic — runtime config loaded from .env

This file was missing from the tree; the app imports `config` everywhere.
Values come from environment / IotaXMusic/.env via python-dotenv.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_a, **_k):
        return False

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / ".env")


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def _env_int(key: str, default: int = 0) -> int:
    raw = _env(key, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    raw = _env(key, "1" if default else "0").lower()
    return raw in ("1", "true", "yes", "on")


# ── Core Telegram ──────────────────────────────────────────────────────────
API_ID = _env_int("API_ID", 0)
API_HASH = _env("API_HASH")
BOT_TOKEN = _env("BOT_TOKEN")
OWNER_ID = _env_int("OWNER_ID", 0)
LOGGER_ID = _env_int("LOGGER_ID", 0)

# Assistant session(s) — music VC needs at least one
STRING_SESSION = _env("STRING_SESSION") or _env("STRING1")
STRING1 = STRING_SESSION
STRING2 = _env("STRING2")
STRING3 = _env("STRING3")
STRING4 = _env("STRING4")
STRING5 = _env("STRING5")

# ── Database ───────────────────────────────────────────────────────────────
MONGO_DB_URI = _env("MONGO_DB_URI") or _env("MONGO_URI")

# ── Optional integrations ──────────────────────────────────────────────────
COOKIE_URL = _env("COOKIE_URL")
DEEP_API = _env("DEEP_API")
API_KEY = _env("API_KEY")
API_URL = _env("API_URL")
VIDEO_API_URL = _env("VIDEO_API_URL")

HEROKU_API_KEY = _env("HEROKU_API_KEY")
HEROKU_APP_NAME = _env("HEROKU_APP_NAME")

SPOTIFY_CLIENT_ID = _env("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = _env("SPOTIFY_CLIENT_SECRET")

# AI chat (optional)
AI_PROVIDER = _env("AI_PROVIDER", "openai")
AI_API_KEY = _env("AI_API_KEY")
AI_API_URL = _env("AI_API_URL", "https://api.openai.com/v1/chat/completions")
AI_MODEL = _env("AI_MODEL", "gpt-4o-mini")
AI_MAX_TOKENS = _env_int("AI_MAX_TOKENS", 1024)
AI_SYSTEM_PROMPT = _env(
    "AI_SYSTEM_PROMPT",
    "You are Iota, a helpful, friendly AI assistant inside the Iota Music Bot.",
)

# ── Limits / feature flags (sane defaults for VPS) ─────────────────────────
DURATION_LIMIT_MIN = _env_int("DURATION_LIMIT", 60)
DURATION_LIMIT = DURATION_LIMIT_MIN * 60
PLAYLIST_FETCH_LIMIT = _env_int("PLAYLIST_FETCH_LIMIT", 25)
TG_AUDIO_FILESIZE_LIMIT = _env_int("TG_AUDIO_FILESIZE_LIMIT", 104857600)  # 100MB
TG_VIDEO_FILESIZE_LIMIT = _env_int("TG_VIDEO_FILESIZE_LIMIT", 1073741824)  # 1GB
AUTO_LEAVING_ASSISTANT = _env_bool("AUTO_LEAVING_ASSISTANT", True)
AUTO_LEAVE_ASSISTANT_TIME = _env_int("AUTO_LEAVE_ASSISTANT_TIME", 540)

# ── Upstream / git ─────────────────────────────────────────────────────────
UPSTREAM_REPO = _env("UPSTREAM_REPO", "https://github.com/Iota/IotaXMusic")
UPSTREAM_BRANCH = _env("UPSTREAM_BRANCH", "main")
GIT_TOKEN = _env("GIT_TOKEN")

# ── Support links / assets ─────────────────────────────────────────────────
SUPPORT_CHAT = _env("SUPPORT_CHAT", "https://t.me/samvadacha_chat")
SUPPORT_CHANNEL = _env("SUPPORT_CHANNEL", "https://t.me/IotaUpdates")

# Local branded assets (never use random YouTube/telegra placeholders)
from pathlib import Path as _Path

_ASSETS = _Path(__file__).resolve().parent / "IotaXMedia" / "assets"
_LOCAL_UPIC = str(_ASSETS / "upic.png")
_LOCAL_TINY = str(_ASSETS / "tiny.png")
_DEFAULT_MUSIC_IMG = _LOCAL_UPIC if (_ASSETS / "upic.png").is_file() else _LOCAL_TINY

YOUTUBE_IMG_URL = _env("YOUTUBE_IMG_URL", _DEFAULT_MUSIC_IMG)
STREAM_IMG_URL = _env("STREAM_IMG_URL", _DEFAULT_MUSIC_IMG)
SOUNCLOUD_IMG_URL = _env("SOUNCLOUD_IMG_URL", _DEFAULT_MUSIC_IMG)
TELEGRAM_AUDIO_URL = _env("TELEGRAM_AUDIO_URL", _DEFAULT_MUSIC_IMG)
TELEGRAM_VIDEO_URL = _env("TELEGRAM_VIDEO_URL", _DEFAULT_MUSIC_IMG)
PLAYLIST_IMG_URL = _env("PLAYLIST_IMG_URL", _DEFAULT_MUSIC_IMG)
SPOTIFY_PLAYLIST_IMG_URL = PLAYLIST_IMG_URL
SPOTIFY_ALBUM_IMG_URL = PLAYLIST_IMG_URL
SPOTIFY_ARTIST_IMG_URL = PLAYLIST_IMG_URL
STATS_VID_URL = _env("STATS_VID_URL", _DEFAULT_MUSIC_IMG)


def youtube_thumb(videoid: str, quality: str = "hqdefault") -> str:
    """Direct YouTube thumbnail URL for a video id."""
    vid = (videoid or "").strip()
    if not vid or len(vid) != 11:
        return YOUTUBE_IMG_URL
    return f"https://i.ytimg.com/vi/{vid}/{quality}.jpg"

# Runtime sets
BANNED_USERS = set()
adminlist = {}
lyrical = {}
votemode = {}
autoclean = []
confirmer = {}

# Extra optional names some plugins expect
POST_GROUP_ID = _env_int("POST_GROUP_ID", 0)
PRIVATE_BOT_MODE = _env_bool("PRIVATE_BOT_MODE", False)
# Comma-separated chat IDs the assistant must never auto-leave
_PROTECTED_RAW = _env("PROTECTED_CHAT_IDS", "")
PROTECTED_CHAT_IDS = {
    int(x.strip())
    for x in _PROTECTED_RAW.split(",")
    if x.strip().lstrip("-").isdigit()
}

# Symbols imported via `from config import X` in various plugins
BOT_USERNAME = _env("BOT_USERNAME", "Iotamusicbot")
HELP_IMG_URL = _env("HELP_IMG_URL", _DEFAULT_MUSIC_IMG)
# Comma-separated sticker file_ids; empty list = skip stickers on /start
_STICKERS_RAW = _env("STICKERS", "")
STICKERS = [s.strip() for s in _STICKERS_RAW.split(",") if s.strip()]
DEBUG_IGNORE_LOG = _env_bool("DEBUG_IGNORE_LOG", False)
# Loading messages for /play (text only — never image URLs)
_DEFAULT_AYU = (
    "🔎 sᴇᴀʀᴄʜɪɴɢ...\n\nᴘʟᴇᴀsᴇ ᴡᴀɪᴛ"
    "||"
    "⏳ ᴘʀᴏᴄᴇssɪɴɢ ʏᴏᴜʀ ǫᴜᴇʀʏ...\n\nᴀʟᴍᴏsᴛ ᴛʜᴇʀᴇ"
    "||"
    "🎵 ғɪɴᴅɪɴɢ ᴛʜᴇ ʙᴇsᴛ ᴍᴀᴛᴄʜ...\n\nᴘʟᴇᴀsᴇ ᴡᴀɪᴛ"
)
_AYU_RAW = _env("AYU", _DEFAULT_AYU)
AYU = [
    s.strip()
    for s in _AYU_RAW.split("||")
    if s.strip() and not s.strip().startswith("http")
] or [
    "🔎 sᴇᴀʀᴄʜɪɴɢ...\n\nᴘʟᴇᴀsᴇ ᴡᴀɪᴛ",
    "⏳ ᴘʀᴏᴄᴇssɪɴɢ ʏᴏᴜʀ ǫᴜᴇʀʏ...\n\nᴀʟᴍᴏsᴛ ᴛʜᴇʀᴇ",
    "🎵 ғɪɴᴅɪɴɢ ᴛʜᴇ ʙᴇsᴛ ᴍᴀᴛᴄʜ...\n\nᴘʟᴇᴀsᴇ ᴡᴀɪᴛ",
]
_DEFAULT_START_CAPTION = (
    "✦ ʜᴇʏ {0},\n\n"
    "✦ ɪ'ᴍ {1}\n\n"
    "✦ ᴜᴘᴛɪᴍᴇ: {2}\n"
    "✦ ᴅɪsᴋ: {3}% | ᴄᴘᴜ: {4}% | ʀᴀᴍ: {5}%\n"
    "✦ ᴜsᴇʀs: {6} | ᴄʜᴀᴛs: {7}"
)
_AYUV_RAW = _env("AYUV", "")
AYUV = [s.strip() for s in _AYUV_RAW.split("||") if s.strip()] or [_DEFAULT_START_CAPTION]


def time_to_seconds(time_str) -> int:
    """Convert 'mm:ss' / 'hh:mm:ss' / int-seconds to total seconds."""
    if time_str is None:
        return 0
    if isinstance(time_str, (int, float)):
        return int(time_str)
    s = str(time_str).strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    parts = s.split(":")
    try:
        parts = [int(float(p)) for p in parts]
    except ValueError:
        return 0
    if len(parts) == 3:
        h, m, sec = parts
        return h * 3600 + m * 60 + sec
    if len(parts) == 2:
        m, sec = parts
        return m * 60 + sec
    return parts[0] if parts else 0


def _validate() -> None:
    missing = []
    if not API_ID:
        missing.append("API_ID")
    if not API_HASH:
        missing.append("API_HASH")
    if not BOT_TOKEN or ":" not in BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not MONGO_DB_URI or "<db_password>" in MONGO_DB_URI:
        missing.append("MONGO_DB_URI (real password required)")
    if not OWNER_ID:
        missing.append("OWNER_ID")
    if missing:
        raise RuntimeError(
            "IotaXMusic config incomplete. Set these in IotaXMusic/.env: "
            + ", ".join(missing)
        )


# Soft validate only when not generating docs/tests
if os.getenv("IOTA_SKIP_CONFIG_VALIDATE") != "1":
    try:
        _validate()
    except RuntimeError as e:
        # Defer hard crash until import-time only if critical; print clear message
        import sys
        print(f"[config] WARNING: {e}", file=sys.stderr)
