"""
Iota AI Memory System
- Per-user private memory (30 day auto-delete)
- Group context (only public info: names/usernames)
- Privacy: no cross-user personal data sharing

Token hygiene:
  • Small default history window
  • Truncate long message bodies on save/load
  • No delete_many on every get_memory (was burning DB + slowing every AI turn)
"""
import time
from utils.mongo_db import get_db

MEMORY_TTL_DAYS = 30
# Keep short — each turn multiplies input tokens on free-tier providers.
DEFAULT_MEMORY_LIMIT = 6
MAX_CONTENT_CHARS = 400


def _clip(content: str, max_chars: int = MAX_CONTENT_CHARS) -> str:
    if not content:
        return ""
    s = str(content).strip()
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1] + "…"


async def save_memory(uid: int, role: str, content: str, shared_with: int = None):
    """
    Save one message to memory. If `shared_with` is given (the partner's
    user id, from an active /connect), the message is tagged with a
    stable pair-key so BOTH users' get_memory() calls can see it.
    """
    db = get_db()
    doc = {
        "uid": uid,
        "role": role,
        "content": _clip(content),
        "ts": int(time.time()),
    }
    if shared_with is not None:
        doc["pair_key"] = _pair_key(uid, shared_with)
    await db.ai_memory.insert_one(doc)


def _pair_key(a: int, b: int) -> str:
    lo, hi = sorted([a, b])
    return f"{lo}:{hi}"


async def get_memory(uid: int, limit: int = DEFAULT_MEMORY_LIMIT, shared_with: int = None) -> list:
    """
    Get recent messages for this user (token-budget aware).

    If `shared_with` is given, merge shared pair history + private history.
    Does NOT purge old docs on every call (cleanup_old_memories handles that).
    """
    db = get_db()
    limit = max(2, min(int(limit or DEFAULT_MEMORY_LIMIT), 12))
    # Light TTL for this user's PRIVATE rows only (not shared pair docs).
    try:
        cutoff = int(time.time()) - (MEMORY_TTL_DAYS * 86400)
        await db.ai_memory.delete_many(
            {"uid": uid, "pair_key": {"$exists": False}, "ts": {"$lt": cutoff}}
        )
    except Exception:
        pass

    if shared_with is not None:
        pk = _pair_key(uid, shared_with)
        try:
            shared = await db.ai_memory.find({"pair_key": pk}).sort(
                "ts", -1
            ).limit(limit).to_list(limit)
            private = await db.ai_memory.find(
                {"uid": uid, "pair_key": {"$exists": False}}
            ).sort("ts", -1).limit(limit).to_list(limit)
        except Exception:
            shared, private = [], []
        docs = list(shared) + list(private)
        docs.sort(key=lambda d: d.get("ts", 0))
        docs = docs[-limit:]
        return [
            {"role": d["role"], "content": _clip(d.get("content", ""))}
            for d in docs
        ]

    try:
        docs = await db.ai_memory.find({"uid": uid}).sort(
            "ts", -1
        ).limit(limit).to_list(limit)
    except Exception:
        docs = []
    docs.reverse()
    return [
        {"role": d["role"], "content": _clip(d.get("content", ""))}
        for d in docs
    ]


async def clear_memory(uid: int):
    await get_db().ai_memory.delete_many({"uid": uid})


async def cleanup_old_memories():
    """Background job: delete memories older than 30 days."""
    cutoff = int(time.time()) - (MEMORY_TTL_DAYS * 86400)
    result = await get_db().ai_memory.delete_many({"ts": {"$lt": cutoff}})
    return result.deleted_count


async def get_group_member_names(chat_id: int, bot) -> str:
    """Get public info of group members for context (names only)."""
    try:
        admins = await bot.get_chat_administrators(chat_id)
        names = [a.user.first_name for a in admins if not a.user.is_bot]
        return ", ".join(names[:10]) if names else ""
    except Exception:
        return ""
