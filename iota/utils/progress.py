"""
Iota Bot — Progress System (Achievements / Daily Challenge / Quests / Stats)

Self-contained, MongoDB-backed. Every function degrades safely (no throws
escape to the caller) and reads/writes a single `user_progress` doc per user.

Game handlers feed the system via `record_game_result()` so achievements
and quests unlock automatically as people play. The system is additive —
nothing else in the bot is required for it to work, and it never blocks a
game flow (callers wrap calls in try/except per utils/game_art's contract).
"""
import time
from utils.mongo_db import get_db, now, ensure_user


# ── Achievement catalogue ────────────────────────────────────────────────
# `check(c)` receives the user's counter dict and returns True when earned.
ACHIEVEMENTS = [
    ("first_win",      "🥇", "Pᴇʜʟᴀ Jᴇᴇᴛ",       "Pehli game jeeti",        lambda c: c.get("games_won", 0) >= 1),
    ("gamer10",        "🎮", "Gᴀᴍᴇʀ",          "10 games khele",          lambda c: c.get("games_played", 0) >= 10),
    ("gamer50",        "👾", "Gᴀᴍᴇ Mᴀsᴛᴇʀ", "50 games khele",          lambda c: c.get("games_played", 0) >= 50),
    ("streak3",        "🔥", "Sᴛʀᴇᴀᴋ",          "3 games ek saath jeete",  lambda c: c.get("best_streak", 0) >= 3),
    ("high_roller",    "💎", "Hɪɢʜ Rᴏʟʟᴇʀ",  "10k+ ek bet lagaya",      lambda c: c.get("max_single_bet", 0) >= 10_000),
    ("whale",          "🐋", "Wʜᴀʟᴇ",           "1L+ balance touch kiya",  lambda c: c.get("peak_balance", 0) >= 100_000),
    ("collector",      "🎁", "Cᴏʟʟᴇᴄᴛᴏʀ",     "5 items collect kiye",   lambda c: c.get("items_owned", 0) >= 5),
    ("social",         "💞", "Sᴏᴄɪᴀʟ",         "Shaadi kar li! 💍",       lambda c: c.get("marriages", 0) >= 1),
    ("quiz_ace",       "🧠", "Qᴜɪᴢ Aᴄᴇ",      "10 quiz sahi kare",      lambda c: c.get("quiz_correct", 0) >= 10),
    ("daily7",         "📅", "Dᴀɪʟʏ",          "7 din daily challenge",  lambda c: c.get("daily_streak", 0) >= 7),
    ("richie",         "🤑", "Rɪᴄʜɪᴇ",         "10L+ net worth",         lambda c: c.get("peak_networth", 0) >= 1_000_000),
    ("villager",       "🏰", "Vɪʟʟᴀɢᴇʀ",       "Kingdom banaya",         lambda c: c.get("kingdom_built", 0) >= 1),
]

ACH_BY_KEY = {a[0]: a for a in ACHIEVEMENTS}

# ── Daily challenge pool ─────────────────────────────────────────────────
DAILY_CHALLENGES = [
    ("win1",    "1 game jeeto",                 300),
    ("wager5k", "5k coins lagao games mein",    400),
    ("play3",   "3 alag games khelo",           500),
    ("quiz5",   "5 quiz sahi karo",             350),
    ("win_streak2", "2 games lagatar jeeto",    600),
]

# ── Quest pool (3 random quests per day) ─────────────────────────────────
QUEST_POOL = [
    ("games2",    "2 games khelo",            200),
    ("wager1k",   "1k coins wager karo",       150),
    ("quiz3",     "3 quiz sahi karo",          150),
    ("bomb1",     "1 bomb game khelo",         150),
    ("daily_spin","1 baar /wheel spin karo",   150),
    ("roulette1", "1 roulette game khelo",     250),
]

DAILY_REWARD = 250  # claim reward for finishing the daily challenge
QUEST_REWARD = 100   # bonus per completed quest


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(now()))


async def ensure_progress(uid: int) -> dict:
    db = get_db()
    doc = await db.user_progress.find_one({"_id": uid})
    if not doc:
        doc = {
            "_id": uid,
            "counters": {},
            "achievements": {},
            "daily": {"date": "", "challenge": None, "completed": False},
            "quests": {"date": "", "items": []},
            "updated_at": now(),
        }
        await db.user_progress.insert_one(doc)
    return doc


async def get_progress(uid: int) -> dict:
    return await ensure_progress(uid)


def _inc(doc: dict, key: str, amount: int = 1) -> int:
    c = doc.setdefault("counters", {})
    c[key] = c.get(key, 0) + amount
    return c[key]


async def record_game_result(uid: int, won: bool, bet: int = 0,
                             game: str = "", quiz_correct: int = 0):
    """Central hook called by game handlers. Updates counters + checks
    achievements. Never raises (best-effort). Returns list of newly
    unlocked achievement keys (for optional notification)."""
    try:
        doc = await ensure_progress(uid)
        c = doc["counters"]
        _inc(doc, "games_played")
        if game:
            played = doc.setdefault("games_played_set", {})
            played[game] = played.get(game, 0) + 1
        if won:
            _inc(doc, "games_won")
            _inc(doc, "streak")
            if c.get("streak", 0) > c.get("best_streak", 0):
                c["best_streak"] = c["streak"]
        else:
            c["streak"] = 0
        if bet > 0:
            _inc(doc, "total_wagered", bet)
            if bet > c.get("max_single_bet", 0):
                c["max_single_bet"] = bet
        if quiz_correct:
            _inc(doc, "quiz_correct", quiz_correct)

        # refresh daily challenge progress
        await _refresh_daily(doc, won=won, bet=bet, game=game,
                             quiz_correct=quiz_correct)

        # check achievements
        newly = []
        for key, _icon, _name, _desc, check in ACHIEVEMENTS:
            if key not in doc["achievements"] and check(c):
                doc["achievements"][key] = now()
                newly.append(key)

        doc["updated_at"] = now()
        await get_db().user_progress.update_one(
            {"_id": uid},
            {"$set": {"counters": c, "achievements": doc["achievements"],
                      "daily": doc["daily"], "quests": doc["quests"],
                      "games_played_set": doc.get("games_played_set", {}),
                      "updated_at": now()}},
        )
        return newly
    except Exception:
        return []


async def _refresh_daily(doc: dict, won: bool, bet: int, game: str,
                         quiz_correct: int):
    d = doc["daily"]
    today = _today()
    if d.get("date") != today:
        return
    ch = d.get("challenge")
    if not ch or d.get("completed"):
        return
    prog = d.setdefault("progress", {})
    if ch == "win1" and won:
        prog["win1"] = prog.get("win1", 0) + 1
    if ch == "wager5k":
        prog["wager5k"] = prog.get("wager5k", 0) + bet
    if ch == "play3":
        played = doc.get("games_played_set", {})
        prog["play3"] = len(played)
    if ch == "quiz5":
        prog["quiz5"] = prog.get("quiz5", 0) + quiz_correct
    if ch == "win_streak2" and won:
        prog["win_streak2"] = prog.get("win_streak2", 0) + 1
    done = False
    if ch == "win1":        done = prog.get("win1", 0) >= 1
    if ch == "wager5k":     done = prog.get("wager5k", 0) >= 5000
    if ch == "play3":       done = prog.get("play3", 0) >= 3
    if ch == "quiz5":       done = prog.get("quiz5", 0) >= 5
    if ch == "win_streak2": done = prog.get("win_streak2", 0) >= 2
    if done:
        d["completed"] = True
    doc["daily"] = d


async def get_or_init_daily(uid: int) -> dict:
    """Roll a fresh daily challenge + quests if the date changed."""
    doc = await ensure_progress(uid)
    today = _today()
    import random
    if doc["daily"].get("date") != today:
        ch = random.choice(DAILY_CHALLENGES)
        doc["daily"] = {"date": today, "challenge": ch[0],
                        "completed": False, "progress": {}, "claimed": False}
        # 3 distinct quests
        pool = list(QUEST_POOL)
        random.shuffle(pool)
        qitems = []
        for q in pool[:3]:
            qitems.append({"id": q[0], "done": False, "reward": q[2]})
        doc["quests"] = {"date": today, "items": qitems}
        await get_db().user_progress.update_one(
            {"_id": uid},
            {"$set": {"daily": doc["daily"], "quests": doc["quests"]}}
        )
    return doc


async def complete_quest(uid: int, qid: str) -> int:
    """Mark a quest done (called by game hooks) and return its reward if
    it just completed, else 0."""
    try:
        doc = await get_or_init_daily(uid)
        for q in doc["quests"]["items"]:
            if q["id"] == qid and not q["done"]:
                q["done"] = True
                await get_db().user_progress.update_one(
                    {"_id": uid}, {"$set": {"quests": doc["quests"]}}
                )
                return q["reward"]
    except Exception:
        pass
    return 0


async def claim_daily(uid: int) -> tuple[bool, int]:
    """Claim the daily-challenge reward once per day. Returns
    (already_claimed_or_not_done, amount)."""
    doc = await get_or_init_daily(uid)
    d = doc["daily"]
    if not d.get("completed"):
        return (False, 0)
    if d.get("claimed"):
        return (True, 0)
    d["claimed"] = True
    await get_db().user_progress.update_one(
        {"_id": uid}, {"$set": {"daily": d}}
    )
    return (False, DAILY_REWARD)


async def daily_progress_text(uid: int) -> str:
    doc = await get_or_init_daily(uid)
    d = doc["daily"]
    ch = next((c for c in DAILY_CHALLENGES if c[0] == d.get("challenge")), None)
    if not ch:
        return "—"
    prog = d.get("progress", {})
    cur = 0
    if ch[0] == "win1":        cur = prog.get("win1", 0)
    if ch[0] == "wager5k":     cur = prog.get("wager5k", 0)
    if ch[0] == "play3":       cur = prog.get("play3", 0)
    if ch[0] == "quiz5":       cur = prog.get("quiz5", 0)
    if ch[0] == "win_streak2": cur = prog.get("win_streak2", 0)
    status = "✅" if d.get("completed") else "⏳"
    return f"{status} {ch[1]}  ({cur})\n   💰 Reward: {DAILY_REWARD}"


async def achievements_list(uid: int) -> list:
    """Returns list of (key, icon, name, desc, unlocked, unlocked_at)."""
    doc = await ensure_progress(uid)
    out = []
    for key, icon, name, desc, _check in ACHIEVEMENTS:
        ts = doc["achievements"].get(key)
        out.append((key, icon, name, desc, ts is not None, ts))
    return out


async def global_achievement_leaders(limit: int = 10) -> list:
    """Rank users by number of achievements unlocked (cross-group)."""
    db = get_db()
    docs = await db.user_progress.find({}).to_list(100000)
    scored = []
    for d in docs:
        ach = d.get("achievements", {})
        if ach:
            scored.append((d["_id"], len(ach)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]


async def get_stats(uid: int) -> dict:
    doc = await ensure_progress(uid)
    c = doc.get("counters", {})
    return {
        "games_played": c.get("games_played", 0),
        "games_won": c.get("games_won", 0),
        "best_streak": c.get("best_streak", 0),
        "total_wagered": c.get("total_wagered", 0),
        "max_single_bet": c.get("max_single_bet", 0),
        "quiz_correct": c.get("quiz_correct", 0),
        "achievements": len(doc.get("achievements", {})),
        "marriages": c.get("marriages", 0),
        "items_owned": c.get("items_owned", 0),
    }


async def sync_profile_counters(uid: int) -> dict:
    """Pull live balances/items into the counter doc so balance-based
    achievements (whale/richie/collector) can be evaluated on demand."""
    doc = await ensure_progress(uid)
    c = doc.setdefault("counters", {})
    try:
        from utils.mongo_db import get_items, get_user
        items = await get_items(uid)
        c["items_owned"] = sum(i.get("quantity", 1) for i in items)
        u = await get_user(uid)
        bal = (u.get("balance", 0) or 0) + (u.get("wallet", 0) or 0) \
              + (u.get("bank", 0) or 0) + (u.get("gems", 0) or 0) * 10
        if bal > c.get("peak_balance", 0):
            c["peak_balance"] = bal
        if bal > c.get("peak_networth", 0):
            c["peak_networth"] = bal
        await get_db().user_progress.update_one(
            {"_id": uid}, {"$set": {"counters": c}}
        )
    except Exception:
        pass
    return doc


async def recheck_achievements(uid: int) -> list:
    """Re-evaluate every achievement against current counters (after a
    sync). Returns newly unlocked keys."""
    doc = await sync_profile_counters(uid)
    c = doc["counters"]
    newly = []
    for key, _icon, _name, _desc, check in ACHIEVEMENTS:
        if key not in doc["achievements"] and check(c):
            doc["achievements"][key] = now()
            newly.append(key)
    if newly:
        await get_db().user_progress.update_one(
            {"_id": uid}, {"$set": {"achievements": doc["achievements"]}}
        )
    return newly
