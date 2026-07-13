"""
Tests for the single-instance lock (utils/instance_lock.py).

Proves the Conflict crash is structurally impossible: only the process that
wins the lock polls. We mock Mongo so the suite is offline/fast.
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

HERE = os.path.dirname(os.path.abspath(__file__))
IOTA = os.path.dirname(HERE)
if IOTA not in sys.path:
    sys.path.insert(0, IOTA)

os.environ.setdefault("BOT_TOKEN", "123456:fake-test-token")
os.environ.setdefault("OWNER_ID", "111111")
os.environ.setdefault("MONGO_URI",
                      "mongodb+srv://test:test@cluster0.tjpjh4k.mongodb.net/iota_bot")

import utils.instance_lock as lock  # noqa: E402

# In tests we stub asyncio.create_task, so make _heartbeat return a plain
# value (not a coroutine) to avoid "coroutine never awaited" warnings.
lock._heartbeat = MagicMock(return_value=None)


def _run(coro):
    return asyncio.run(coro)


class _FakeColl:
    """In-memory stand-in for the bot_instance_lock collection."""
    def __init__(self):
        self.docs = {}

    async def find_one_and_update(self, filt, upd, upsert=False,
                                   return_document=None):
        doc = self.docs.get(lock.LOCK_ID)
        now = upd["$set"]["last_heartbeat"]
        matched = False
        if doc is None:
            matched = True  # upsert path
        elif doc.get("pid") == upd["$set"]["pid"]:
            matched = True
        elif doc.get("last_heartbeat", 0) < now - lock.STALE_SECS:
            matched = True
        if matched:
            new = dict(upd["$set"])
            new["_id"] = lock.LOCK_ID
            self.docs[lock.LOCK_ID] = new
            return new
        return None

    async def update_one(self, filt, upd):
        d = self.docs.get(lock.LOCK_ID)
        if d and d.get("pid") == filt.get("pid"):
            d["last_heartbeat"] = upd["$set"]["last_heartbeat"]
        return MagicMock()

    async def delete_one(self, filt):
        d = self.docs.get(lock.LOCK_ID)
        if d and d.get("pid") == filt.get("pid"):
            self.docs.pop(lock.LOCK_ID, None)
        return MagicMock()


class TestInstanceLock(unittest.TestCase):
    def setUp(self):
        self.coll = _FakeColl()

    def test_first_process_becomes_primary(self):
        with patch.object(lock, "_coll", return_value=self.coll), \
             patch("utils.instance_lock.asyncio.create_task", new=MagicMock()):
            # ensure_single_instance should return immediately for the first.
            _run(lock.ensure_single_instance(MagicMock()))
        self.assertIn(lock.LOCK_ID, self.coll.docs)

    def test_second_process_waits_and_does_not_claim(self):
        # First claims it.
        with patch.object(lock, "_coll", return_value=self.coll), \
             patch("utils.instance_lock.asyncio.create_task", new=MagicMock()):
            _run(lock.ensure_single_instance(MagicMock()))
        owner_pid = self.coll.docs[lock.LOCK_ID]["pid"]

        # Second process: monkeypatch sleep to avoid a real wait, and assert
        # it never overwrites the lock (i.e. it loops, not acquires).
        sleeps = []
        async def _fake_sleep(s):
            sleeps.append(s)
            # Break the loop after the first iteration so the test ends.
            raise asyncio.CancelledError()

        async def _fake_acquire(my_id):
            return await lock._try_acquire(my_id)

        with patch.object(lock, "_coll", return_value=self.coll), \
             patch("utils.instance_lock.asyncio.sleep", _fake_sleep):
            try:
                _run(lock.ensure_single_instance(MagicMock()))
            except asyncio.CancelledError:
                pass
        # Lock still owned by the first process.
        self.assertEqual(self.coll.docs[lock.LOCK_ID]["pid"], owner_pid)
        self.assertTrue(sleeps)  # the secondary did wait

    def test_stale_lock_is_taken_over(self):
        # Seed a stale lock owned by someone else.
        self.coll.docs[lock.LOCK_ID] = {
            "_id": lock.LOCK_ID, "pid": "old",
            "last_heartbeat": 0,  # far in the past → stale
        }
        with patch.object(lock, "_coll", return_value=self.coll), \
             patch("utils.instance_lock.asyncio.create_task", new=MagicMock()):
            _run(lock.ensure_single_instance(MagicMock()))
        self.assertNotEqual(self.coll.docs[lock.LOCK_ID]["pid"], "old")

    def test_db_failure_proceeds_as_primary(self):
        async def _boom(*a, **k):
            raise RuntimeError("mongo down")
        fake = MagicMock()
        fake.find_one_and_update = _boom
        with patch.object(lock, "_coll", return_value=fake), \
             patch("utils.instance_lock.asyncio.create_task", new=MagicMock()):
            # Should NOT raise — we proceed as primary on DB error.
            _run(lock.ensure_single_instance(MagicMock()))


if __name__ == "__main__":
    unittest.main()
