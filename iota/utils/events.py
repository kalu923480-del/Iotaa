"""Iota Bot — Global timed events (XP boost, double daily, lucky rob, game fever)."""
import logging
import time
from utils.mongo_db import get_db, now

logger = logging.getLogger(__name__)

EVENT_TYPES = {
    "double_daily": {
        "name": "Double Daily",
        "emoji": "🎁",
        "desc": "Daily rewards x2",
        "multiplier": 2.0,
        "field": "daily",
    },
    "xp_boost": {
        "name": "XP Boost",
        "emoji": "⚡",
        "desc": "XP gains x2",
        "multiplier": 2.0,
        "field": "xp",
    },
    "lucky_rob": {
        "name": "Lucky Rob",
        "emoji": "🦹",
        "desc": "Rob tax halved",
        "multiplier": 0.5,
        "field": "rob_tax",
    },
    "game_fever": {
        "name": "Game Fever",
        "emoji": "🎮",
        "desc": "Game win XP bonus",
        "multiplier": 2.0,
        "field": "game_xp",
    },
}


async def get_active_events() -> list:
    """Return list of active global event docs (lazy-expires stale ones)."""
    try:
        await cleanup_expired_events()
        ts = now()
        return await get_db().global_events.find({
            "active": True,
            "ends": {"$gt": ts},
        }).to_list(50)
    except Exception:
        return []


async def start_event(event_key: str, duration_hours: float, started_by: int) -> tuple:
    """Start a global event. Returns (ok, message, doc_or_none)."""
    try:
        if event_key not in EVENT_TYPES:
            return False, f"Unknown event type: {event_key}", None
        ts = now()
        ends = ts + max(1, int(float(duration_hours) * 3600))
        doc = {
            "_id": event_key,
            "active": True,
            "starts": ts,
            "ends": ends,
            "started_by": started_by,
            "meta": {},
        }
        await get_db().global_events.update_one(
            {"_id": event_key}, {"$set": doc}, upsert=True
        )
        info = EVENT_TYPES[event_key]
        return True, f"{info['emoji']} {info['name']} started for {duration_hours}h!", doc
    except Exception as e:
        return False, f"Failed to start event: {e}", None


async def stop_event(event_key: str) -> tuple:
    """Stop a global event. Returns (ok, message)."""
    try:
        r = await get_db().global_events.update_one(
            {"_id": event_key}, {"$set": {"active": False, "ends": now()}}
        )
        if getattr(r, "matched_count", 0) == 0:
            return False, f"Event {event_key} not found."
        info = EVENT_TYPES.get(event_key, {})
        name = info.get("name", event_key)
        return True, f"⛔ {name} stopped."
    except Exception as e:
        return False, f"Failed to stop event: {e}"


async def stop_all_events():
    """Stop every active event (used on shutdown)."""
    try:
        await get_db().global_events.update_many(
            {"active": True}, {"$set": {"active": False, "ends": now()}}
        )
    except Exception:
        pass


async def event_multiplier(field: str, default=1.0) -> float:
    """Return the combined active multiplier for `field`, or default.

    Multipliers are multiplied together so boosts (e.g. 2.0 XP) and
    reductions (e.g. 0.5 rob tax) both work correctly — previously only
    `max()` was used, which silently ignored Lucky Rob (0.5).
    """
    try:
        events = await get_active_events()
        result = 1.0
        found = False
        for ev in events:
            key = ev.get("_id")
            info = EVENT_TYPES.get(key)
            if info and info.get("field") == field:
                result *= float(info.get("multiplier", 1.0))
                found = True
        return result if found else default
    except Exception:
        return default


async def cleanup_expired_events():
    """Mark expired events as inactive."""
    try:
        ts = now()
        await get_db().global_events.update_many(
            {"active": True, "ends": {"$lte": ts}},
            {"$set": {"active": False}}
        )
    except Exception:
        pass
