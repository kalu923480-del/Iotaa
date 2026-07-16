"""
Iota Bot — Quote store (MongoDB)

Persistence layer for the upgraded /q system (ported from quote-bot's
design into iota's Python/Mongo stack). Handles per-user display settings
(default theme, emoji brand, privacy mode, rating toggle) and a personal
quote archive that powers /qtop (top-rated) and /qrand archive features.

All functions are async and Never raise on a missing DB — they degrade
gracefully so the quote command keeps working even if Mongo is down.
"""
import logging
import uuid

from utils.mongo_db import get_db

logger = logging.getLogger(__name__)

_SETTINGS = "quote_settings"
_QUOTES = "quote_archive"


async def get_settings(uid: int) -> dict:
    """Return a user's quote display settings (with safe defaults)."""
    default = {"theme": "dark", "emoji": None, "privacy": False, "rate": False}
    try:
        doc = await get_db()[_SETTINGS].find_one({"_id": uid})
        if doc:
            default.update({k: doc.get(k, default[k]) for k in default})
    except Exception as e:
        logger.debug(f"quote get_settings failed: {e}")
    return default


async def set_setting(uid: int, **kw):
    try:
        await get_db()[_SETTINGS].update_one(
            {"_id": uid}, {"$set": kw}, upsert=True
        )
    except Exception as e:
        logger.debug(f"quote set_setting failed: {e}")


async def save_quote(uid: int, chat_id: int, name: str, text: str,
                     theme: str) -> str:
    """Persist a generated quote and return its id (used for ratings/top)."""
    qid = uuid.uuid4().hex[:10]
    try:
        await get_db()[_QUOTES].insert_one({
            "_id": qid, "uid": uid, "chat_id": chat_id,
            "name": name, "text": text[:1000], "theme": theme,
            "up": 0, "down": 0, "ts": __import__("time").time(),
        })
    except Exception as e:
        logger.debug(f"quote save_quote failed: {e}")
    return qid


async def get_quote(qid: str) -> dict | None:
    try:
        return await get_db()[_QUOTES].find_one({"_id": qid})
    except Exception as e:
        logger.debug(f"quote get_quote failed: {e}")
        return None


async def rate_quote(qid: str, up: bool) -> dict:
    """Apply a +1 up or +1 down vote. Returns the new {up, down, score}."""
    field = "up" if up else "down"
    try:
        await get_db()[_QUOTES].update_one(
            {"_id": qid}, {"$inc": {field: 1}}
        )
    except Exception as e:
        logger.debug(f"quote rate_quote failed: {e}")
    doc = await get_quote(qid)
    if not doc:
        return {"up": 1 if up else 0, "down": 0 if up else 1, "score": 1 if up else -1}
    up_n = doc.get("up", 0)
    down_n = doc.get("down", 0)
    return {"up": up_n, "down": down_n, "score": up_n - down_n}


async def top_quotes(chat_id: int, limit: int = 10) -> list:
    """Top-rated quotes for a chat (score desc)."""
    try:
        cur = get_db()[_QUOTES].find(
            {"chat_id": chat_id}
        ).sort([("up", -1), ("ts", -1)]).limit(limit)
        return await cur.to_list(length=limit)
    except Exception as e:
        logger.debug(f"quote top_quotes failed: {e}")
        return []


async def forget_quote(uid: int, qid: str) -> bool:
    try:
        res = await get_db()[_QUOTES].delete_one({"_id": qid, "uid": uid})
        return res.deleted_count > 0
    except Exception as e:
        logger.debug(f"quote forget_quote failed: {e}")
        return False
