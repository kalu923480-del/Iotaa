"""
Iota — safe callback_data codec (Telegram 64-byte guard).

Callback button payloads are hard-capped at 64 bytes by Telegram. Several
handlers build `callback_data` from ids/usernames and will silently BREAK
(the button does nothing) the moment a payload gets long. This module
encodes structured data as a compact, URL-safe base64 token under a short
prefix, and refuses to build anything over 64 bytes.

Usage:
    from utils.callback_codec import encode_callback, decode_callback

    data = encode_callback("wsp", {"w": wid})   # -> "wsp:<base64>"
    # pattern in bot.py:  r"^wsp:"
    payload = decode_callback(query.data, "wsp") # -> {"w": wid} or None
"""
import json
import base64

MAX_CALLBACK_BYTES = 64


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(s: str) -> bytes:
    pad = (-len(s)) % 4
    return base64.urlsafe_b64decode(s + ("=" * pad))


def encode_callback(prefix: str, data: dict) -> str:
    """Encode `data` into `prefix:<b64>` and assert it fits in 64 bytes."""
    if not prefix or ":" in prefix:
        raise ValueError("callback prefix must be non-empty and colon-free")
    token = prefix + ":" + _b64encode(
        json.dumps(data, separators=(",", ":")).encode("utf-8")
    )
    if len(token) > MAX_CALLBACK_BYTES:
        raise ValueError(
            f"callback_data too long ({len(token)} > {MAX_CALLBACK_BYTES} bytes)"
        )
    return token


def decode_callback(data, prefix: str):
    """Decode a callback token, or return None if it doesn't match `prefix`."""
    if not data or not isinstance(data, str) or not data.startswith(prefix + ":"):
        return None
    raw = data[len(prefix) + 1:]
    try:
        return json.loads(_b64decode(raw))
    except Exception:
        return None


def safe_cb(prefix: str, data: dict, fallback: str = "gh_home") -> str:
    """Encode, but never return something over 64 bytes — fall back to a
    short static token so the button always works."""
    try:
        return encode_callback(prefix, data)
    except ValueError:
        return fallback


_installed = False


def install_callback_guard():
    """Globally enforce Telegram's 64-byte callback_data limit.

    Monkeypatches telegram.InlineKeyboardButton so ANY handler in the bot
    that builds a too-long callback_data is caught at build time (logged,
    never raised) instead of shipping a dead button. Call once at startup.
    """
    global _installed
    if _installed:
        return
    try:
        from telegram import InlineKeyboardButton
        import logging
        _orig = InlineKeyboardButton.__init__

        def _guarded(self, text=None, callback_data=None, *a, **kw):
            if isinstance(callback_data, str) and len(callback_data) > MAX_CALLBACK_BYTES:
                logging.getLogger(__name__).warning(
                    f"⚠️ callback_data {len(callback_data)}B > 64B limit: "
                    f"{callback_data[:40]}… (button will be dead on Telegram)"
                )
            return _orig(self, text, callback_data, *a, **kw)

        InlineKeyboardButton.__init__ = _guarded
        _installed = True
    except Exception as e:
        logging.getLogger(__name__).debug(f"callback guard install skipped: {e}")
