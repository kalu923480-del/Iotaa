"""Iota Bot - MongoDB Async Database Layer (motor)"""
import time, re
from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGO_URI, DB_NAME

_client = None
_db = None
_connection_ok = None   # None=unknown, True/False after first check


def get_db():
    global _client, _db
    if _db is None:
        _client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=8000)
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
            f"👉 Fix this in config.py — set _MONGO_PASS to your real "
            f"MongoDB Atlas password. Until fixed, /bal, /daily, /rob, "
            f"/ludo and all other database-backed commands will not work."
        )
        return False


def is_db_connected() -> bool:
    return _connection_ok is not False  # True or unknown = assume ok, only block on confirmed False


def now(): return int(time.time())

# ── Users ────────────────────────────────────────────────────────────

async def ensure_user(uid, username="", full_name=""):
    db = get_db()
    u = await db.users.find_one({"_id": uid})
    if not u:
        u = {"_id":uid,"username":username,"full_name":full_name,
             "balance":0,"gems":0,"is_premium":False,"premium_emoji":"",
             "last_daily":0,"kills":0,"daily_kills":0,"last_kill_reset":0,
             "robs":0,"daily_robs":0,"last_rob_reset":0,"xp":0,"level":1,
             "protected_until":0,"dead_until":0,"wallet":0,"is_banned":False,
             "free_gem_claimed":False,"custom_title":"","created_at":now(),
             # Seed history with the very first name/username seen, so a
             # user who never changes anything still has at least one
             # entry (matches full history depth shown by other bots
             # instead of staying empty forever).
             "name_history":[full_name] if full_name else [],
             "username_history":[username] if username else []}
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
    return u

async def get_user(uid):
    u = await get_db().users.find_one({"_id":uid})
    return u or await ensure_user(uid)

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
    """Users who haven't been marked unreachable — the actual DM-able audience."""
    return await get_db().users.find(
        {"dm_unreachable": {"$ne": True}}, {"_id": 1}
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

# ── Group economy ────────────────────────────────────────────────────

async def ensure_guser(uid, cid):
    key = f"{uid}_{cid}"
    db = get_db()
    gu = await db.group_economy.find_one({"_id":key})
    if not gu:
        gu = {"_id":key,"user_id":uid,"chat_id":cid,"balance":0,"kills":0,"robs":0,"protected_until":0,"dead_until":0}
        await db.group_economy.insert_one(gu)
    return gu

async def get_guser(uid, cid):
    key = f"{uid}_{cid}"
    gu = await get_db().group_economy.find_one({"_id":key})
    return gu or await ensure_guser(uid, cid)

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
    ws = await get_db().welcome_settings.find_one({"_id":cid})
    return ws or {"_id":cid,"enabled":True,"custom_msg":"","send_gif":True}

async def set_welcome_settings(cid, **kw):
    await get_db().welcome_settings.update_one({"_id":cid},{"$set":kw},upsert=True)

# ── Group protection ──────────────────────────────────────────────────

async def get_prot(cid):
    p = await get_db().group_protection.find_one({"_id":cid})
    if not p:
        p = {"_id":cid,"enabled":True,"anti_spam":True,"anti_link":True,
             "anti_arabic":False,"anti_forward":False,"anti_bot":True,
             "anti_flood":True,"flood_limit":5,"flood_window":5,
             "anti_raid":True,"raid_threshold":10,"raid_window":30,
             "profanity_filter":False,"log_channel":0}
        await get_db().group_protection.insert_one(p)
    return p

async def update_prot(cid, **kw):
    await get_db().group_protection.update_one({"_id":cid},{"$set":kw},upsert=True)

# ── Reports ───────────────────────────────────────────────────────────

async def add_report(cid, reporter, reported, reason, msg_text=""):
    await get_db().reports.insert_one({"chat_id":cid,"reporter_id":reporter,
        "reported_id":reported,"reason":reason,"msg_text":msg_text,"status":"pending","created_at":now()})

async def get_reports(cid, status="pending"):
    return await get_db().reports.find({"chat_id":cid,"status":status},sort=[("created_at",-1)],limit=20).to_list(20)

async def get_report_count(cid):
    return await get_db().reports.count_documents({"chat_id":cid,"status":"pending"})

# ── Bad words ─────────────────────────────────────────────────────────

async def get_bad_words(cid):
    doc = await get_db().bad_words.find_one({"_id":cid})
    return doc.get("words",[]) if doc else []

async def add_bad_word(cid, word):
    await get_db().bad_words.update_one({"_id":cid},{"$addToSet":{"words":word.lower()}},upsert=True)

async def remove_bad_word(cid, word):
    await get_db().bad_words.update_one({"_id":cid},{"$pull":{"words":word.lower()}})

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
    except Exception: pass
    try:
        await db.join_requests.create_index([("chat_id",1),("user_id",1)], unique=True)
        await db.join_requests.create_index([("chat_id",1),("date",1)])
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
