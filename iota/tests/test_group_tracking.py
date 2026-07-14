"""
Tests for group auto-tracking (the fix for "broadcast/welcome don't work in
new non-admin groups").

Root cause that this guards against: groups only got a group_settings doc
when an admin ran an advanced-admin command, so a non-admin group was
invisible to /broadcast and had no persisted welcome settings. Now
ensure_group_settings() creates a complete doc on join, and get_all_groups()
ignores groups the bot has left.
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
IOTA = os.path.dirname(HERE)
if IOTA not in sys.path:
    sys.path.insert(0, IOTA)

os.environ.setdefault("BOT_TOKEN", "123456:fake-test-token")
os.environ.setdefault("OWNER_ID", "111111")
os.environ.setdefault("MONGO_URI",
                      "mongodb+srv://test:test@cluster0.tjpjh4k.mongodb.net/iota_bot")

import utils.mongo_db as mdb  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


class _FakeColl:
    def __init__(self):
        self.docs = {}

    async def find_one(self, q, *_a, **_k):
        _id = q.get("_id")
        return self.docs.get(_id)

    def find(self, q, *_a, **_k):
        # Only supports the {"active": {"$ne": False}} filter used by
        # get_all_groups().
        class _Cur:
            def __init__(self, items):
                self.items = items

            async def to_list(self, n):
                return self.items
        items = [{"_id": k} for k, v in self.docs.items()
                 if not (q.get("active") == {"$ne": False} and v.get("active") is False)]
        return _Cur(items)

    async def update_one(self, q, update, upsert=False):
        _id = q.get("_id")
        doc = self.docs.get(_id)
        if doc is None:
            if upsert:
                doc = {"_id": _id}
                self.docs[_id] = doc
            else:
                return
        if "$setOnInsert" in update:
            for k, v in update["$setOnInsert"].items():
                doc.setdefault(k, v)
        if "$set" in update:
            doc.update(update["$set"])


class _FakeDB:
    def __init__(self):
        self.group_settings = _FakeColl()
        self.welcome_settings = _FakeColl()


class TestEnsureGroupSettings(unittest.TestCase):
    def test_creates_complete_doc(self):
        db = _FakeDB()
        with patch.object(mdb, "get_db", return_value=db):
            _run(mdb.ensure_group_settings(123, title="My Group"))
        doc = db.group_settings.docs[123]
        # Every canonical default field must be present (so advanced-admin
        # commands never KeyError).
        for k in mdb.DEFAULT_GROUP_SETTINGS:
            self.assertIn(k, doc)
        self.assertEqual(doc["active"], True)
        self.assertEqual(doc["title"], "My Group")
        # welcome defaults created too
        self.assertIn(123, db.welcome_settings.docs)

    def test_preserves_existing_customisation(self):
        db = _FakeDB()
        db.group_settings.docs[123] = {"_id": 123, "rules": "be nice", "active": True}
        with patch.object(mdb, "get_db", return_value=db):
            _run(mdb.ensure_group_settings(123))
        # The existing custom value must NOT be clobbered by defaults.
        self.assertEqual(db.group_settings.docs[123]["rules"], "be nice")

    def test_get_all_groups_skips_inactive(self):
        db = _FakeDB()
        db.group_settings.docs[1] = {"_id": 1, "active": True}
        db.group_settings.docs[2] = {"_id": 2, "active": False}  # left group
        db.group_settings.docs[3] = {"_id": 3}                  # no active field
        with patch.object(mdb, "get_db", return_value=db):
            groups = _run(mdb.get_all_groups())
        ids = {g["_id"] for g in groups}
        self.assertIn(1, ids)
        self.assertIn(3, ids)          # missing active field is treated as active
        self.assertNotIn(2, ids)       # left group excluded


if __name__ == "__main__":
    unittest.main()
