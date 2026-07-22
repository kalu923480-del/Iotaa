"""
Unit tests for the new power systems:
  * utils/xp — xp_level / xp_progress / add_xp / sync_level
  * utils/clans — create / join / leave / deposit
  * utils/events — start / stop / event_multiplier / expiry

Run: python -m unittest tests.test_power_systems -v   (from the iota/ folder)
"""
import os
import sys
import asyncio
import unittest
import time
import types
import re

HERE = os.path.dirname(os.path.abspath(__file__))
IOTA = os.path.dirname(HERE)
if IOTA not in sys.path:
    sys.path.insert(0, IOTA)

# Env so config_template-style imports work if config.py is generated
os.environ.setdefault("BOT_TOKEN", "123456:fake-test-token")
os.environ.setdefault("OWNER_ID", "111111")
os.environ.setdefault(
    "MONGO_URI",
    "mongodb+srv://test:test@cluster0.tjpjh4k.mongodb.net/iota_bot",
)

# Stub config BEFORE importing utils that pull XP/clan constants
_orig_config = sys.modules.get("config")
sys.modules["config"] = types.SimpleNamespace(
    BOT_TOKEN="123456:fake",
    OWNER_ID=111111,
    MONGO_URI="mongodb://fake",
    DB_NAME="test",
    XP_PER_LEVEL=1000,
    LEVEL_UP_COIN_BASE=200,
    CLAN_CREATE_COST=5_000,
    CLAN_MAX_MEMBERS=20,
    CLAN_NAME_MIN=3,
    CLAN_NAME_MAX=24,
    CLAN_TAG_MIN=2,
    CLAN_TAG_MAX=5,
    EVENT_DEFAULT_HOURS=6,
    XP_KILL_NORMAL=(0, 5),
    XP_KILL_PREMIUM=(10, 20),
    XP_ROB_PER_1K=1,
)


def _match_query(d, q):
    if not q:
        return True
    for k, v in q.items():
        if k.startswith("$"):
            continue
        if isinstance(v, dict):
            cur = d.get(k)
            if "$regex" in v:
                flags = re.I if v.get("$options") == "i" else 0
                if not re.search(v["$regex"], str(cur or ""), flags):
                    return False
            if "$gt" in v and not ((cur if cur is not None else 0) > v["$gt"]):
                return False
            if "$lt" in v and not ((cur if cur is not None else 0) < v["$lt"]):
                return False
            if "$lte" in v and not ((cur if cur is not None else 0) <= v["$lte"]):
                return False
            if "$gte" in v and not ((cur if cur is not None else 0) >= v["$gte"]):
                return False
            if "$ne" in v and cur == v["$ne"]:
                return False
        else:
            if d.get(k) != v:
                return False
    return True


class _FakeColl:
    def __init__(self):
        self.docs = {}
        self._last_q = None
        self._sort = None
        self._limit = None

    async def find_one(self, q=None, *args, **kwargs):
        q = q or {}
        if "_id" in q and len(q) == 1:
            return self.docs.get(q["_id"])
        for d in self.docs.values():
            if _match_query(d, q):
                return dict(d)
        return None

    def find(self, q=None, **kwargs):
        self._last_q = q or {}
        self._sort = kwargs.get("sort")
        self._limit = kwargs.get("limit")
        return self

    def sort(self, *args, **kwargs):
        # Motor accepts .sort([("xp", -1)]) or .sort("xp", -1)
        if len(args) == 2 and not isinstance(args[0], (list, tuple)):
            self._sort = [(args[0], args[1])]
        elif args:
            self._sort = args[0]
        return self

    def limit(self, n):
        self._limit = n
        return self

    async def to_list(self, n=0):
        items = [dict(d) for d in self.docs.values() if _match_query(d, self._last_q)]
        if self._sort:
            for key, direction in reversed(list(self._sort)):
                items.sort(key=lambda d, k=key: d.get(k, 0) or 0, reverse=(direction < 0))
        lim = n if n and n > 0 else self._limit
        if lim and lim > 0:
            return items[:lim]
        return items

    async def insert_one(self, doc):
        self.docs[doc["_id"]] = dict(doc)
        return type("R", (), {"inserted_id": doc["_id"]})()

    async def update_one(self, q, upd, upsert=False):
        d = None
        if "_id" in q:
            d = self.docs.get(q["_id"])
        if d is None:
            for doc in self.docs.values():
                if _match_query(doc, q):
                    d = doc
                    break
        matched = 1 if d is not None else 0
        if d is None:
            if not upsert:
                return type("R", (), {"matched_count": 0, "modified_count": 0})()
            d = {"_id": q.get("_id")}
            self.docs[d["_id"]] = d
            matched = 1
        for k, v in upd.get("$set", {}).items():
            d[k] = v
        for k, v in upd.get("$inc", {}).items():
            d[k] = d.get(k, 0) + v
        for k, v in upd.get("$unset", {}).items():
            d.pop(k, None)
        return type("R", (), {"matched_count": matched, "modified_count": 1})()

    async def update_many(self, q, upd):
        n = 0
        for d in list(self.docs.values()):
            if _match_query(d, q or {}):
                for k, v in upd.get("$set", {}).items():
                    d[k] = v
                for k, v in upd.get("$inc", {}).items():
                    d[k] = d.get(k, 0) + v
                n += 1
        return type("R", (), {"matched_count": n, "modified_count": n})()

    async def delete_one(self, q):
        if "_id" in q and q["_id"] in self.docs:
            del self.docs[q["_id"]]
            return type("R", (), {"deleted_count": 1})()
        for _id, d in list(self.docs.items()):
            if _match_query(d, q):
                del self.docs[_id]
                return type("R", (), {"deleted_count": 1})()
        return type("R", (), {"deleted_count": 0})()

    async def count_documents(self, q=None):
        return sum(1 for d in self.docs.values() if _match_query(d, q or {}))


class _FakeDB:
    def __init__(self):
        self.users = _FakeColl()
        self.clans = _FakeColl()
        self.global_events = _FakeColl()

    def __getitem__(self, key):
        return getattr(self, key)


_fake = _FakeDB()


def _patch_modules():
    import utils.mongo_db as mdb
    mdb.get_db = lambda: _fake
    # Re-bind helpers that closed over nothing but call get_db at runtime — OK
    import utils.xp as xp_mod
    if hasattr(xp_mod, "get_db"):
        xp_mod.get_db = lambda: _fake
    import utils.events as ev_mod
    if hasattr(ev_mod, "get_db"):
        ev_mod.get_db = lambda: _fake
    import utils.clans as cl_mod
    if hasattr(cl_mod, "get_db"):
        cl_mod.get_db = lambda: _fake


def run(coro):
    return asyncio.run(coro)


class TestXP(unittest.TestCase):
    def setUp(self):
        _fake.users.docs.clear()
        _fake.clans.docs.clear()
        _fake.global_events.docs.clear()
        _patch_modules()

    def _seed_user(self, uid=1, xp=0, level=1, balance=1000):
        _fake.users.docs[uid] = {
            "_id": uid,
            "username": "test",
            "full_name": "Test",
            "balance": balance,
            "xp": xp,
            "level": level,
            "is_banned": False,
            "clan_id": "",
        }

    def test_xp_level_math(self):
        from utils.helpers import xp_level
        from config import XP_PER_LEVEL
        self.assertEqual(xp_level(0), 1)
        self.assertEqual(xp_level(XP_PER_LEVEL - 1), 1)
        self.assertEqual(xp_level(XP_PER_LEVEL), 2)
        self.assertEqual(xp_level(XP_PER_LEVEL + 1), 2)

    def test_xp_progress(self):
        from utils.helpers import xp_progress
        from config import XP_PER_LEVEL
        lv, into, needed = xp_progress(0)
        self.assertEqual(lv, 1)
        self.assertEqual(into, 0)
        self.assertEqual(needed, XP_PER_LEVEL)

    def test_add_xp_level_up(self):
        from utils.xp import add_xp
        from config import LEVEL_UP_COIN_BASE
        self._seed_user(uid=1, xp=0, level=1, balance=1000)
        res = run(add_xp(1, 1000, "test"))
        self.assertTrue(res["leveled_up"])
        self.assertEqual(res["levels_gained"], 1)
        self.assertEqual(res["level"], 2)
        expected_reward = LEVEL_UP_COIN_BASE * 2
        self.assertEqual(res["reward"], expected_reward)
        # level-up coins credited
        self.assertEqual(_fake.users.docs[1]["balance"], 1000 + expected_reward)
        self.assertEqual(_fake.users.docs[1]["xp"], 1000)
        self.assertEqual(_fake.users.docs[1]["level"], 2)

    def test_add_xp_no_level_up(self):
        from utils.xp import add_xp
        self._seed_user(uid=2, xp=100, level=1)
        res = run(add_xp(2, 50, "test"))
        self.assertFalse(res["leveled_up"])
        self.assertEqual(res["levels_gained"], 0)
        self.assertEqual(res["level"], 1)
        self.assertEqual(res["xp"], 150)

    def test_sync_level(self):
        from utils.xp import sync_level
        # xp_level(3000): lv1 needs 1000, lv2 needs 2000 → total 3000 → level 3
        self._seed_user(uid=3, xp=3000, level=1)
        lv = run(sync_level(3))
        self.assertEqual(lv, 3)
        self.assertEqual(_fake.users.docs[3]["level"], 3)


class TestClans(unittest.TestCase):
    def setUp(self):
        _fake.users.docs.clear()
        _fake.clans.docs.clear()
        _fake.global_events.docs.clear()
        _patch_modules()

    def _seed_user(self, uid=1, balance=10000, clan_id=""):
        _fake.users.docs[uid] = {
            "_id": uid,
            "username": f"user{uid}",
            "full_name": f"User {uid}",
            "balance": balance,
            "clan_id": clan_id,
            "is_banned": False,
            "xp": 0,
            "level": 1,
        }

    def test_create_clan(self):
        from utils.clans import create_clan
        self._seed_user(uid=1, balance=10000)
        ok, msg, doc = run(create_clan(1, "Test Clan", "TEST"))
        self.assertTrue(ok, msg)
        self.assertIn("TEST", msg)
        self.assertIsNotNone(doc)
        self.assertEqual(_fake.users.docs[1]["clan_id"], doc["_id"])
        self.assertEqual(_fake.users.docs[1]["balance"], 10000 - 5000)

    def test_create_clan_no_balance(self):
        from utils.clans import create_clan
        self._seed_user(uid=2, balance=100)
        ok, msg, doc = run(create_clan(2, "Poor Clan", "POOR"))
        self.assertFalse(ok)
        self.assertIsNone(doc)

    def test_join_and_leave(self):
        from utils.clans import create_clan, join_clan, leave_clan, get_user_clan
        self._seed_user(uid=1, balance=10000)
        self._seed_user(uid=2, balance=10000)
        ok, _, doc = run(create_clan(1, "Alpha", "ALPHA"))
        self.assertTrue(ok)
        ok, msg = run(join_clan(2, doc["_id"]))
        self.assertTrue(ok, msg)
        clan = run(get_user_clan(2))
        self.assertIsNotNone(clan)
        ok, msg = run(leave_clan(2))
        self.assertTrue(ok, msg)
        clan = run(get_user_clan(2))
        self.assertIsNone(clan)

    def test_deposit(self):
        from utils.clans import create_clan, join_clan, deposit_to_clan, get_clan
        self._seed_user(uid=1, balance=10000)
        self._seed_user(uid=2, balance=5000)
        ok, _, doc = run(create_clan(1, "Beta", "BETA"))
        self.assertTrue(ok)
        run(join_clan(2, doc["_id"]))
        ok, msg = run(deposit_to_clan(2, 1000))
        self.assertTrue(ok, msg)
        c = run(get_clan(doc["_id"]))
        self.assertEqual(c["bank"], 1000)
        self.assertEqual(_fake.users.docs[2]["balance"], 4000)


class TestEvents(unittest.TestCase):
    def setUp(self):
        _fake.users.docs.clear()
        _fake.clans.docs.clear()
        _fake.global_events.docs.clear()
        _patch_modules()

    def test_start_and_multiplier(self):
        from utils.events import start_event, event_multiplier, get_active_events
        ok, msg, doc = run(start_event("xp_boost", 2, 1))
        self.assertTrue(ok, msg)
        events = run(get_active_events())
        self.assertEqual(len(events), 1)
        mult = run(event_multiplier("xp", 1.0))
        self.assertEqual(mult, 2.0)

    def test_event_expiry(self):
        from utils.events import start_event, get_active_events
        ok, _, doc = run(start_event("xp_boost", 0.0005, 1))  # ~1.8s
        self.assertTrue(ok)
        time.sleep(2.2)
        events = run(get_active_events())
        self.assertEqual(len(events), 0)

    def test_stop_event(self):
        from utils.events import start_event, stop_event, event_multiplier
        ok, _, _ = run(start_event("xp_boost", 2, 1))
        self.assertTrue(ok)
        ok, msg = run(stop_event("xp_boost"))
        self.assertTrue(ok)
        self.assertIn("stopped", msg.lower())
        mult = run(event_multiplier("xp", 1.0))
        self.assertEqual(mult, 1.0)

    def test_lucky_rob_multiplier(self):
        from utils.events import start_event, event_multiplier
        ok, _, _ = run(start_event("lucky_rob", 1, 1))
        self.assertTrue(ok)
        mult = run(event_multiplier("rob_tax", 1.0))
        self.assertEqual(mult, 0.5)


# Keep stub config for this module; other test files restore their own.
# If a real config existed, put it back so discover order is safer.
if _orig_config is not None:
    # leave our stub — XP constants required; real config may lack new keys
    pass


if __name__ == "__main__":
    unittest.main()
