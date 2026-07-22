"""Active-group session for DM group management."""
import time
from utils.mongo_db import get_db, now

_active: dict[int, int] = {}


async def set_active_group(uid: int, chat_id: int) -> None:
    _active[uid] = chat_id
    try:
        await get_db().group_sessions.update_one(
            {"_id": uid},
            {"$set": {"active_chat_id": chat_id, "updated_at": now()}},
            upsert=True,
        )
    except Exception:
        pass


async def get_active_group(uid: int) -> int | None:
    if uid in _active:
        return _active[uid]
    try:
        doc = await get_db().group_sessions.find_one({"_id": uid})
        if doc and doc.get("active_chat_id"):
            _active[uid] = int(doc["active_chat_id"])
            return _active[uid]
    except Exception:
        pass
    return None


async def clear_active_group(uid: int) -> None:
    _active.pop(uid, None)
    try:
        await get_db().group_sessions.delete_one({"_id": uid})
    except Exception:
        pass


async def is_user_group_admin(bot, chat_id: int, user_id: int) -> bool:
    from config import OWNER_ID
    if int(user_id) == int(OWNER_ID):
        return True
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        return m.status in ("administrator", "creator")
    except Exception:
        return False


async def list_candidate_groups_for_user(bot, user_id: int, limit: int = 40) -> list[dict]:
    from utils.mongo_db import get_db
    out = []
    try:
        docs = await get_db().group_settings.find(
            {"active": {"$ne": False}}
        ).sort("tracked_at", -1).to_list(200)
    except Exception:
        docs = []
    for d in docs:
        if len(out) >= limit:
            break
        cid = d.get("_id")
        if not isinstance(cid, (int, float)):
            try:
                cid = int(cid)
            except Exception:
                continue
        if not await is_user_group_admin(bot, cid, user_id):
            continue
        title = d.get("title") or f"Group {cid}"
        try:
            ch = await bot.get_chat(int(cid))
            title = ch.title or title
        except Exception:
            pass
        out.append({"id": int(cid), "title": title})
    return out
