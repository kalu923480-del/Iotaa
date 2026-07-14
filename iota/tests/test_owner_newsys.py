"""
Tests for the new Owner Systems module (handlers/owner_newsys.py).

These guard against:
  * import/registration mismatches (importing bot.py executes the whole
    owner-systems import block — a typo'd function name fails loudly),
  * the scheduling time parser (_parse_when) accepting/rejecting correctly,
  * the owner DB helpers (sudo, scheduled jobs, blackwords, watchlist)
    behaving with a fake MongoDB,
  * a couple of end-to-end owner commands not crashing.
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch, AsyncMock

HERE = os.path.dirname(os.path.abspath(__file__))
IOTA = os.path.dirname(HERE)
if IOTA not in sys.path:
    sys.path.insert(0, IOTA)

os.environ.setdefault("BOT_TOKEN", "123456:fake-test-token")
os.environ.setdefault("OWNER_ID", "111111")
os.environ.setdefault(
    "MONGO_URI", "mongodb+srv://test:test@cluster0.tjpjh4k.mongodb.net/iota_bot"
)


def _run(coro):
    return asyncio.run(coro)


# ── Minimal fake MongoDB (dict-backed, just enough for the helpers) ────────

class _FakeColl:
    def __init__(self):
        self.docs = {}

    async def find_one(self, q, *_a, **_k):
        _id = q.get("_id")
        return self.docs.get(_id)

    def find(self, q=None, *_a, **_k):
        class _Cur:
            def __init__(self, items):
                self.items = items

            async def to_list(self, n):
                return self.items[:n]
        items = list(self.docs.values())
        if q:
            out = []
            for d in items:
                ok = True
                for k, v in q.items():
                    if isinstance(v, dict) and "$ne" in v:
                        if d.get(k) == v["$ne"]:
                            ok = False
                            break
                    elif d.get(k) != v:
                        ok = False
                        break
                if ok:
                    out.append(d)
            items = out
        return _Cur(items)

    async def insert_one(self, doc):
        _id = doc.get("_id")
        self.docs[_id] = dict(doc)

    async def update_one(self, q, update, upsert=False):
        _id = q.get("_id")
        d = self.docs.get(_id)
        modified = 0
        if d is None:
            if upsert:
                d = {"_id": _id}
                self.docs[_id] = d
                modified = 1
            else:
                return _mk(0)
        if "$set" in update:
            d.update(update["$set"])
            modified = 1
        if "$setOnInsert" in update:
            for k, v in update["$setOnInsert"].items():
                d.setdefault(k, v)
        return _mk(modified)

    async def update_many(self, q, update, upsert=False):
        class _R:
            modified_count = 0
        for d in self.docs.values():
            if "$set" in update:
                d.update(update["$set"])
                _R.modified_count += 1
        return _R()

    async def delete_one(self, q):
        _id = q.get("_id")
        if _id in self.docs:
            del self.docs[_id]
            return _mk(1)
        return _mk(0)

    async def delete_many(self, q):
        before = len(self.docs)
        self.docs.clear()
        return _mk(before)

    async def count_documents(self, q=None):
        return len(self.docs)

    async def aggregate(self, *a, **k):
        class _Cur:
            async def to_list(self, n):
                return []
        return _Cur()


def _mk(n):
    class _R:
        pass
    r = _R()
    r.deleted_count = n
    r.modified_count = n
    return r


class _FakeDB:
    def __init__(self):
        self.bot_config = _FakeColl()
        self.scheduled_jobs = _FakeColl()
        self.autoreplies = _FakeColl()
        self.blackwords = _FakeColl()
        self.watchlist = _FakeColl()
        self.users = _FakeColl()
        self.group_settings = _FakeColl()
        self.welcome_settings = _FakeColl()
        self.command_usage = _FakeColl()
        self.error_log = _FakeColl()

    async def command(self, *a, **k):
        return {"ok": True}

    async def list_collection_names(self):
        return [k for k in vars(self) if not k.startswith("_")]


class TestOwnerSystemsImports(unittest.TestCase):
    def test_bot_imports_all_new_commands(self):
        # Importing bot.py runs the owner-systems import block; a typo'd
        # function name would raise ImportError here.
        import bot  # noqa: F401

    def test_all_functions_exist(self):
        import handlers.owner_newsys as m
        names = [
            "lockdown_cmd", "unlock_cmd", "slowall_cmd", "lockall_cmd",
            "unlockall_cmd", "shieldstatus_cmd", "massban_cmd", "massunban_cmd",
            "banfrom_cmd", "unbanfrom_cmd", "cleanbots_cmd", "botgate_cmd",
            "allowedbots_cmd", "watchuser_cmd", "unwatch_cmd", "watchlist_cmd",
            "suslist_cmd", "schedbroadcast_cmd", "schedmsg_cmd", "remindall_cmd",
            "scheds_cmd", "cancelsched_cmd", "autoreply_cmd", "autoreplies_cmd",
            "delautoreply_cmd", "blackword_cmd", "blackwords_cmd", "delblackword_cmd",
            "growth_cmd", "retention_cmd", "latency_cmd", "health_cmd",
            "pingall_cmd", "deadgroups_cmd", "online_cmd", "commandstats_cmd",
            "errorlog_cmd", "sudoadd_cmd", "sudoremove_cmd", "stafflist_cmd",
            "handover_cmd", "whereis_cmd", "common_cmd", "economystats_cmd",
            "rain_cmd", "reseteco_cmd", "dbstats_cmd",
            "exportcsv_cmd", "backup_cmd", "vacuum_cmd", "indexes_cmd",
            "persona_cmd", "defaultwelcome_cmd", "forcewelcome_cmd", "botbio_cmd",
            "setmenu_cmd", "logchat_cmd", "notify_cmd", "alert_cmd", "ownersys_cmd",
        ]
        for n in names:
            self.assertTrue(callable(getattr(m, n)), f"{n} missing/callable")


class TestParseWhen(unittest.TestCase):
    def test_relative(self):
        import handlers.owner_newsys as m
        now = int(__import__("time").time())
        self.assertAlmostEqual(m._parse_when("+30m"), now + 30 * 60, delta=2)
        self.assertAlmostEqual(m._parse_when("2h"), now + 2 * 3600, delta=2)
        self.assertAlmostEqual(m._parse_when("3d"), now + 3 * 86400, delta=2)

    def test_absolute(self):
        import handlers.owner_newsys as m
        self.assertEqual(m._parse_when("1710000000"), 1710000000)

    def test_invalid(self):
        import handlers.owner_newsys as m
        self.assertIsNone(m._parse_when(""))
        self.assertIsNone(m._parse_when("soon"))
        self.assertIsNone(m._parse_when("abc"))


class TestOwnerHelpers(unittest.TestCase):
    def setUp(self):
        self.db = _FakeDB()
        self.patcher = patch("utils.mongo_db.get_db", return_value=self.db)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_sudo_add_remove(self):
        import utils.mongo_db as mdb
        _run(mdb.add_sudo(999))
        self.assertIn(999, _run(mdb.list_sudo()))
        _run(mdb.remove_sudo(999))
        self.assertNotIn(999, _run(mdb.list_sudo()))

    def test_scheduled_jobs(self):
        import utils.mongo_db as mdb
        jid = _run(mdb.add_scheduled_job("broadcast", 123456, {"text": "hi"}))
        self.assertEqual(len(_run(mdb.list_scheduled_jobs())), 1)
        self.assertTrue(_run(mdb.cancel_scheduled_job(jid)))
        self.assertEqual(len(_run(mdb.list_scheduled_jobs())), 0)

    def test_blackwords(self):
        import utils.mongo_db as mdb
        _run(mdb.add_blackword("spam"))
        self.assertTrue(any(w["word"] == "spam" for w in _run(mdb.list_blackwords())))
        self.assertTrue(_run(mdb.del_blackword("spam")))
        self.assertFalse(any(w["word"] == "spam" for w in _run(mdb.list_blackwords())))

    def test_watchlist(self):
        import utils.mongo_db as mdb
        _run(mdb.add_watch(123, "suspect"))
        self.assertTrue(_run(mdb.is_watched(123)))
        _run(mdb.remove_watch(123))
        self.assertFalse(_run(mdb.is_watched(123)))


class TestOwnerCommandsSmoke(unittest.TestCase):
    def setUp(self):
        self.db = _FakeDB()
        self.patcher = patch("utils.mongo_db.get_db", return_value=self.db)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def _owner_update(self):
        msg = AsyncMock()
        msg.reply_html = AsyncMock()
        chat = AsyncMock()
        chat.id = 111111
        user = AsyncMock()
        user.id = 111111
        upd = AsyncMock()
        upd.effective_user = user
        upd.effective_chat = chat
        upd.effective_message = msg
        return upd, msg

    def test_ownersys_menu(self):
        import handlers.owner_newsys as m
        upd, msg = self._owner_update()
        ctx = AsyncMock()
        ctx.args = []
        _run(m.ownersys_cmd(upd, ctx))
        self.assertTrue(msg.reply_html.called)

    def test_shieldstatus(self):
        import handlers.owner_newsys as m
        upd, msg = self._owner_update()
        ctx = AsyncMock()
        ctx.args = []
        _run(m.shieldstatus_cmd(upd, ctx))
        self.assertTrue(msg.reply_html.called)
        self.assertIn("Shield", msg.reply_html.call_args[0][0])

    def test_schedbroadcast_invalid_time(self):
        import handlers.owner_newsys as m
        upd, msg = self._owner_update()
        ctx = AsyncMock()
        ctx.args = ["soon", "hello"]
        _run(m.schedbroadcast_cmd(upd, ctx))
        self.assertIn("Invalid time", msg.reply_html.call_args[0][0])

    def test_blackword_add(self):
        import handlers.owner_newsys as m
        upd, msg = self._owner_update()
        ctx = AsyncMock()
        ctx.args = ["spam"]
        _run(m.blackword_cmd(upd, ctx))
        self.assertIn("Added", msg.reply_html.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
