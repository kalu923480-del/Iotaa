"""Iota Bot — Central XP engine

All XP gains flow through add_xp() so level-up coin rewards fire exactly
once per level and the user's level field is always in sync.
"""
import logging
from utils.mongo_db import get_db, get_user, update_user, add_balance
from utils.helpers import xp_level
try:
    from config import XP_PER_LEVEL, LEVEL_UP_COIN_BASE
except Exception:
    XP_PER_LEVEL = 1000
    LEVEL_UP_COIN_BASE = 200

logger = logging.getLogger(__name__)


def xp_for_level(level: int) -> int:
    """Total XP needed to REACH this level (level 1 = 0).

    Uses the same progressive curve as helpers.xp_level:
        cost for level N = N * XP_PER_LEVEL
    """
    if level <= 1:
        return 0
    return sum(n * XP_PER_LEVEL for n in range(1, level))


def xp_progress(xp: int) -> tuple:
    """Return (level, xp_into_current_level, xp_needed_for_next)."""
    lv = xp_level(xp)
    into = xp - xp_for_level(lv)
    needed = xp_for_level(lv + 1) - xp
    return lv, into, needed


async def add_xp(uid: int, amount: int, reason: str = "") -> dict:
    """Atomically add XP, recompute level, pay level-up coin rewards.

    Returns {"xp", "level", "old_level", "leveled_up": bool,
             "levels_gained": int, "reward": int}

    Never raises — returns zeros on any failure.
    """
    try:
        if amount <= 0:
            return {"xp": 0, "level": 1, "old_level": 1,
                    "leveled_up": False, "levels_gained": 0, "reward": 0}
        try:
            from utils.events import event_multiplier
            amount = int(amount * await event_multiplier("xp", 1.0))
        except Exception:
            pass
        d = await get_user(uid)
        current_xp = int(d.get("xp", 0) or 0)
        # Recompute level from XP (source of truth) so a stale user.level
        # field can never block level-up rewards or under-count gains.
        old_level = xp_level(current_xp)
        new_xp = current_xp + amount
        new_level = xp_level(new_xp)
        levels_gained = max(0, new_level - old_level)

        reward = 0
        if levels_gained > 0:
            for lvl in range(old_level + 1, new_level + 1):
                reward += LEVEL_UP_COIN_BASE * lvl

        await update_user(uid, xp=new_xp, level=new_level)

        if reward > 0:
            try:
                await add_balance(uid, reward)
            except Exception:
                logger.debug("add_xp: add_balance failed for %s", uid)

        try:
            from utils.clans import contribute_xp
            await contribute_xp(uid, amount)
        except Exception:
            pass

        return {
            "xp": new_xp,
            "level": new_level,
            "old_level": old_level,
            "leveled_up": levels_gained > 0,
            "levels_gained": levels_gained,
            "reward": reward,
        }
    except Exception as e:
        logger.debug("add_xp best-effort fail for %s: %s", uid, e)
        return {"xp": 0, "level": 1, "old_level": 1,
                "leveled_up": False, "levels_gained": 0, "reward": 0}


async def get_top_xp(n: int = 10) -> list:
    """Top users by xp from users collection."""
    try:
        cursor = get_db().users.find({"is_banned": {"$ne": True}}).sort("xp", -1).limit(n)
        return await cursor.to_list(n)
    except Exception:
        try:
            return await get_db().users.find({"is_banned": {"$ne": True}}).sort(
                [("xp", -1)]
            ).limit(n).to_list(n)
        except Exception:
            return []


async def sync_level(uid: int) -> int:
    """Recompute level from xp and write to user.level. Return level."""
    try:
        d = await get_user(uid)
        xp = int(d.get("xp", 0) or 0)
        lv = xp_level(xp)
        await update_user(uid, level=lv)
        return lv
    except Exception:
        return 1
