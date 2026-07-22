"""Iota Bot - MongoDB Async Database Layer (motor)"""
import time, re, uuid
from motor.motor_asyncio import AsyncIOMotorClient
from config import (
    MONGO_URI, DB_NAME,
    BANK_DAILY_RATE, PREMIUM_BANKING_CAP, FD_BREAK_PENALTY,
    BANK_CUSTOMER_DAILY_RATE, BANK_OWNER_DEPOSIT_FEE, BANK_RATE_MIN,
    BANK_RATE_MAX,
)

_client = None
_db = None
_connection_ok = None   # None=unknown, True/False after first check


def get_db():
    """Return the database handle, creating the motor client lazily.

    Timeouts are set so a dead/unreachable MongoDB fails FAST and clearly
    (instead of hanging for minutes) and surfaces as a clean "database
    unavailable" error rather than a confusing generic crash. motor retries
    dropped connections automatically, so a brief outage self-heals.
    """
    global _client, _db
    if _db is None:
        _client = AsyncIOMotorClient(
            MONGO_URI,
            serverSelectionTimeoutMS=8000,   # give up selecting a node after 8s
            connectTimeoutMS=10000,          # TCP connect timeout
            socketTimeoutMS=20000,           # per-operation socket timeout
            retryWrites=True,
            maxPoolSize=50,
        )
        _db = _client[DB_NAME]
    return _db


async def check_connection() -> bool:
    """Ping MongoDB once at startup. Returns True if reachable."""
    global _connection_ok
    try:
        await get_db().command("ping")
        _connection_ok = True
        return True
    except Exception as e:
        _connection_ok = False
        import logging
        logging.getLogger(__name__).error(
            f"❌ MongoDB connection FAILED: {e}\n"
            f"👉 Fix this in the deploy env — set MONGO_URI (full Atlas "
            f"connection string) or MONGO_PASS. Also confirm the Atlas "
            f"cluster is running and its Network Access list allows "
            f"connections (e.g. 'Allow Access From Anywhere'). Until fixed, "
            f"/bal, /daily, /rob, /pay, /ludo and ALL other database-backed "
            f"commands will fail with a database error."
        )
        return False


def is_db_connected() -> bool:
    return _connection_ok is not False  # True or unknown = assume ok, only block on confirmed False


def now(): return int(time.time())

# ── Users ────────────────────────────────────────────────────────────

# Canonical default user document. Every field a handler may read
# directly (e.g. victim["protected_until"], user["balance"]) is present
# here so legacy documents — created by an older bot version before some
# fields existed — never raise KeyError at runtime. get_user/ensure_user
# always return a doc merged over these defaults.
DEFAULT_USER = {
    "username":"", "full_name":"",
    "balance":0, "gems":0, "is_premium":False, "premium_emoji":"",
    "last_daily":0, "kills":0, "daily_kills":0, "last_kill_reset":0,
    "robs":0, "daily_robs":0, "last_rob_reset":0, "xp":0, "level":1,
    "protected_until":0, "dead_until":0, "wallet":0, "is_banned":False,
    "free_gem_claimed":False, "custom_title":"",
    "name_history":[], "username_history":[],
    "clan_id":"",
}

async def ensure_user(uid, username="", full_name=""):
    db = get_db()
    u = await db.users.find_one({"_id": uid})
    if not u:
        u = dict(DEFAULT_USER)
        u["_id"] = uid
        u["username"] = username
        u["full_name"] = full_name
        u["created_at"] = now()
        # Seed history with the very first name/username seen, so a
        # user who never changes anything still has at least one
        # entry (matches full history depth shown by other bots
        # instead of staying empty forever).
        u["name_history"] = [full_name] if full_name else []
        u["username_history"] = [username] if username else []
        await db.users.insert_one(u)
    else:
        upd = {}
        if username and u.get("username") != username:
            upd["username"] = username
            hist = u.get("username_history", [])
            # 🔴 FIX: the OLD username (the one being replaced) was never
            # being recorded — only the brand-new one ever got appended,
            # so history could never grow past the single latest value.
            # Push the outgoing username into history (not the incoming
            # one) so a full change-log accumulates over time, matching
            # what /detail is supposed to show.
            old_username = u.get("username")
            if old_username and old_username not in hist:
                hist = [old_username] + hist[:9]
            upd["username_history"] = hist
        if full_name and u.get("full_name") != full_name:
            upd["full_name"] = full_name
            hist = u.get("name_history", [])
            old_full_name = u.get("full_name")
            if old_full_name and old_full_name not in hist:
                hist = [old_full_name] + hist[:9]
            upd["name_history"] = hist
        if upd:
            await db.users.update_one({"_id":uid},{"$set":upd})
            u.update(upd)
        # 🔴 FIX: legacy documents may be missing fields added later
        # (e.g. protected_until / dead_until). Merge over DEFAULT_USER so
        # direct dict access in handlers can never raise KeyError.
        merged = dict(DEFAULT_USER); merged.update(u); u = merged
    return u

async def get_user(uid):
    u = await get_db().users.find_one({"_id":uid})
    if not u:
        return await ensure_user(uid)
    # 🔴 FIX: fill any fields missing from legacy docs (see ensure_user).
    merged = dict(DEFAULT_USER); merged.update(u)
    return merged

async def get_user_by_username(username: str):
    """
    Look up a user document by their @username (case-insensitive,
    leading @ optional). This is the fallback used by admin commands
    (.mute @someone, .ban @someone, etc.) when Telegram's own
    bot.get_chat("@username") fails — which happens very often for
    usernames the bot hasn't cached yet, users with strict privacy
    settings, or plain API hiccups. Without this fallback, admin
    commands using a bare @username (instead of a reply) would
    silently fail with "Specify a user!" even though the user is real
    and has talked to the bot before.
    Returns the user dict (with "_id" = their Telegram user id) or None.
    """
    if not username:
        return None
    uname = username.lstrip("@")
    if not uname:
        return None
    return await get_db().users.find_one(
        {"username": {"$regex": f"^{re.escape(uname)}$", "$options": "i"}}
    )

async def update_user(uid, **kw):
    if kw: await get_db().users.update_one({"_id":uid},{"$set":kw})

async def add_balance(uid, amt):
    await get_db().users.update_one({"_id":uid},{"$inc":{"balance":amt}})

async def add_gems(uid, amt):
    await get_db().users.update_one({"_id":uid},{"$inc":{"gems":amt}})

async def deduct_gems(uid, amt):
    await get_db().users.update_one({"_id":uid},{"$inc":{"gems":-abs(amt)}})

async def get_top_rich(n=10):
    pipeline = [
        {"$match": {"is_banned": {"$ne": True}}},
        {"$addFields": {"tb": {"$add": [
            {"$ifNull": ["$balance", 0]}, {"$ifNull": ["$wallet", 0]}
        ]}}},
        {"$sort": {"tb": -1}},
        {"$limit": n},
    ]
    return await get_db().users.aggregate(pipeline).to_list(n)

async def get_top_kill(n=10):
    return await get_db().users.find(
        {"is_banned":{"$ne":True}},sort=[("kills",-1)],limit=n
    ).to_list(n)

async def get_user_rank(uid):
    u = await get_user(uid)
    if not u:
        return 0
    total = (u.get("balance", 0) or 0) + (u.get("wallet", 0) or 0)
    pipeline = [
        {"$match": {"is_banned": {"$ne": True}}},
        {"$addFields": {"tb": {"$add": [
            {"$ifNull": ["$balance", 0]}, {"$ifNull": ["$wallet", 0]}
        ]}}},
        {"$match": {"tb": {"$gt": total}}},
        {"$count": "c"},
    ]
    res = await get_db().users.aggregate(pipeline).to_list(1)
    c = res[0]["c"] if res else 0
    return c + 1


async def get_total_balance(uid) -> int:
    u = await get_user(uid)
    if not u:
        return 0
    return (u.get("balance", 0) or 0) + (u.get("wallet", 0) or 0)

async def total_users():
    return await get_db().users.count_documents({})

async def mark_user_unreachable(uid, reason="blocked"):
    """
    Records that a DM to this user failed permanently (they blocked the
    bot, deleted their account, etc.) so future broadcasts can skip them
    automatically instead of retrying — and failing on — the same dead
    users every single time, which is what was driving the broadcast
    failure rate so high.
    """
    await get_db().users.update_one(
        {"_id": uid},
        {"$set": {"dm_unreachable": True, "dm_unreachable_reason": reason, "dm_unreachable_at": now()}}
    )

async def mark_user_reachable(uid):
    """Clears the unreachable flag — called whenever we successfully
    message a user again (e.g. they unblocked the bot and used a command)."""
    await get_db().users.update_one(
        {"_id": uid},
        {"$unset": {"dm_unreachable": "", "dm_unreachable_reason": "", "dm_unreachable_at": ""}}
    )

async def get_broadcastable_users():
    """Users who haven't been marked unreachable — the actual DM-able audience.
    Projects full_name/username too so broadcast tagging ({mention}/{name})
    can be personalised without an extra per-user lookup."""
    return await get_db().users.find(
        {"dm_unreachable": {"$ne": True}},
        {"_id": 1, "full_name": 1, "username": 1},
    ).to_list(200000)

# ── Broadcast/Announce history + deletion ───────────────────────────────
#
# 🆕 WHY THIS EXISTS: previously a broadcast/announce, once sent, could
# never be recalled — if the owner made a typo or needed to retract
# something, there was no way to pull it back, and no record of what
# had even been sent before. This tracks every (broadcast_id -> list of
# {chat_id, message_id}) so the owner can delete a specific broadcast
# from everywhere it was sent, or from one specific chat, and can list
# a full history of past broadcasts.

async def create_broadcast_record(kind: str, content_preview: str, sender_id: int) -> str:
    """kind = 'broadcast' (DMs) or 'announce' (groups). Returns the new broadcast_id."""
    import uuid
    bid = str(uuid.uuid4())[:8]
    await get_db().broadcast_history.insert_one({
        "_id": bid, "kind": kind, "content_preview": content_preview[:200],
        "sender_id": sender_id, "created_at": now(),
        "targets": [],  # filled in via add_broadcast_target as sends succeed
    })
    return bid

async def add_broadcast_target(bid: str, chat_id: int, message_id: int):
    await get_db().broadcast_history.update_one(
        {"_id": bid},
        {"$push": {"targets": {"chat_id": chat_id, "message_id": message_id}}}
    )

async def get_broadcast_record(bid: str):
    return await get_db().broadcast_history.find_one({"_id": bid})

async def list_broadcast_history(limit: int = 20):
    return await get_db().broadcast_history.find(
        {}, sort=[("created_at", -1)], limit=limit
    ).to_list(limit)


async def get_all_groups():
    """Active group chat ids the bot is a member of (for group broadcasts/
    announces). Excludes groups the bot has left (active=False) so dead
    chats don't waste send attempts. Returns a list of {"_id": chat_id} dicts."""
    return await get_db().group_settings.find(
        {"active": {"$ne": False}}, {"_id": 1}
    ).to_list(100000)


# Canonical default document for a group. Centralised here so both the
# auto-tracking path (ensure_group_settings) and handlers/advanced_admin.py
# always agree on the field set — a mismatch would cause KeyErrors in the
# advanced-admin commands.
DEFAULT_GROUP_SETTINGS = {
    "lock_messages": False,
    "lock_media": False,
    "lock_stickers": False,
    "lock_gifs": False,
    "lock_links": False,
    "lock_polls": False,
    "lock_forwards": False,
    "lock_games": False,
    "flood_limit": 0,       # 0 = disabled
    "flood_action": "mute", # mute/ban/kick
    "rules": "",
    "rules_button": "",
    "warn_limit": 3,
    "warn_mode": "ban",     # ban/mute/kick
    "warn_time": 0,
    "goodbye_enabled": False,
    "goodbye_msg": "",
    "captcha_enabled": False,
    "captcha_time": 120,
    "log_channel": 0,
    "clean_service": False,
    "anti_channel_pin": False,
    "disabled_cmds": [],
    "silent_actions": False,
    "lang": "en",
    "tracked_at": 0,
    "active": True,
    "nightmode_enabled": False,
    "nightmode_start": "23:00",
    "nightmode_end": "07:00",
    "forcesub_channel": "",
    "welcome_buttons": [],
}


async def ensure_group_settings(cid: int, title: str = "", active: bool = True,
                                bot_is_admin: bool = None):
    """
    Make sure a chat has a complete group_settings doc so it shows up in
    group broadcasts and advanced-admin features work. Uses $setOnInsert so
    any admin customisations already saved are preserved (never overwritten).

    Called automatically whenever the bot joins ANY group (admin or not) via
    the my_chat_member handler in bot.py — previously groups were only
    created lazily when an admin ran an advanced-admin command, so
    non-admin groups were invisible to /broadcast and similar.

    `bot_is_admin` records whether the bot currently holds admin rights in
    the chat. This drives the welcome/anti-spam "needs admin" warnings and
    the group join onboarding message (a non-admin bot cannot receive
    new_chat_members service messages, so it literally cannot welcome new
    members until it is promoted).
    """
    doc = dict(DEFAULT_GROUP_SETTINGS)
    doc["tracked_at"] = now()
    doc["active"] = active
    if title:
        doc["title"] = title
    await get_db().group_settings.update_one(
        {"_id": cid}, {"$setOnInsert": doc}, upsert=True
    )
    # Keep the live admin-status flag in sync (separate $set so it never
    # clobbers the $setOnInsert defaults above). None = "unknown, don't touch".
    if bot_is_admin is not None:
        await get_db().group_settings.update_one(
            {"_id": cid}, {"$set": {"bot_is_admin": bool(bot_is_admin)}}
        )
    # Welcome defaults: enabled, text-only (GIF off).
    await get_db().welcome_settings.update_one(
        {"_id": cid},
        {"$setOnInsert": {
            "enabled": True,
            "custom_msg": "",
            "send_gif": False,
            "send_sticker": False,
            "welcome_buttons": [],
        }},
        upsert=True,
    )
    return await get_db().group_settings.find_one({"_id": cid})


async def get_group_settings(cid: int):
    """Return a group_settings doc merged over DEFAULT_GROUP_SETTINGS so
    legacy docs never miss new fields (nightmode, forcesub, etc.)."""
    doc = await get_db().group_settings.find_one({"_id": cid})
    if not doc:
        return None
    merged = dict(DEFAULT_GROUP_SETTINGS)
    merged.update(doc)
    return merged


async def is_bot_admin_in_group(cid: int) -> bool:
    """
    Best-effort check of whether the bot currently holds admin rights in a
    chat. Returns False if unknown / not tracked / any error (fail-closed so
    we never *assume* the bot is an admin when it isn't).
    """
    try:
        g = await get_group_settings(cid)
        if g and isinstance(g.get("bot_is_admin"), bool):
            return g["bot_is_admin"]
    except Exception:
        pass
    return False


async def set_group_inactive(cid: int):
    """Mark a group the bot has left so it stops receiving broadcasts."""
    await get_db().group_settings.update_one(
        {"_id": cid}, {"$set": {"active": False}}
    )


# ── Owner systems (new powerful owner-panel subsystems) ────────────────────
#
# All of these use schemaless MongoDB collections so adding a new subsystem
# never requires a migration. Every function is fail-soft: a DB error returns
# a safe default rather than raising into the owner command that called it.

# ── Owner override + sudo staff ──────────────────────────────────────────

async def get_owner_override() -> int:
    """If ownership was transferred at runtime, return the new owner id."""
    try:
        d = await get_db().bot_config.find_one({"_id": "owner_override"})
        if d and isinstance(d.get("uid"), int):
            return d["uid"]
    except Exception:
        pass
    return 0


async def set_owner_override(uid: int):
    await get_db().bot_config.update_one(
        {"_id": "owner_override"}, {"$set": {"uid": int(uid)}}, upsert=True
    )


async def list_sudo() -> list:
    try:
        d = await get_db().bot_config.find_one({"_id": "sudo_users"})
        if d and isinstance(d.get("uids"), list):
            return [int(x) for x in d["uids"]]
    except Exception:
        pass
    return []


async def add_sudo(uid: int):
    uids = set(await list_sudo()); uids.add(int(uid))
    await get_db().bot_config.update_one(
        {"_id": "sudo_users"}, {"$set": {"uids": list(uids)}}, upsert=True
    )


async def remove_sudo(uid: int):
    uids = set(await list_sudo()); uids.discard(int(uid))
    await get_db().bot_config.update_one(
        {"_id": "sudo_users"}, {"$set": {"uids": list(uids)}}, upsert=True
    )


async def is_sudo(uid: int) -> bool:
    return int(uid) in (await list_sudo())


# ── Global lockdown / shield state ──────────────────────────────────────

async def get_shield() -> dict:
    try:
        d = await get_db().bot_config.find_one({"_id": "shield"})
        if d:
            d.pop("_id", None); return d
    except Exception:
        pass
    return {"lockdown": False, "reason": "", "slowmode": 0, "media_locked": False}


async def set_shield(**kw):
    await get_db().bot_config.update_one(
        {"_id": "shield"}, {"$set": kw}, upsert=True
    )


# ── Scheduled jobs ──────────────────────────────────────────────────────

async def add_scheduled_job(kind: str, run_at: int, payload: dict) -> str:
    from uuid import uuid4
    jid = uuid4().hex[:10]
    await get_db().scheduled_jobs.insert_one(
        {"_id": jid, "kind": kind, "run_at": run_at, "payload": payload,
         "created_at": now(), "done": False}
    )
    return jid


async def list_scheduled_jobs(limit: int = 50):
    return await get_db().scheduled_jobs.find(
        {"done": False}, sort=[("run_at", 1)], limit=limit
    ).to_list(limit)


async def get_due_jobs():
    return await get_db().scheduled_jobs.find(
        {"done": False, "run_at": {"$lte": now()}}
    ).to_list(200)


async def mark_job_done(jid: str):
    await get_db().scheduled_jobs.update_one(
        {"_id": jid}, {"$set": {"done": True}}
    )


async def cancel_scheduled_job(jid: str) -> bool:
    r = await get_db().scheduled_jobs.update_one(
        {"_id": jid, "done": False}, {"$set": {"done": True}}
    )
    return r.modified_count > 0


# ── Global auto-reply rules ─────────────────────────────────────────────

async def add_autoreply(trigger: str, response: str) -> str:
    from uuid import uuid4
    rid = uuid4().hex[:8]
    await get_db().autoreplies.insert_one(
        {"_id": rid, "trigger": trigger.lower(), "response": response,
         "created_at": now()}
    )
    return rid


async def list_autoreplies(limit: int = 100):
    return await get_db().autoreplies.find({}).to_list(limit)


async def del_autoreply(rid: str) -> bool:
    r = await get_db().autoreplies.delete_one({"_id": rid})
    return r.deleted_count > 0


# ── Global blacklist words ──────────────────────────────────────────────

async def add_blackword(word: str):
    w = word.lower()
    await get_db().blackwords.update_one(
        {"_id": w}, {"$set": {"word": w}}, upsert=True
    )


async def list_blackwords(limit: int = 500):
    return await get_db().blackwords.find({}).to_list(limit)


async def del_blackword(word: str) -> bool:
    r = await get_db().blackwords.delete_one({"_id": word.lower()})
    return r.deleted_count > 0


# ── Watched users ───────────────────────────────────────────────────────

async def add_watch(uid: int, note: str = ""):
    await get_db().watchlist.update_one(
        {"_id": int(uid)},
        {"$set": {"note": note, "since": now()}}, upsert=True
    )


async def remove_watch(uid: int) -> bool:
    r = await get_db().watchlist.delete_one({"_id": int(uid)})
    return r.deleted_count > 0


async def list_watch(limit: int = 200):
    return await get_db().watchlist.find({}).to_list(limit)


async def is_watched(uid: int) -> bool:
    try:
        d = await get_db().watchlist.find_one({"_id": int(uid)})
        return d is not None
    except Exception:
        return False


async def touch_watched_activity(uid: int, chat_id: int):
    """Record a watched user's latest activity (called from middleware)."""
    try:
        await get_db().watchlist.update_one(
            {"_id": int(uid)},
            {"$set": {"last_active": now(), "last_chat": int(chat_id)}},
            upsert=False,
        )
    except Exception:
        pass


# ── Allowed-bots gate ───────────────────────────────────────────────────

async def list_allowed_bots() -> list:
    try:
        d = await get_db().bot_config.find_one({"_id": "allowed_bots"})
        if d and isinstance(d.get("usernames"), list):
            return [str(x).lower() for x in d["usernames"]]
    except Exception:
        pass
    return []


async def set_allowed_bots(usernames: list):
    await get_db().bot_config.update_one(
        {"_id": "allowed_bots"},
        {"$set": {"usernames": [str(x).lower() for x in usernames]}},
        upsert=True,
    )


async def get_botgate_mode() -> str:
    try:
        d = await get_db().bot_config.find_one({"_id": "botgate"})
        return d.get("mode", "off") if d else "off"
    except Exception:
        return "off"


async def set_botgate_mode(mode: str):
    await get_db().bot_config.update_one(
        {"_id": "botgate"}, {"$set": {"mode": mode}}, upsert=True
    )


# ── Owner log channel + notify toggle ───────────────────────────────────

async def get_log_chat() -> int:
    try:
        d = await get_db().bot_config.find_one({"_id": "log_chat"})
        if d and isinstance(d.get("chat_id"), int):
            return d["chat_id"]
    except Exception:
        pass
    return 0


async def set_log_chat(chat_id: int):
    await get_db().bot_config.update_one(
        {"_id": "log_chat"}, {"$set": {"chat_id": int(chat_id)}}, upsert=True
    )


async def get_notify() -> bool:
    try:
        d = await get_db().bot_config.find_one({"_id": "notify"})
        return bool(d.get("on", True)) if d else True
    except Exception:
        return True


async def set_notify(on: bool):
    await get_db().bot_config.update_one(
        {"_id": "notify"}, {"$set": {"on": bool(on)}}, upsert=True
    )


# ── Bot persona + global default welcome ────────────────────────────────

async def get_persona() -> str:
    try:
        d = await get_db().bot_config.find_one({"_id": "persona"})
        return d.get("text", "") if d else ""
    except Exception:
        return ""


async def set_persona(text: str):
    await get_db().bot_config.update_one(
        {"_id": "persona"}, {"$set": {"text": text}}, upsert=True
    )


async def get_default_welcome() -> str:
    try:
        d = await get_db().bot_config.find_one({"_id": "default_welcome"})
        return d.get("text", "") if d else ""
    except Exception:
        return ""


async def set_default_welcome(text: str):
    await get_db().bot_config.update_one(
        {"_id": "default_welcome"}, {"$set": {"text": text}}, upsert=True
    )


# ── Command usage stats ──────────────────────────────────────────────────

async def bump_command_stat(cmd: str):
    try:
        await get_db().command_usage.update_one(
            {"_id": cmd}, {"$inc": {"count": 1}}, upsert=True
        )
    except Exception:
        pass


async def top_commands(limit: int = 20):
    return await get_db().command_usage.find({}).sort("count", -1).limit(limit).to_list(limit)


# ── Error log (lightweight, kept short) ─────────────────────────────────

async def log_error_entry(text: str):
    try:
        await get_db().error_log.insert_one({"t": now(), "msg": text})
        # keep the collection bounded to the latest 200 entries
        count = await get_db().error_log.count_documents({})
        if count > 200:
            excess = await get_db().error_log.find({}, {"_id": 1}).sort("t", 1).to_list(count - 200)
            ids = [e["_id"] for e in excess]
            if ids:
                await get_db().error_log.delete_many({"_id": {"$in": ids}})
    except Exception:
        pass


async def recent_errors(limit: int = 20):
    return await get_db().error_log.find({}).sort("t", -1).limit(limit).to_list(limit)


# ── Economy overview helpers ────────────────────────────────────────────

async def total_balance_in_circulation() -> int:
    try:
        res = await get_db().users.aggregate([
            {"$match": {"is_banned": {"$ne": True}}},
            {"$group": {"_id": None,
                        "bal": {"$sum": {"$ifNull": ["$balance", 0]}},
                        "wal": {"$sum": {"$ifNull": ["$wallet", 0]}},
                        "gem": {"$sum": {"$ifNull": ["$gems", 0]}}}},
        ]).to_list(1)
        if res:
            return res[0]
    except Exception:
        pass
    return {"bal": 0, "wal": 0, "gem": 0}


# ── Card rank ────────────────────────────────────────────────────────

async def ensure_card_rank(uid):
    db = get_db()
    cr = await db.card_rank.find_one({"_id":uid})
    if not cr:
        cr = {"_id":uid,"wins":0,"losses":0,"won_amount":0,"lost_amount":0,"streak":0,"best_streak":0}
        await db.card_rank.insert_one(cr)
    return cr

async def get_card_rank(uid):
    cr = await get_db().card_rank.find_one({"_id":uid})
    return cr or await ensure_card_rank(uid)

async def update_card_rank(uid, **kw):
    await get_db().card_rank.update_one({"_id":uid},{"$set":kw},upsert=True)

async def get_card_leaders(n=10):
    return await get_db().card_rank.find(sort=[("won_amount",-1)],limit=n).to_list(n)

async def get_card_rank_position(uid):
    cr = await get_card_rank(uid)
    c = await get_db().card_rank.count_documents({"won_amount":{"$gt":cr["won_amount"]}})
    t = await get_db().card_rank.count_documents({})
    return c+1, t

# ── Hack rank (Hack-the-Code game leaderboard) ────────────────────────
#
# Mirrors the card_rank collection so the unified /leaders panel can switch
# between game leaderboards. Winner earnings + win count are recorded when a
# hack game is won (see handlers/hack_game.py). best_streak/streak track the
# longest consecutive hack wins for a richer leaderboard row.

async def ensure_hack_rank(uid):
    db = get_db()
    hr = await db.hack_rank.find_one({"_id":uid})
    if not hr:
        hr = {"_id":uid,"wins":0,"losses":0,"won_amount":0,"lost_amount":0,"streak":0,"best_streak":0}
        await db.hack_rank.insert_one(hr)
    return hr

async def get_hack_rank(uid):
    hr = await get_db().hack_rank.find_one({"_id":uid})
    return hr or await ensure_hack_rank(uid)

async def update_hack_rank(uid, **kw):
    await get_db().hack_rank.update_one({"_id":uid},{"$set":kw},upsert=True)

async def get_hack_leaders(n=10):
    return await get_db().hack_rank.find(sort=[("won_amount",-1)],limit=n).to_list(n)

async def get_hack_rank_position(uid):
    hr = await get_hack_rank(uid)
    c = await get_db().hack_rank.count_documents({"won_amount":{"$gt":hr["won_amount"]}})
    t = await get_db().hack_rank.count_documents({})
    return c+1, t

# ── Group economy ────────────────────────────────────────────────────

# Default group-economy document (see DEFAULT_USER note above). Legacy
# group_economy docs may lack protected_until / dead_until, so reads must
# be merged over these defaults to avoid KeyError in /grob, /gkill, etc.
DEFAULT_GUSER = {
    "user_id":0, "chat_id":0, "balance":0, "kills":0, "robs":0,
    "protected_until":0, "dead_until":0,
}

async def ensure_guser(uid, cid):
    key = f"{uid}_{cid}"
    db = get_db()
    gu = await db.group_economy.find_one({"_id":key})
    if not gu:
        gu = dict(DEFAULT_GUSER)
        gu["_id"] = key; gu["user_id"] = uid; gu["chat_id"] = cid
        await db.group_economy.insert_one(gu)
    else:
        merged = dict(DEFAULT_GUSER); merged.update(gu); gu = merged
    return gu

async def get_guser(uid, cid):
    key = f"{uid}_{cid}"
    gu = await get_db().group_economy.find_one({"_id":key})
    if not gu:
        return await ensure_guser(uid, cid)
    merged = dict(DEFAULT_GUSER); merged.update(gu)
    return merged

async def update_guser(uid, cid, **kw):
    if kw: await get_db().group_economy.update_one({"_id":f"{uid}_{cid}"},{"$set":kw},upsert=True)

async def get_granks(cid, n=10):
    return await get_db().group_economy.find(
        {"chat_id":cid},sort=[("balance",-1)],limit=n
    ).to_list(n)

# ── Warnings ─────────────────────────────────────────────────────────

async def add_warning(uid, cid, reason, by):
    await get_db().warnings.insert_one({"user_id":uid,"chat_id":cid,"reason":reason,"warned_by":by,"warned_at":now()})

async def get_warnings(uid, cid):
    return await get_db().warnings.find({"user_id":uid,"chat_id":cid},sort=[("warned_at",-1)]).to_list(50)

async def count_warnings(uid, cid):
    return await get_db().warnings.count_documents({"user_id":uid,"chat_id":cid})

async def remove_last_warning(uid, cid):
    w = await get_db().warnings.find_one({"user_id":uid,"chat_id":cid},sort=[("warned_at",-1)])
    if w:
        await get_db().warnings.delete_one({"_id":w["_id"]}); return True
    return False

# ── Items ─────────────────────────────────────────────────────────────

async def add_item(uid, name, qty=1):
    ex = await get_db().items.find_one({"owner_id":uid,"item_name":name})
    if ex:
        await get_db().items.update_one({"owner_id":uid,"item_name":name},{"$inc":{"quantity":qty}})
    else:
        await get_db().items.insert_one({"owner_id":uid,"item_name":name,"quantity":qty})

async def get_items(uid):
    return await get_db().items.find({"owner_id":uid}).to_list(100)

async def remove_item(uid, name, qty=1):
    it = await get_db().items.find_one({"owner_id":uid,"item_name":name})
    if not it or it["quantity"]<qty: return False
    if it["quantity"]==qty:
        await get_db().items.delete_one({"_id":it["_id"]})
    else:
        await get_db().items.update_one({"_id":it["_id"]},{"$inc":{"quantity":-qty}})
    return True

# ── Village ───────────────────────────────────────────────────────────

async def ensure_village(uid):
    db = get_db()
    v = await db.village.find_one({"_id":uid})
    if not v:
        v = {"_id":uid,"wood":0,"stone":0,"iron":0,"citizens":50,"max_citizens":50,
             "workers":0,"troops":{},"home_level":1,"camp_level":1,"hut_level":1,
             "woodyard_level":1,"quarry_level":1,"iron_mine_level":1,"walls":{},
             "defense":{},"storage_cap":2000,"treasury":0,"vault":0,
             "last_mine":now(),"last_tax":now(),"last_attack":0,
             "stage":"village","kingdom_level":1,"protected_until":0}
        await db.village.insert_one(v)
    return v

async def get_village(uid):
    v = await get_db().village.find_one({"_id":uid})
    return v or await ensure_village(uid)

async def update_village(uid, **kw):
    if kw: await get_db().village.update_one({"_id":uid},{"$set":kw},upsert=True)

async def get_empire_top(n=10):
    pipeline = [{"$addFields":{"total":{"$add":["$vault","$treasury"]}}},
                {"$sort":{"total":-1}},{"$limit":n}]
    return await get_db().village.aggregate(pipeline).to_list(n)

# ── Coupons ───────────────────────────────────────────────────────────

async def use_global_coupon(uid, code):
    try:
        await get_db().global_used_coupons.insert_one({"user_id":uid,"coupon":code}); return True
    except Exception: return False

async def get_group_coupon(cid):
    return await get_db().group_coupons.find_one({"_id":cid})

async def set_group_coupon(cid, code, amount, by):
    await get_db().group_coupons.update_one({"_id":cid},
        {"$set":{"code":code,"amount":amount,"created_by":by,"created_at":now()}},upsert=True)

async def delete_group_coupon(cid):
    await get_db().group_coupons.delete_one({"_id":cid})
    await get_db().group_coupon_used.delete_many({"chat_id":cid})

async def use_group_coupon(uid, cid):
    try:
        await get_db().group_coupon_used.insert_one({"user_id":uid,"chat_id":cid}); return True
    except Exception: return False

# ── Coupons with claim limits ──────────────────────────────────────────
# Global (owner) coupons and group coupons are two SEPARATE systems:
#   • global_coupons  — created by the owner via /addcoupon, claimable by
#     ANY user once each, up to `limit` total redemptions.
#   • group_coupons   — one per group (keyed by chat_id), created by an
#     admin via /create_coupon, claimable by each group member once, up to
#     `limit` total redemptions.
# Both track a live `claimed` counter and per-user uniqueness so a user
# can never double-claim and the total can never exceed `limit` (modulo a
# negligible race at the exact boundary, which is fine for a chat bot).

async def get_global_coupon(code):
    """Return the DB-stored global coupon doc for `code`, or None."""
    return await get_db().global_coupons.find_one({"_id": code})

async def set_global_coupon(code, amount, limit, by):
    """Upsert an owner global coupon. `claimed` starts at 0 only on insert."""
    await get_db().global_coupons.update_one(
        {"_id": code},
        {"$set": {"amount": amount, "limit": limit,
                  "created_by": by, "created_at": now()},
         "$setOnInsert": {"claimed": 0}},
        upsert=True
    )

async def delete_global_coupon(code):
    await get_db().global_coupons.delete_one({"_id": code})
    await get_db().global_used_coupons.delete_many({"coupon": code})

async def global_coupon_claim_count(code):
    return await get_db().global_used_coupons.count_documents({"coupon": code})

async def inc_global_coupon_claimed(code):
    await get_db().global_coupons.update_one(
        {"_id": code}, {"$inc": {"claimed": 1}}
    )

# Extend the group coupon helpers with a `limit` and live `claimed` counter.

async def set_group_coupon(cid, code, amount, by, limit=0):
    """Upsert a group coupon. `claimed` starts at 0 only on insert."""
    await get_db().group_coupons.update_one(
        {"_id": cid},
        {"$set": {"code": code, "amount": amount, "limit": limit,
                  "created_by": by, "created_at": now()},
         "$setOnInsert": {"claimed": 0}},
        upsert=True
    )

async def group_coupon_claim_count(cid):
    return await get_db().group_coupon_used.count_documents({"chat_id": cid})

async def inc_group_coupon_claimed(cid):
    await get_db().group_coupons.update_one(
        {"_id": cid}, {"$inc": {"claimed": 1}}
    )

# ── Valentines ────────────────────────────────────────────────────────

async def get_valentine(uid):
    return await get_db().valentines.find_one({"_id":uid})

async def set_valentine(uid, gender, c1, c2, c3):
    await get_db().valentines.update_one({"_id":uid},
        {"$set":{"gender":gender,"choice1":c1,"choice2":c2,"choice3":c3}},upsert=True)

async def delete_valentine(uid):
    await get_db().valentines.delete_one({"_id":uid})

async def count_valentines():
    t = await get_db().valentines.count_documents({})
    m = await get_db().valentines.count_documents({"gender":"male"})
    f = await get_db().valentines.count_documents({"gender":"female"})
    return {"t":t,"m":m,"f":f}

# ── Card games (in-memory handled in games.py) ────────────────────────

async def get_card_game_db(gid):
    return await get_db().card_games.find_one({"_id":gid})

async def save_card_game(gid, data):
    await get_db().card_games.update_one({"_id":gid},{"$set":data},upsert=True)

async def delete_card_game_db(gid):
    await get_db().card_games.delete_one({"_id":gid})

# ── Admin promotions ──────────────────────────────────────────────────

async def track_promotion(uid, cid, by):
    await get_db().admin_promotions.update_one({"_id":f"{uid}_{cid}"},
        {"$set":{"user_id":uid,"chat_id":cid,"promoted_by":by}},upsert=True)

async def get_bot_promotions(cid):
    return await get_db().admin_promotions.find({"chat_id":cid}).to_list(100)

async def remove_promotion(uid, cid):
    await get_db().admin_promotions.delete_one({"_id":f"{uid}_{cid}"})


# ── Whispers (private group messages with read receipts) ──────────────────
async def create_whisper(sender_id, target_id, chat_id, text):
    """Store a whisper and return its short id (used in the read button)."""
    wid = uuid.uuid4().hex[:12]
    await get_db().whispers.insert_one({
        "_id": wid, "sender_id": sender_id, "target_id": target_id,
        "chat_id": chat_id, "text": text, "read": False, "created_at": now(),
    })
    return wid


async def get_whisper(wid):
    return await get_db().whispers.find_one({"_id": wid})


async def mark_whisper_read(wid):
    await get_db().whispers.update_one({"_id": wid}, {"$set": {"read": True}})

# ── Welcome ───────────────────────────────────────────────────────────

# ── Owner-managed sticker packs (for sticker-to-sticker replies) ──────────
#
# WHY THIS EXISTS: previously, sending Iota-reply stickers required
# hand-editing a Python dict in handlers/sticker_reply.py (_STICKER_IDS)
# and redeploying the bot — not something the owner could do from
# within Telegram. This backs that same "mood -> sticker file_id" idea
# with MongoDB instead, so the owner can add/remove/list stickers with
# simple commands (see handlers/owner_panel.py's /addsticker etc.),
# no code changes or redeploys needed.

async def add_sticker_to_pack(mood: str, file_id: str, added_by: int) -> None:
    """Adds one sticker file_id under a mood/category. Moods are
    free-form strings chosen by the owner (e.g. 'happy', 'love', 'slap',
    or a custom pack name) — not a fixed enum, so the owner can build out
    exactly the categories they want over time."""
    await get_db().sticker_packs.update_one(
        {"_id": mood.lower()},
        {"$addToSet": {"file_ids": file_id},
         "$set": {"updated_at": now(), "updated_by": added_by}},
        upsert=True
    )

async def remove_sticker_from_pack(mood: str, file_id: str) -> bool:
    """Removes one sticker by file_id from a mood. Returns True if it existed."""
    result = await get_db().sticker_packs.update_one(
        {"_id": mood.lower()}, {"$pull": {"file_ids": file_id}}
    )
    return result.modified_count > 0

async def get_stickers_for_mood(mood: str) -> list:
    doc = await get_db().sticker_packs.find_one({"_id": mood.lower()})
    return doc.get("file_ids", []) if doc else []

async def list_all_sticker_packs() -> dict:
    """Returns {mood: count} for every configured mood — used by /stickerpacks."""
    docs = await get_db().sticker_packs.find({}).to_list(1000)
    return {d["_id"]: len(d.get("file_ids", [])) for d in docs}

async def clear_sticker_pack(mood: str) -> int:
    """Removes an entire mood's sticker pack. Returns how many were removed."""
    doc = await get_db().sticker_packs.find_one({"_id": mood.lower()})
    count = len(doc.get("file_ids", [])) if doc else 0
    await get_db().sticker_packs.delete_one({"_id": mood.lower()})
    return count


async def get_welcome_settings(cid):
    """
    Returns this group's welcome-message settings, or sane defaults if
    none have been saved yet. This function's `async def` line was
    missing entirely — the two lines below used to sit here as orphaned,
    never-executed code (not inside any function), which meant
    `from utils.mongo_db import get_welcome_settings` in handlers/welcome.py
    raised an ImportError and crashed the ENTIRE bot on startup before
    a single handler could load. This is the same class of bug as the
    earlier missing-GIFS crash.
    """
    ws = await get_db().welcome_settings.find_one({"_id": cid})
    # Defaults: text-only welcome (GIF off) for every group.
    base = {
        "_id": cid,
        "enabled": True,
        "custom_msg": "",
        "send_gif": False,
        "send_sticker": False,
        "welcome_buttons": [],
    }
    if not ws:
        return base
    base.update(ws)
    if "welcome_buttons" not in base or base["welcome_buttons"] is None:
        base["welcome_buttons"] = []
    # Legacy docs that never stored send_gif → treat as off (text-only default)
    if "send_gif" not in ws:
        base["send_gif"] = False
    return base

async def set_welcome_settings(cid, **kw):
    await get_db().welcome_settings.update_one({"_id":cid},{"$set":kw},upsert=True)


# ── Welcome-sent dedupe (join event + soft first-message welcome) ───────

def _welcome_key(chat_id: int, user_id: int) -> str:
    return f"{int(chat_id)}:{int(user_id)}"


async def was_welcomed(chat_id: int, user_id: int) -> bool:
    """True if we already welcomed this user in this chat (recently or ever)."""
    try:
        doc = await get_db().welcome_sent.find_one({"_id": _welcome_key(chat_id, user_id)})
        return bool(doc)
    except Exception:
        return False


async def mark_welcomed(chat_id: int, user_id: int) -> None:
    try:
        await get_db().welcome_sent.update_one(
            {"_id": _welcome_key(chat_id, user_id)},
            {"$set": {"chat_id": int(chat_id), "user_id": int(user_id), "ts": now()}},
            upsert=True,
        )
    except Exception:
        pass


async def clear_welcomed(chat_id: int, user_id: int) -> None:
    """Allow re-welcome after leave/rejoin."""
    try:
        await get_db().welcome_sent.delete_one({"_id": _welcome_key(chat_id, user_id)})
    except Exception:
        pass

# ── Group protection ──────────────────────────────────────────────────

# Default protection + linkban fields (merged over legacy docs).
DEFAULT_PROT = {
    "enabled": True,
    # ── Existing ─────────────────────────────────────────────────────────
    "anti_spam": True,
    "anti_link": True,
    "anti_arabic": False,
    "anti_forward": False,
    "anti_bot": True,
    "anti_flood": True,
    "flood_limit": 5,
    "flood_window": 5,
    "anti_raid": True,
    "raid_threshold": 10,
    "raid_window": 30,
    "profanity_filter": False,
    "bad_word_mode": "contains",
    "log_channel": 0,
    # ── New-member gate ─────────────────────────────────────────────────
    "newmember_gate": False,
    "newmember_minutes": 10,
    "newmember_action": "delete",
    # ── Mention spam ───────────────────────────────────────────────────
    "anti_mention": False,
    "mention_limit": 5,
    "mention_action": "delete",
    # ── Name filter ─────────────────────────────────────────────────────
    "name_filter": False,
    "name_filter_action": "delete",
    "name_blocklist": [],
    # ── Channel posts only (not all forwards) ───────────────────────────
    "anti_channel_post": False,
    # ── Excessive CAPS ──────────────────────────────────────────────────
    "anti_caps": False,
    "caps_min_len": 12,
    "caps_ratio": 0.75,
    # ── Repeat message spam ─────────────────────────────────────────────
    "anti_repeat": False,
    "repeat_count": 3,
    "repeat_window": 30,
    # ── Raid upgrade ────────────────────────────────────────────────────
    "raid_action": "slow",
    "raid_mute_new": False,
    # ── RTL / Zalgo ────────────────────────────────────────────────────
    "anti_zalgo": False,
    "anti_rtl": False,
    # ── Linkban (foreign invite / spam links) ──────────────────────────
    "linkban_enabled": False,
    "linkban_mode": "delete",
    "linkban_mute_secs": 300,
    "linkban_allow_own": True,
    "linkban_block_urls": False,
    "link_allowlist": [],
    # ── NSFW filter ───────────────────────────────────────────────────
    "nsfw_enabled": True,
    "nsfw_threshold": 90,
    "nsfw_scan_stickers": True,
    "nsfw_scan_photos": True,
    "nsfw_action": "delete",
    "nsfw_mute_secs": 300,
    "nsfw_notify": False,
    "nsfw_set_allowlist": [],
    "nsfw_set_banlist": [],
    "nsfw_id_banlist": [],
    "nsfw_id_allowlist": [],
}


async def get_prot(cid):
    p = await get_db().group_protection.find_one({"_id": cid})
    if not p:
        p = dict(DEFAULT_PROT)
        p["_id"] = cid
        try:
            await get_db().group_protection.insert_one(dict(p))
        except Exception:
            pass
        return p
    # Merge defaults so legacy docs never miss new linkban fields
    merged = dict(DEFAULT_PROT)
    merged.update(p)
    if merged.get("link_allowlist") is None:
        merged["link_allowlist"] = []
    return merged


async def update_prot(cid, **kw):
    await get_db().group_protection.update_one({"_id": cid}, {"$set": kw}, upsert=True)


async def add_link_allow(cid, entry: str) -> bool:
    """Add a whitelist entry (domain / @user / substring). Returns False if duplicate."""
    from utils.linkban import normalize_allow_entry
    e = normalize_allow_entry(entry)
    if not e:
        return False
    prot = await get_prot(cid)
    allow = list(prot.get("link_allowlist") or [])
    if e in [normalize_allow_entry(x) for x in allow]:
        return False
    allow.append(e)
    await update_prot(cid, link_allowlist=allow)
    return True


async def remove_link_allow(cid, entry: str) -> bool:
    from utils.linkban import normalize_allow_entry
    e = normalize_allow_entry(entry)
    if not e:
        return False
    prot = await get_prot(cid)
    allow = list(prot.get("link_allowlist") or [])
    new = [x for x in allow if normalize_allow_entry(x) != e]
    if len(new) == len(allow):
        return False
    await update_prot(cid, link_allowlist=new)
    return True


# ── NSFW cache + list helpers ────────────────────────────────────────────

async def nsfw_cache_get(unique_id: str) -> dict | None:
    doc = await get_db().nsfw_cache.find_one({"_id": unique_id})
    if doc:
        doc.pop("_id", None)
        return doc
    return None


async def nsfw_cache_set(unique_id: str, score: int, reasons: list, is_nsfw: bool):
    await get_db().nsfw_cache.update_one(
        {"_id": unique_id},
        {"$set": {"score": int(score), "reasons": list(reasons),
                  "is_nsfw": bool(is_nsfw), "ts": now()}},
        upsert=True,
    )


async def nsfw_list_add(cid, field: str, value: str):
    prot = await get_prot(cid)
    current = list(prot.get(field) or [])
    if value not in current:
        current.append(value)
        await update_prot(cid, **{field: current})


async def nsfw_list_remove(cid, field: str, value: str):
    prot = await get_prot(cid)
    current = list(prot.get(field) or [])
    new = [v for v in current if v != value]
    if len(new) != len(current):
        await update_prot(cid, **{field: new})

# ── Reports ───────────────────────────────────────────────────────────

async def add_report(cid, reporter, reported, reason, msg_text=""):
    await get_db().reports.insert_one({"chat_id":cid,"reporter_id":reporter,
        "reported_id":reported,"reason":reason,"msg_text":msg_text,"status":"pending","created_at":now()})

async def get_reports(cid, status="pending"):
    return await get_db().reports.find({"chat_id":cid,"status":status},sort=[("created_at",-1)],limit=20).to_list(20)

async def get_report_count(cid):
    return await get_db().reports.count_documents({"chat_id":cid,"status":"pending"})

# ── Moderation action log ─────────────────────────────────────────────

async def log_mod_action(chat_id: int, actor_id: int, action: str, target_id: int = 0, reason: str = "", meta: dict = None):
    try:
        await get_db().mod_actions.insert_one({
            "chat_id": chat_id, "actor_id": actor_id,
            "action": action, "target_id": target_id,
            "reason": reason or "", "meta": meta or {},
            "created_at": now(),
        })
    except Exception:
        pass

async def list_mod_actions(chat_id: int, limit: int = 20):
    try:
        return await get_db().mod_actions.find(
            {"chat_id": chat_id}
        ).sort("created_at", -1).limit(limit).to_list(limit)
    except Exception:
        try:
            return await get_db().mod_actions.find(
                {"chat_id": chat_id}
            ).sort([("created_at", -1)]).limit(limit).to_list(limit)
        except Exception:
            return []

async def clear_mod_actions(chat_id: int) -> int:
    try:
        r = await get_db().mod_actions.delete_many({"chat_id": chat_id})
        return r.deleted_count
    except Exception:
        return 0

# ── Bad words ─────────────────────────────────────────────────────────

async def get_bad_words(cid):
    doc = await get_db().bad_words.find_one({"_id":cid})
    return doc.get("words",[]) if doc else []

async def add_bad_word(cid, word):
    await get_db().bad_words.update_one({"_id":cid},{"$addToSet":{"words":word.lower()}},upsert=True)

async def remove_bad_word(cid, word):
    await get_db().bad_words.update_one({"_id":cid},{"$pull":{"words":word.lower()}})

# ── Name blocklist ────────────────────────────────────────────────────

async def get_name_blocklist(cid):
    doc = await get_db().name_blocklist.find_one({"_id":cid})
    return doc.get("words",[]) if doc else []

async def add_name_block(cid, word):
    await get_db().name_blocklist.update_one({"_id":cid},{"$addToSet":{"words":word.lower()}},upsert=True)

async def remove_name_block(cid, word):
    await get_db().name_blocklist.update_one({"_id":cid},{"$pull":{"words":word.lower()}})

# ── Misc ──────────────────────────────────────────────────────────────

async def get_sticker_pack(uid):
    return await get_db().sticker_packs.find_one({"_id":uid})

async def set_sticker_pack(uid, pack_name, pack_title):
    await get_db().sticker_packs.update_one({"_id":uid},
        {"$set":{"pack_name":pack_name,"pack_title":pack_title}},upsert=True)

async def set_top_group(rank, uid, name, link):
    await get_db().top_groups.update_one({"_id":rank},
        {"$set":{"user_id":uid,"group_name":name,"group_link":link}},upsert=True)

async def get_top_groups():
    return await get_db().top_groups.find(sort=[("_id",1)]).to_list(5)

async def remove_top_group(rank):
    await get_db().top_groups.delete_one({"_id": rank})

async def is_gaming_open(cid):
    """Back-compat shim — now reads from the unified system-status doc
    (see get_system_status below) instead of its own separate flag, so
    /close and /open correctly control every game, not just card/bet."""
    status = await get_system_status(cid)
    return status.get("games", True)

async def set_gaming_status(cid, status):
    await set_system_status(cid, games=status)

# ── Unified system-closing (/close, /open) ──────────────────────────────
#
# 🔴 WHY THIS EXISTS: /close previously only ever set ONE boolean
# ("is_gaming_open"), and that boolean was only ever CHECKED in 2 out of
# ~15 game commands (card and bet) — every other game (bomb, ludo,
# bluff, hack, wordgame, hangman, quiz, tictactoe, rps, werewolf, slots)
# and EVERY economy command (/daily, /rob, /kill, /give, /wallet, etc.)
# and EVERY village-war command completely ignored /close entirely.
# This is the actual, root-cause fix: one unified status document per
# chat covering all three closable systems (games / economy / village),
# with helper decorators in utils/system_gate.py that every relevant
# command now uses, instead of each command needing its own ad-hoc check.

async def get_system_status(cid: int) -> dict:
    doc = await get_db().system_status.find_one({"_id": cid})
    if not doc:
        return {"games": True, "economy": True, "village": True}
    return {
        "games":    doc.get("games", True),
        "economy":  doc.get("economy", True),
        "village":  doc.get("village", True),
    }

async def set_system_status(cid: int, **kw):
    """set_system_status(cid, games=False) or economy=False / village=False,
    or any combination — only the keys passed in are changed."""
    valid = {k: v for k, v in kw.items() if k in ("games", "economy", "village")}
    if valid:
        await get_db().system_status.update_one(
            {"_id": cid}, {"$set": valid}, upsert=True
        )

# ── Stars payments tracking ────────────────────────────────────────────

async def log_stars_payment(uid, payload, stars, full_name=""):
    await get_db().stars_payments.insert_one({
        "user_id":uid,"payload":payload,"stars":stars,
        "full_name":full_name,"created_at":now()
    })

async def get_stars_total():
    pipeline = [{"$group":{"_id":None,"total":{"$sum":"$stars"}}}]
    r = await get_db().stars_payments.aggregate(pipeline).to_list(1)
    return r[0]["total"] if r else 0

# ── Promoter system ───────────────────────────────────────────────────

async def add_promoter(uid, ref_code):
    await get_db().promoters.update_one({"_id":uid},
        {"$set":{"ref_code":ref_code,"referred":[],"earnings":0,"created_at":now()}},upsert=True)

async def get_promoter(uid):
    return await get_db().promoters.find_one({"_id":uid})

async def get_promoter_by_code(code):
    return await get_db().promoters.find_one({"ref_code":code})

async def add_referral(promoter_uid, referred_uid, reward):
    await get_db().promoters.update_one({"_id":promoter_uid},{
        "$addToSet":{"referred":referred_uid},
        "$inc":{"earnings":reward}
    })

# ── Last seen ─────────────────────────────────────────────────────────

async def update_last_seen(uid, username="", full_name=""):
    await get_db().last_seen.update_one({"_id":uid},{
        "$set":{"username":username,"full_name":full_name,"last_seen":now()}
    },upsert=True)

async def get_last_seen(uid):
    return await get_db().last_seen.find_one({"_id":uid})

# ── Indexes ───────────────────────────────────────────────────────────

async def create_indexes():
    db = get_db()
    await db.users.create_index([("balance",-1)])
    await db.users.create_index([("kills",-1)])
    await db.group_economy.create_index([("chat_id",1),("balance",-1)])
    await db.warnings.create_index([("user_id",1),("chat_id",1)])
    await db.card_rank.create_index([("won_amount",-1)])
    await db.reports.create_index([("chat_id",1),("status",1)])
    await db.last_seen.create_index([("last_seen",-1)])
    try:
        await db.connections.create_index([("status",1),("expires_at",1)])
        await db.ai_memory.create_index([("pair_key",1),("ts",1)])
    except Exception: pass
    try:
        await db.global_used_coupons.create_index([("user_id",1),("coupon",1)],unique=True)
        await db.group_coupon_used.create_index([("user_id",1),("chat_id",1)],unique=True)
        await db.stars_payments.create_index([("user_id",1)])
        await db.global_coupons.create_index([("code",1)])
    except Exception: pass
    try:
        await db.join_requests.create_index([("chat_id",1),("user_id",1)], unique=True)
        await db.join_requests.create_index([("chat_id",1),("date",1)])
    except Exception: pass
    try:
        await db.fixed_deposits.create_index([("uid",1)])
        await db.fixed_deposits.create_index([("maturity_ts",1)])
        await db.recurring_deposits.create_index([("uid",1)])
        await db.recurring_deposits.create_index([("next_due_ts",1)])
        await db.banks.create_index([("owner_id",1)])
        await db.bank_txns.create_index([("uid",1),("ts",-1)])
    except Exception: pass
    try:
        # 🏢 Business Empire (handlers/business.py + utils/business_store.py)
        await db.businesses.create_index([("owner_id",1),("active",1)])
        await db.businesses.create_index([("type",1),("active",1)])
        await db.businesses.create_index([("active",1),("total_earned",-1)])
        await db.businesses.create_index([("active",1),("next_salary_ts",1)])
        await db.business_offers.create_index([("target_id",1),("status",1)])
        await db.business_offers.create_index([("biz_id",1),("target_id",1)])
    except Exception: pass
    try:
        await db.users.create_index([("xp", -1)])
        await db.clans.create_index([("tag", 1)], unique=True)
        await db.clans.create_index([("total_xp", -1)])
    except Exception: pass
    print("✅ MongoDB indexes created")


# ══════════════════════════════════════════════════════════════════════
# 🆕 Join request storage (captured from ChatJoinRequest updates)
# ══════════════════════════════════════════════════════════════════════

async def save_join_request(chat_id: int, user_id: int, full_name: str,
                            username: str, date: int, invite_link: str = ""):
    await get_db().join_requests.update_one(
        {"chat_id": chat_id, "user_id": user_id},
        {"$set": {
            "chat_id": chat_id, "user_id": user_id,
            "full_name": full_name, "username": username,
            "date": date, "invite_link": invite_link,
        }},
        upsert=True,
    )


async def delete_join_request(chat_id: int, user_id: int):
    await get_db().join_requests.delete_one({"chat_id": chat_id, "user_id": user_id})


async def get_join_requests(chat_id: int, limit: int = None) -> list:
    cur = get_db().join_requests.find({"chat_id": chat_id}).sort("date", 1)
    if limit:
        cur = cur.limit(limit)
    return await cur.to_list(limit or 100000)


async def count_join_requests(chat_id: int) -> int:
    return await get_db().join_requests.count_documents({"chat_id": chat_id})

async def deduct_balance(uid, amt):
    """Deduct balance safely (won't go below 0)."""
    await get_db().users.update_one(
        {"_id": uid},
        {"$inc": {"balance": -abs(amt)}}
    )


# ══════════════════════════════════════════════════════════════════════
# 🆕 New feature storage (nickname / birthday / todo / countdown / giveaway)
# ══════════════════════════════════════════════════════════════════════

async def set_nickname(uid: int, nickname: str):
    await get_db().users.update_one({"_id": uid}, {"$set": {"nickname": nickname}}, upsert=True)

async def get_nickname(uid: int) -> str | None:
    u = await get_db().users.find_one({"_id": uid}, {"nickname": 1})
    return (u or {}).get("nickname")


async def set_birthday(uid: int, day: int, month: int, chat_id: int, full_name: str = ""):
    await get_db().birthdays.update_one(
        {"_id": uid},
        {"$set": {"day": day, "month": month, "chat_id": chat_id, "full_name": full_name}},
        upsert=True,
    )

async def get_birthday(uid: int) -> dict | None:
    return await get_db().birthdays.find_one({"_id": uid})

async def get_birthdays_today(day: int, month: int) -> list:
    return await get_db().birthdays.find({"day": day, "month": month}).to_list(1000)


async def add_todo(uid: int, text: str):
    await get_db().todos.update_one(
        {"_id": uid}, {"$push": {"items": {"text": text, "done": False}}}, upsert=True
    )

async def get_todos(uid: int) -> list:
    doc = await get_db().todos.find_one({"_id": uid})
    return (doc or {}).get("items", [])

async def complete_todo(uid: int, index: int) -> bool:
    doc = await get_db().todos.find_one({"_id": uid})
    items = (doc or {}).get("items", [])
    if 0 <= index < len(items):
        items[index]["done"] = True
        await get_db().todos.update_one({"_id": uid}, {"$set": {"items": items}})
        return True
    return False

async def clear_todos(uid: int):
    await get_db().todos.update_one({"_id": uid}, {"$set": {"items": []}}, upsert=True)


async def create_countdown(uid: int, name: str, target_iso: str, chat_id: int):
    key = f"{uid}:{name.lower()}"
    await get_db().countdowns.update_one(
        {"_id": key},
        {"$set": {"uid": uid, "name": name, "target": target_iso, "chat_id": chat_id}},
        upsert=True,
    )

async def get_countdown(uid: int, name: str) -> dict | None:
    return await get_db().countdowns.find_one({"_id": f"{uid}:{name.lower()}"})

async def get_countdowns_for_user(uid: int) -> list:
    return await get_db().countdowns.find({"uid": uid}).to_list(200)


async def create_giveaway(chat_id: int, message_id: int, prize: str, end_ts: float, host_id: int) -> str:
    gid = f"{chat_id}_{message_id}"
    await get_db().giveaways.insert_one({
        "_id": gid, "chat_id": chat_id, "message_id": message_id,
        "prize": prize, "end_ts": end_ts, "host_id": host_id,
        "participants": [], "ended": False, "winner": None,
    })
    return gid

async def join_giveaway(gid: str, uid: int) -> bool:
    doc = await get_db().giveaways.find_one({"_id": gid})
    if not doc or doc.get("ended"):
        return False
    if uid in doc.get("participants", []):
        return False
    await get_db().giveaways.update_one({"_id": gid}, {"$push": {"participants": uid}})
    return True

async def get_giveaway(gid: str) -> dict | None:
    return await get_db().giveaways.find_one({"_id": gid})

async def end_giveaway(gid: str, winner: int | None):
    await get_db().giveaways.update_one({"_id": gid}, {"$set": {"ended": True, "winner": winner}})


# ══════════════════════════════════════════════════════════════════════
# 🆕 Economy: bank / loan / lottery
# ══════════════════════════════════════════════════════════════════════

async def get_bank(uid: int) -> int:
    u = await get_db().users.find_one({"_id": uid}, {"bank": 1})
    return (u or {}).get("bank", 0)

async def deposit_to_bank(uid: int, amt: int):
    await get_db().users.update_one(
        {"_id": uid}, {"$inc": {"balance": -amt, "bank": amt}}, upsert=True
    )

async def withdraw_from_bank(uid: int, amt: int):
    await get_db().users.update_one(
        {"_id": uid}, {"$inc": {"balance": amt, "bank": -amt}}, upsert=True
    )


async def get_loan(uid: int) -> dict:
    u = await get_db().users.find_one({"_id": uid}, {"loan_amount": 1, "loan_due_ts": 1})
    u = u or {}
    return {"amount": u.get("loan_amount", 0), "due_ts": u.get("loan_due_ts", 0)}

async def take_loan(uid: int, amt: int, due_ts: float):
    await get_db().users.update_one(
        {"_id": uid},
        {"$inc": {"balance": amt}, "$set": {"loan_amount": amt, "loan_due_ts": due_ts}},
        upsert=True,
    )

async def repay_loan(uid: int, amt: int) -> int:
    """Repays up to `amt` toward the loan. Returns the amount actually
    repaid (capped at what's owed)."""
    loan = await get_loan(uid)
    owed = loan["amount"]
    pay = min(amt, owed)
    if pay <= 0:
        return 0
    remaining = owed - pay
    if remaining <= 0:
        # Fully repaid — clear both fields cleanly.
        await get_db().users.update_one(
            {"_id": uid},
            {"$inc": {"balance": -pay}, "$set": {"loan_amount": 0, "loan_due_ts": 0}},
        )
    else:
        # Partial repayment — only touch loan_amount, leave loan_due_ts
        # exactly as it was (setting it to None here would make later
        # `due_ts > now` comparisons crash with a TypeError).
        await get_db().users.update_one(
            {"_id": uid},
            {"$inc": {"balance": -pay}, "$set": {"loan_amount": remaining}},
        )
    return pay


# ═══════════════════════════════════════════════════════════════════════════
# 🆕 PREMIUM BANKING SYSTEM (real-life style: bank interest, FD, RD, passbook,
# and user-owned Banks/Branches). All of this is PREMIUM-ONLY — see the
# premium gate in handlers/banking.py. Money is always moved atomically so
# coins can never be created or destroyed except exactly as intended.
# ═══════════════════════════════════════════════════════════════════════════

# ── Passbook / transaction ledger ───────────────────────────────────────────
async def add_bank_txn(uid: int, ttype: str, amount: int, note: str = "",
                       counter=None):
    """Append a transaction to the user's passbook. Never raises."""
    try:
        await get_db().bank_txns.insert_one({
            "uid": uid, "type": ttype, "amount": int(amount),
            "note": note or "", "counter": counter, "ts": int(time.time()),
        })
    except Exception:
        logger.debug("add_bank_txn failed for %s", uid)


async def get_bank_txns(uid: int, limit: int = 15) -> list:
    cur = get_db().bank_txns.find({"uid": uid}).sort("ts", -1).limit(limit)
    return await cur.to_list(length=limit)


# ── Bank (demand deposit) interest ──────────────────────────────────────────
async def accrue_bank_interest(rate: float = BANK_DAILY_RATE,
                               cap: int = PREMIUM_BANKING_CAP) -> int:
    """Credit daily interest on every non-empty bank balance (capped at the
    premium banking cap). Returns number of balances updated."""
    if rate <= 0:
        return 0
    db = get_db()
    updated = 0
    async for u in db.users.find({"bank": {"$gt": 0}}, {"_id": 1, "bank": 1}):
        bal = u["bank"]
        interest = int(bal * rate)
        if interest <= 0:
            continue
        new_bal = min(bal + interest, cap)
        if new_bal == bal:
            continue
        await db.users.update_one({"_id": u["_id"]}, {"$set": {"bank": new_bal}})
        updated += 1
    return updated


# ── Fixed Deposits (FD) ─────────────────────────────────────────────────────
async def create_fd(uid: int, principal: int, tenure_days: int,
                     rate: float) -> object:
    """Create a Fixed Deposit. Locks `principal` from the wallet atomically
    (gated on balance >= principal) so coins can never be created or lost.
    Returns the new FD document (with _id), or None if funds were
    insufficient."""
    db = get_db()
    res = await db.users.update_one(
        {"_id": uid, "balance": {"$gte": principal}},
        {"$inc": {"balance": -principal}},
    )
    if res.modified_count == 0:
        return None
    now = int(time.time())
    doc = {
        "uid": uid, "principal": principal, "rate": rate,
        "tenure_days": tenure_days, "created_at": now,
        "maturity_ts": now + tenure_days * 86400, "status": "active",
    }
    res = await get_db().fixed_deposits.insert_one(doc)
    doc["_id"] = res.inserted_id
    return doc


async def get_fd(fd_id) -> dict:
    from bson import ObjectId
    try:
        if not isinstance(fd_id, ObjectId):
            fd_id = ObjectId(fd_id)
    except Exception:
        return None
    return await get_db().fixed_deposits.find_one({"_id": fd_id})


async def list_fds(uid: int) -> list:
    cur = get_db().fixed_deposits.find({"uid": uid, "status": "active"}).sort("maturity_ts", 1)
    return await cur.to_list(length=50)


def fd_payout(fd: dict) -> int:
    """Compute the FD payout (principal + interest, capped at principal on
    break). Pure function — does no DB work, so it is intentionally NOT async.
    Caller credits the wallet with the returned amount.

    An FD pays full principal+interest once its lock-in has ended (status
    "matured", OR an "active" FD whose maturity_ts has passed). The early
    break penalty only applies when the holder cashes out BEFORE maturity."""
    principal = fd["principal"]
    if fd.get("status") == "broken":
        return principal
    interest = int(principal * fd.get("rate", 0))
    if fd.get("status") == "active" and fd.get("maturity_ts", 0) > int(time.time()):
        # genuine premature break
        elapsed = max(0, int(time.time()) - fd["created_at"])
        progress = min(1.0, elapsed / (fd["tenure_days"] * 86400))
        interest = int(principal * fd.get("rate", 0) * progress)
        penalty = int((principal + interest) * FD_BREAK_PENALTY)
        return max(principal, principal + interest - penalty)
    return principal + interest  # matured (or lock-in already ended)


async def settle_fd(fd_id, payout: int, status: str):
    await get_db().fixed_deposits.update_one(
        {"_id": fd_id}, {"$set": {"status": status}}
    )
    return payout


async def process_fd_maturities() -> int:
    """Mature every active FD whose lock-in has ended. Returns count."""
    now = int(time.time())
    db = get_db()
    done = 0
    async for fd in db.fixed_deposits.find(
        {"status": "active", "maturity_ts": {"$lte": now}}
    ):
        payout = fd_payout(fd)
        await db.users.update_one(
            {"_id": fd["uid"]}, {"$inc": {"balance": payout}}
        )
        await add_bank_txn(fd["uid"], "fd_mature", payout,
                           f"FD #{fd['_id']} matured")
        await db.fixed_deposits.update_one(
            {"_id": fd["_id"]}, {"$set": {"status": "matured"}}
        )
        done += 1
    return done


# ── Recurring Deposits (RD) ─────────────────────────────────────────────────
async def create_rd(uid: int, installment: int, months: int,
                     rate: float) -> object:
    """Create a Recurring Deposit and lock the first installment from the
    wallet atomically (gated on balance >= installment). Returns the new RD
    document (with _id), or None if funds were insufficient."""
    db = get_db()
    res = await db.users.update_one(
        {"_id": uid, "balance": {"$gte": installment}},
        {"$inc": {"balance": -installment}},
    )
    if res.modified_count == 0:
        return None
    now = int(time.time())
    # First installment is collected up-front (just deducted above).
    doc = {
        "uid": uid, "installment": installment, "months": months,
        "paid": 1, "total": installment, "rate": rate,
        "start_ts": now, "next_due_ts": now + 30 * 86400,
        "maturity_ts": now + months * 30 * 86400, "status": "active",
    }
    res = await get_db().recurring_deposits.insert_one(doc)
    doc["_id"] = res.inserted_id
    return doc


async def get_rd(rd_id) -> dict:
    from bson import ObjectId
    try:
        if not isinstance(rd_id, ObjectId):
            rd_id = ObjectId(rd_id)
    except Exception:
        return None
    return await get_db().recurring_deposits.find_one({"_id": rd_id})


async def list_rds(uid: int) -> list:
    cur = get_db().recurring_deposits.find(
        {"uid": uid, "status": "active"}
    ).sort("maturity_ts", 1)
    return await cur.to_list(length=50)


def rd_payout(rd: dict) -> int:
    """Maturity payout = total contributions + simple monthly interest.
    Pure function (no DB work) so it is intentionally NOT async."""
    if rd.get("status") == "broken":
        return rd["total"]
    interest = int(rd["total"] * rd.get("rate", 0) * rd["months"])
    return rd["total"] + interest


async def process_rd_installments() -> int:
    """Collect the next RD installment for every due plan and mature plans
    whose tenure is complete. Returns number of plans processed."""
    now = int(time.time())
    db = get_db()
    processed = 0
    async for rd in db.recurring_deposits.find({"status": "active"}):
        if now < rd["next_due_ts"] and now < rd["maturity_ts"]:
            continue
        uid = rd["uid"]
        # Collect installment if still within the plan and wallet allows.
        if rd["paid"] < rd["months"] and now >= rd["next_due_ts"]:
            u = await get_user(uid)
            if u.get("balance", 0) >= rd["installment"]:
                await db.users.update_one(
                    {"_id": uid}, {"$inc": {"balance": -rd["installment"]}}
                )
                await add_bank_txn(uid, "rd_instalment", -rd["installment"],
                                   f"RD #{rd['_id']} installment")
                await db.recurring_deposits.update_one(
                    {"_id": rd["_id"]},
                    {"$inc": {"paid": 1, "total": rd["installment"]},
                     "$set": {"next_due_ts": now + 30 * 86400}},
                )
                rd["paid"] += 1
                rd["total"] += rd["installment"]
        # Mature if tenure complete.
        if now >= rd["maturity_ts"] or rd["paid"] >= rd["months"]:
            payout = rd_payout(rd)
            await db.users.update_one(
                {"_id": uid}, {"$inc": {"balance": payout}}
            )
            await add_bank_txn(uid, "rd_mature", payout, f"RD #{rd['_id']} matured")
            await db.recurring_deposits.update_one(
                {"_id": rd["_id"]}, {"$set": {"status": "matured"}}
            )
        processed += 1
    return processed


# ── User-owned Banks / Branches ─────────────────────────────────────────────
async def create_bank(owner_id: int, name: str, reserve: int) -> object:
    """Open a bank. Locks `reserve` from the owner's wallet atomically
    (gated on balance >= reserve) so the reserve can never be created out of
    thin air. Returns the bank doc, or None if funds were insufficient."""
    db = get_db()
    res = await db.users.update_one(
        {"_id": owner_id, "balance": {"$gte": reserve}},
        {"$inc": {"balance": -reserve}},
    )
    if res.modified_count == 0:
        return None
    now = int(time.time())
    doc = {
        "owner_id": owner_id, "name": name[:40], "reserve": reserve,
        "rate": BANK_CUSTOMER_DAILY_RATE, "msg": "",
        "deposits": {}, "total_fees": 0, "created_at": now, "active": True,
    }
    res = await get_db().banks.insert_one(doc)
    doc["_id"] = res.inserted_id
    return doc


async def get_bank_info(bank_id) -> dict:
    from bson import ObjectId
    try:
        if not isinstance(bank_id, ObjectId):
            bank_id = ObjectId(bank_id)
    except Exception:
        return None
    return await get_db().banks.find_one({"_id": bank_id})


async def list_banks(limit: int = 10) -> list:
    cur = get_db().banks.find({"active": True}).sort("created_at", -1).limit(limit)
    return await cur.to_list(length=limit)


async def bank_deposit(bank_id, uid: int, amt: int) -> tuple:
    """Customer deposits `amt` into a bank. Returns (ok, fee_earned)."""
    if amt <= 0:
        return False, 0
    db = get_db()
    bank = await get_bank_info(bank_id)
    if not bank or not bank.get("active"):
        return False, 0
    # Deduct from customer wallet (gated), credit bank deposit pool.
    res = await db.users.update_one(
        {"_id": uid, "balance": {"$gte": amt}},
        {"$inc": {"balance": -amt}},
    )
    if res.modified_count == 0:
        return False, 0
    uid_s = str(uid)
    dep = bank["deposits"].get(uid_s, {"principal": 0, "acc_int": 0, "last_acc_ts": int(time.time())})
    dep["principal"] = dep.get("principal", 0) + amt
    fee = int(amt * BANK_OWNER_DEPOSIT_FEE)
    await db.banks.update_one(
        {"_id": bank_id},
        {"$set": {f"deposits.{uid_s}": dep}, "$inc": {"total_fees": fee}},
    )
    # Owner earns the one-time deposit fee.
    await db.users.update_one({"_id": bank["owner_id"]}, {"$inc": {"balance": fee}})
    await add_bank_txn(uid, "bank_deposit", -amt, f"→ Bank: {bank['name']}")
    return True, fee


async def bank_withdraw(bank_id, uid: int, amt: int) -> tuple:
    """Customer withdraws up to their principal+accrued interest. Returns
    (ok, payout)."""
    if amt <= 0:
        return False, 0
    db = get_db()
    bank = await get_bank_info(bank_id)
    if not bank or not bank.get("active"):
        return False, 0
    uid_s = str(uid)
    dep = bank["deposits"].get(uid_s)
    if not dep:
        return False, 0
    available = dep["principal"] + dep["acc_int"]
    if available <= 0:
        return False, 0
    payout = min(amt, available)
    ratio = payout / available
    red_p = int(dep["principal"] * ratio)
    red_i = payout - red_p
    new_dep = {
        "principal": dep["principal"] - red_p,
        "acc_int": dep["acc_int"] - red_i,
        "last_acc_ts": dep.get("last_acc_ts", int(time.time())),
    }
    if new_dep["principal"] <= 0 and new_dep["acc_int"] <= 0:
        await db.banks.update_one({"_id": bank_id}, {"$unset": {f"deposits.{uid_s}": ""}})
    else:
        await db.banks.update_one({"_id": bank_id}, {"$set": {f"deposits.{uid_s}": new_dep}})
    await db.users.update_one({"_id": uid}, {"$inc": {"balance": payout}})
    await add_bank_txn(uid, "bank_withdraw", payout, f"← Bank: {bank['name']}")
    return True, payout


async def set_bank_profile(bank_id, owner_id: int, **fields) -> bool:
    bank = await get_bank_info(bank_id)
    if not bank or bank.get("owner_id") != owner_id or not bank.get("active"):
        return False
    upd = {}
    if "name" in fields and fields["name"]:
        upd["name"] = str(fields["name"])[:40]
    if "msg" in fields and fields["msg"] is not None:
        upd["msg"] = str(fields["msg"])[:300]
    if "rate" in fields and fields["rate"] is not None:
        r = float(fields["rate"])
        upd["rate"] = min(BANK_RATE_MAX, max(BANK_RATE_MIN, r))
    if upd:
        await get_db().banks.update_one({"_id": bank_id}, {"$set": upd})
        return True
    return False


async def close_bank(bank_id) -> dict:
    """Close a bank: return the owner's reserve and every customer's
    principal+interest, then deactivate. Returns a summary dict."""
    db = get_db()
    bank = await get_bank_info(bank_id)
    if not bank or not bank.get("active"):
        return {"ok": False}
    # Return owner reserve.
    await db.users.update_one(
        {"_id": bank["owner_id"]}, {"$inc": {"balance": bank["reserve"]}}
    )
    await add_bank_txn(bank["owner_id"], "bank_close", bank["reserve"],
                       f"Bank '{bank['name']}' closed — reserve returned")
    # Return each customer's principal+interest.
    returned = 0
    for uid_s, dep in (bank.get("deposits") or {}).items():
        amt = dep["principal"] + dep["acc_int"]
        if amt <= 0:
            continue
        try:
            uid = int(uid_s)
        except Exception:
            continue
        await db.users.update_one({"_id": uid}, {"$inc": {"balance": amt}})
        await add_bank_txn(uid, "bank_close", amt, f"Bank '{bank['name']}' closed")
        returned += amt
    await db.banks.update_one({"_id": bank_id}, {"$set": {"active": False}})
    return {"ok": True, "reserve": bank["reserve"], "returned": returned,
            "fees": bank.get("total_fees", 0)}


async def accrue_bank_customer_interest() -> int:
    """Credit daily interest to every bank customer's deposit. Returns the
    number of banks processed."""
    db = get_db()
    now = int(time.time())
    processed = 0
    async for bank in db.banks.find({"active": True, "deposits.0": {"$exists": True}}):
        changed = False
        for uid_s, dep in list((bank.get("deposits") or {}).items()):
            total = dep["principal"] + dep["acc_int"]
            if total <= 0:
                continue
            interest = int(total * bank.get("rate", BANK_CUSTOMER_DAILY_RATE))
            if interest <= 0:
                continue
            dep["acc_int"] = dep.get("acc_int", 0) + interest
            dep["last_acc_ts"] = now
            changed = True
        if changed:
            await db.banks.update_one(
                {"_id": bank["_id"]}, {"$set": {"deposits": bank["deposits"]}}
            )
            processed += 1
    return processed


async def get_lottery_pool(chat_id: int) -> int:
    doc = await get_db().lottery.find_one({"_id": chat_id})
    return (doc or {}).get("pool", 0)

async def add_to_lottery_pool(chat_id: int, amt: int):
    await get_db().lottery.update_one(
        {"_id": chat_id}, {"$inc": {"pool": amt}}, upsert=True
    )

async def reset_lottery_pool(chat_id: int):
    await get_db().lottery.update_one({"_id": chat_id}, {"$set": {"pool": 0}}, upsert=True)


# ── Admin notes (per-group key-value store) ───────────────────────────────────

async def set_note(chat_id: int, key: str, value: str):
    await get_db().notes.update_one(
        {"_id": f"{chat_id}:{key.lower()}"},
        {"$set": {"chat_id": chat_id, "key": key.lower(), "value": value}},
        upsert=True,
    )

async def get_note(chat_id: int, key: str) -> str | None:
    doc = await get_db().notes.find_one({"_id": f"{chat_id}:{key.lower()}"})
    return (doc or {}).get("value")

async def delete_note(chat_id: int, key: str) -> bool:
    r = await get_db().notes.delete_one({"_id": f"{chat_id}:{key.lower()}"})
    return r.deleted_count > 0

async def list_notes(chat_id: int) -> list[str]:
    docs = await get_db().notes.find({"chat_id": chat_id}, {"key": 1}).to_list(500)
    return [d["key"] for d in docs]

async def clear_notes(chat_id: int) -> int:
    r = await get_db().notes.delete_many({"chat_id": chat_id})
    return r.deleted_count


# ── Group warn limit setting ──────────────────────────────────────────────────

async def get_warn_limit(chat_id: int) -> int:
    doc = await get_db().group_settings.find_one({"_id": chat_id}, {"warn_limit": 1})
    return (doc or {}).get("warn_limit", 3)

async def set_warn_limit(chat_id: int, limit: int):
    await get_db().group_settings.update_one(
        {"_id": chat_id}, {"$set": {"warn_limit": limit}}, upsert=True
    )


# ── DM spam block ─────────────────────────────────────────────────────────────

async def set_spam_block(uid: int, until_ts: float):
    await get_db().spam_blocks.update_one(
        {"_id": uid}, {"$set": {"until_ts": until_ts}}, upsert=True
    )

async def get_spam_block(uid: int) -> float:
    """Returns until_ts (epoch seconds). 0 means not blocked."""
    doc = await get_db().spam_blocks.find_one({"_id": uid})
    return (doc or {}).get("until_ts", 0)

async def clear_spam_block(uid: int):
    await get_db().spam_blocks.delete_one({"_id": uid})


# ══════════════════════════════════════════════════════════════════════
# 🆕 Village: raid history log
# ══════════════════════════════════════════════════════════════════════

async def log_raid(attacker_id: int, defender_id: int, attacker_won: bool,
                    loot_wood: int = 0, loot_stone: int = 0, loot_iron: int = 0):
    await get_db().raid_log.insert_one({
        "attacker_id": attacker_id, "defender_id": defender_id,
        "attacker_won": attacker_won, "loot_wood": loot_wood,
        "loot_stone": loot_stone, "loot_iron": loot_iron, "ts": now(),
    })

async def get_raid_history(uid: int, limit: int = 5) -> list:
    """Recent raids where `uid` was either the attacker or defender."""
    cursor = get_db().raid_log.find(
        {"$or": [{"attacker_id": uid}, {"defender_id": uid}]}
    ).sort("ts", -1).limit(limit)
    return await cursor.to_list(limit)


# ══════════════════════════════════════════════════════════════════════
# 🆕 Admin Filters (keyword auto-responders)
# ══════════════════════════════════════════════════════════════════════

async def save_filter(cid: int, keyword: str, text: str,
                      file_id=None, ftype: str = "text"):
    """Create or overwrite a keyword filter for a chat. `keyword` is stored
    lower-cased so matching is case-insensitive."""
    kw = keyword.lower().strip()
    await get_db().filters.update_one(
        {"chat_id": cid, "keyword": kw},
        {"$set": {"chat_id": cid, "keyword": kw, "text": text,
                  "file_id": file_id, "ftype": ftype, "ts": now()}},
        upsert=True,
    )


async def get_filter(cid: int, keyword: str) -> dict | None:
    kw = keyword.lower().strip()
    return await get_db().filters.find_one({"chat_id": cid, "keyword": kw})


async def match_filter(cid: int, text: str) -> dict | None:
    """Return a filter whose keyword appears as a whole/partial token in
    `text`. Case-insensitive. Multi-word keywords are matched as a substring;
    single-word keywords are matched as a word boundary so 'bot' doesn't
    fire on 'robot'."""
    if not text:
        return None
    low = text.lower()
    doc = await get_db().filters.find_one({"chat_id": cid, "keyword": low})
    if doc:
        return doc
    # Fall back to token search for single-word filters
    words = set(re.split(r'\W+', low))
    words.discard("")
    if not words:
        return None
    async for f in get_db().filters.find({"chat_id": cid}):
        kw = f.get("keyword", "")
        if " " in kw:
            if kw in low:
                return f
        elif kw in words:
            return f
    return None


async def list_filters(cid: int) -> list:
    return await get_db().filters.find({"chat_id": cid}).to_list(1000)


async def delete_filter(cid: int, keyword: str) -> bool:
    kw = keyword.lower().strip()
    res = await get_db().filters.delete_one({"chat_id": cid, "keyword": kw})
    return res.deleted_count > 0


async def clear_filters(cid: int) -> int:
    res = await get_db().filters.delete_many({"chat_id": cid})
    return res.deleted_count


# ══════════════════════════════════════════════════════════════════════
# 🆕 Global Ban (GBAN) — owner-only network-wide ban
# ══════════════════════════════════════════════════════════════════════

async def add_gban(uid: int, reason: str, by: int) -> bool:
    res = await get_db().gban.update_one(
        {"_id": uid},
        {"$set": {"_id": uid, "reason": reason or "No reason given",
                  "by": by, "ts": now()}},
        upsert=True,
    )
    return res.upserted_id is not None or res.modified_count > 0


async def remove_gban(uid: int) -> bool:
    res = await get_db().gban.delete_one({"_id": uid})
    return res.deleted_count > 0


async def is_gbanned(uid: int) -> dict | None:
    return await get_db().gban.find_one({"_id": uid})


async def list_gban(limit: int = 50) -> list:
    return await get_db().gban.find().sort("ts", -1).to_list(limit)
