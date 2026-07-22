"""Iota Bot — Clans DB layer."""
import logging
import re
import time

from utils.mongo_db import get_db, get_user, update_user, deduct_balance
from utils.helpers import fmt

logger = logging.getLogger(__name__)

try:
    from config import (
        CLAN_CREATE_COST, CLAN_MAX_MEMBERS,
        CLAN_NAME_MIN, CLAN_NAME_MAX, CLAN_TAG_MIN, CLAN_TAG_MAX,
    )
except Exception:
    CLAN_CREATE_COST = 5_000
    CLAN_MAX_MEMBERS = 20
    CLAN_NAME_MIN = 3
    CLAN_NAME_MAX = 24
    CLAN_TAG_MIN = 2
    CLAN_TAG_MAX = 5


def _now() -> int:
    return int(time.time())


def _clan_coll():
    return get_db().clans


async def create_clan(owner_id: int, name: str, tag: str) -> tuple:
    """Create a new clan. Returns (ok, message, doc_or_none)."""
    try:
        name = name.strip()
        tag = tag.strip().upper()
        if not (CLAN_NAME_MIN <= len(name) <= CLAN_NAME_MAX):
            return False, f"Name must be {CLAN_NAME_MIN}-{CLAN_NAME_MAX} chars.", None
        if not re.fullmatch(r"[A-Za-z0-9 ]+", name):
            return False, "Name must be alphanumeric + spaces only.", None
        if not (CLAN_TAG_MIN <= len(tag) <= CLAN_TAG_MAX):
            return False, f"Tag must be {CLAN_TAG_MIN}-{CLAN_TAG_MAX} chars.", None
        if not re.fullmatch(r"[A-Z0-9]+", tag):
            return False, "Tag must be alphanumeric uppercase only.", None

        owner = await get_user(owner_id)
        if owner.get("balance", 0) < CLAN_CREATE_COST:
            return False, f"Need {CLAN_CREATE_COST} coins to create a clan!", None
        existing = await get_user_clan(owner_id)
        if existing:
            return False, "You are already in a clan! Leave first.", None

        coll = _clan_coll()
        exists = await coll.find_one({"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}})
        if exists:
            return False, "A clan with that name already exists.", None
        exists_tag = await coll.find_one({"tag": tag})
        if exists_tag:
            return False, "A clan with that tag already exists.", None

        await deduct_balance(owner_id, CLAN_CREATE_COST)
        cid = f"clan_{owner_id}_{_now()}"
        doc = {
            "_id": cid,
            "name": name,
            "tag": tag,
            "owner_id": owner_id,
            "members": {
                str(owner_id): {"role": "owner", "joined": _now(), "contributed": 0}
            },
            "bank": 0,
            "total_xp": 0,
            "level": 1,
            "desc": "",
            "created_at": _now(),
            "invite_only": False,
        }
        await coll.insert_one(doc)
        await update_user(owner_id, clan_id=cid)
        return True, f"🏰 Clan '{name}' [{tag}] created!", doc
    except Exception as e:
        logger.debug("create_clan failed: %s", e)
        return False, "Failed to create clan.", None


async def get_clan(clan_id: str) -> dict | None:
    try:
        return await _clan_coll().find_one({"_id": clan_id})
    except Exception:
        return None


async def get_user_clan(uid: int) -> dict | None:
    try:
        u = await get_user(uid)
        cid = u.get("clan_id") or ""
        if not cid:
            return None
        return await get_clan(cid)
    except Exception:
        return None


async def join_clan(uid: int, clan_id: str) -> tuple:
    """Join a clan by id. Returns (ok, message)."""
    try:
        u = await get_user(uid)
        if u.get("clan_id"):
            return False, "You are already in a clan! /clanleave first."
        clan = await get_clan(clan_id)
        if not clan:
            return False, "Clan not found."
        members = clan.get("members", {})
        if len(members) >= CLAN_MAX_MEMBERS:
            return False, "Clan is full!"
        if str(uid) in members:
            return False, "You are already a member."
        members[str(uid)] = {"role": "member", "joined": _now(), "contributed": 0}
        await _clan_coll().update_one({"_id": clan_id}, {"$set": {"members": members}})
        await update_user(uid, clan_id=clan_id)
        return True, f"✅ Joined {clan['name']} [{clan['tag']}]!"
    except Exception as e:
        logger.debug("join_clan failed: %s", e)
        return False, "Failed to join clan."


async def leave_clan(uid: int) -> tuple:
    """Leave current clan. Owner must transfer or disband first."""
    try:
        clan = await get_user_clan(uid)
        if not clan:
            return False, "You are not in a clan."
        if clan.get("owner_id") == uid:
            return False, "You are the owner! /clantransfer or /clandisband first."
        members = dict(clan.get("members", {}))
        members.pop(str(uid), None)
        await _clan_coll().update_one({"_id": clan["_id"]}, {"$set": {"members": members}})
        await update_user(uid, clan_id="")
        return True, f"Left {clan['name']}."
    except Exception as e:
        logger.debug("leave_clan failed: %s", e)
        return False, "Failed to leave clan."


async def kick_member(actor_id: int, target_uid: int) -> tuple:
    """Owner or officer can kick members. Only owner can kick officers."""
    try:
        clan = await get_user_clan(actor_id)
        if not clan:
            return False, "You are not in a clan."
        members = dict(clan.get("members", {}))
        actor_role = members.get(str(actor_id), {}).get("role")
        if actor_role not in ("owner", "officer"):
            return False, "Only the owner or an officer can kick."
        target_role = members.get(str(target_uid), {}).get("role")
        if not target_role:
            return False, "User is not in your clan."
        if target_role == "owner":
            return False, "Can't kick the owner."
        if target_role == "officer" and actor_role != "owner":
            return False, "Only the owner can kick officers."
        if target_uid == actor_id:
            return False, "You can't kick yourself — use /clanleave."
        members.pop(str(target_uid), None)
        await _clan_coll().update_one({"_id": clan["_id"]}, {"$set": {"members": members}})
        await update_user(target_uid, clan_id="")
        return True, "Member kicked."
    except Exception as e:
        logger.debug("kick_member failed: %s", e)
        return False, "Failed to kick member."


async def disband_clan(owner_id: int) -> tuple:
    try:
        clan = await get_user_clan(owner_id)
        if not clan:
            return False, "You are not in a clan."
        if clan.get("owner_id") != owner_id:
            return False, "Only the owner can disband."
        cid = clan["_id"]
        for uid_s in list(clan.get("members", {}).keys()):
            try:
                await update_user(int(uid_s), clan_id="")
            except Exception:
                pass
        await _clan_coll().delete_one({"_id": cid})
        return True, f"🏰 Clan '{clan['name']}' disbanded."
    except Exception as e:
        logger.debug("disband_clan failed: %s", e)
        return False, "Failed to disband clan."


async def deposit_to_clan(uid: int, amount: int) -> tuple:
    try:
        if amount <= 0:
            return False, "Amount must be positive."
        clan = await get_user_clan(uid)
        if not clan:
            return False, "You are not in a clan."
        user = await get_user(uid)
        if user.get("balance", 0) < amount:
            return False, "Not enough balance."
        await deduct_balance(uid, amount)
        await _clan_coll().update_one(
            {"_id": clan["_id"]}, {"$inc": {"bank": amount}}
        )
        return True, f"💰 Deposited {amount} to clan bank."
    except Exception as e:
        logger.debug("deposit_to_clan failed: %s", e)
        return False, "Failed to deposit."


async def top_clans(n: int = 10) -> list:
    try:
        return await _clan_coll().find({}).sort("total_xp", -1).limit(n).to_list(n)
    except Exception:
        try:
            return await _clan_coll().find({}).sort([("total_xp", -1)]).limit(n).to_list(n)
        except Exception:
            return []


async def contribute_xp(uid: int, amount: int) -> None:
    """Add XP to the user's clan total_xp (best-effort)."""
    try:
        if amount <= 0:
            return
        clan = await get_user_clan(uid)
        if not clan:
            return
        await _clan_coll().update_one(
            {"_id": clan["_id"]}, {"$inc": {"total_xp": amount}}
        )
    except Exception:
        pass


async def set_clan_desc(owner_id: int, desc: str) -> tuple:
    try:
        clan = await get_user_clan(owner_id)
        if not clan:
            return False, "You are not in a clan."
        if clan.get("owner_id") != owner_id:
            return False, "Only the owner can set the description."
        await _clan_coll().update_one(
            {"_id": clan["_id"]}, {"$set": {"desc": desc[:500]}}
        )
        return True, "Clan description updated."
    except Exception as e:
        logger.debug("set_clan_desc failed: %s", e)
        return False, "Failed to set description."


async def transfer_ownership(owner_id: int, new_owner_id: int) -> tuple:
    try:
        clan = await get_user_clan(owner_id)
        if not clan:
            return False, "You are not in a clan."
        if clan.get("owner_id") != owner_id:
            return False, "Only the owner can transfer."
        members = dict(clan.get("members", {}))
        if str(new_owner_id) not in members:
            return False, "Target user is not in your clan."
        members[str(owner_id)] = dict(members.get(str(owner_id), {}))
        members[str(owner_id)]["role"] = "officer"
        members[str(new_owner_id)] = dict(members.get(str(new_owner_id), {}))
        members[str(new_owner_id)]["role"] = "owner"
        await _clan_coll().update_one(
            {"_id": clan["_id"]},
            {"$set": {"owner_id": new_owner_id, "members": members}}
        )
        return True, f"Ownership transferred to user {new_owner_id}."
    except Exception as e:
        logger.debug("transfer_ownership failed: %s", e)
        return False, "Failed to transfer ownership."


async def clan_info_text(clan: dict) -> str:
    try:
        members = clan.get("members", {})
        member_count = len(members)
        lines = [
            f"🏰 <b>{clan.get('name', '?')}</b> [{clan.get('tag', '?')}]\n",
            f"👑 Owner: <b>{clan.get('owner_id', '?')}</b>\n",
            f"👥 Members: {member_count}/{CLAN_MAX_MEMBERS}\n",
            f"⚡ Total XP: {clan.get('total_xp', 0)}\n",
            f"🏦 Bank: {fmt(clan.get('bank', 0))}\n",
            f"📝 {clan.get('desc', '') or 'No description.'}",
        ]
        return "\n".join(lines)
    except Exception:
        return "Clan info unavailable."


async def get_clan_by_name_or_tag(query: str) -> dict | None:
    try:
        q = query.strip()
        coll = _clan_coll()
        tag_match = await coll.find_one({"tag": q.upper()})
        if tag_match:
            return tag_match
        return await coll.find_one(
            {"name": {"$regex": f"^{re.escape(q)}$", "$options": "i"}}
        )
    except Exception:
        return None
