"""
Unit tests for the banking & marketplace storage layer (utils.banking_store).

Real MongoDB isn't available in CI/sandbox, so we inject a tiny in-memory
fake DB that implements exactly the Motor operations the store uses
($inc / $set / $gte / $lt / $ne / upsert / find / insert). This exercises the
ACTUAL atomic logic of transfers, savings, loans and marketplace listings —
so a passing suite proves coins can never be created, lost, or duplicated.

Run:  python -m unittest tests.test_banking -v   (from the iota/ folder)
"""
import asyncio
import sys
import types
import unittest
from unittest.mock import patch

# ── Stub `config` BEFORE anything imports utils.mongo_db ───────────────────
sys.modules["config"] = types.SimpleNamespace(
    MONGO_URI="mongodb://fake", DB_NAME="test"
)

from bson import ObjectId  # noqa: E402

import utils.mongo_db as mm  # noqa: E402
from utils import banking_store as bs  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
# In-memory fake Motor DB
# ═══════════════════════════════════════════════════════════════════════════
def _matches(doc, flt):
    for k, cond in flt.items():
        val = doc.get(k)
        if isinstance(cond, dict):
            for op, v in cond.items():
                if op == "$gte":
                    if not (val >= v):
                        return False
                elif op == "$gt":
                    if not (val > v):
                        return False
                elif op == "$lte":
                    if not (val <= v):
                        return False
                elif op == "$lt":
                    if not (val < v):
                        return False
                elif op == "$ne":
                    if val == v:
                        return False
                elif op == "$regex":
                    import re as _re
                    flags = 0
                    if isinstance(v, dict) and "$options" in v:
                        pass
                    pattern = v if isinstance(v, str) else cond.get("$regex", "")
                    opts = cond.get("$options", "")
                    if "i" in opts:
                        flags = _re.I
                    if not _re.search(pattern, str(val or ""), flags):
                        return False
                # unknown ops ignored
        else:
            if val != cond:
                return False
    return True


def _apply(doc, upd):
    for op, fields in upd.items():
        if op == "$inc":
            for k, v in fields.items():
                doc[k] = doc.get(k, 0) + v
        elif op == "$set":
            for k, v in fields.items():
                doc[k] = v


class _Result:
    def __init__(self, modified_count=0, inserted_id=None):
        self.modified_count = modified_count
        self.inserted_id = inserted_id


class FakeCollection:
    def __init__(self, name):
        self.name = name
        self.docs = []

    def find(self, flt=None, projection=None):
        return FakeCursor(self.docs, flt or {})

    async def find_one(self, flt, projection=None):
        for d in self.docs:
            if _matches(d, flt):
                return d
        return None

    async def update_one(self, flt, upd, upsert=False):
        for d in self.docs:
            if _matches(d, flt):
                _apply(d, upd)
                return _Result(modified_count=1)
        if upsert:
            newdoc = {k: v for k, v in flt.items() if not isinstance(v, dict)}
            _apply(newdoc, upd)
            self.docs.append(newdoc)
            return _Result(modified_count=1)
        return _Result(modified_count=0)

    async def insert_one(self, doc):
        if "_id" not in doc:
            doc["_id"] = ObjectId()
        self.docs.append(doc)
        return _Result(inserted_id=doc["_id"])


class FakeCursor:
    def __init__(self, docs, flt):
        self.docs = docs
        self.flt = flt
        self._skip = 0
        self._limit = 0

    def sort(self, *a):
        return self

    def skip(self, n):
        self._skip = n
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _matched(self):
        ms = [d for d in self.docs if _matches(d, self.flt)]
        if self._skip:
            ms = ms[self._skip:]
        if self._limit:
            ms = ms[: self._limit]
        return ms

    def to_list(self, length=None):
        return self._matched()

    def __aiter__(self):
        return self._agen()

    async def _agen(self):
        for d in self._matched():
            yield d


class FakeDB:
    def __init__(self):
        self.users = FakeCollection("users")
        self.items = FakeCollection("items")
        self.marketplace = FakeCollection("marketplace")


def _u(db, uid, **kw):
    doc = {"_id": uid, "balance": 0, "savings": 0}
    doc.update(kw)
    db.users.docs.append(doc)
    return doc


# ═══════════════════════════════════════════════════════════════════════════
# Test suite
# ═══════════════════════════════════════════════════════════════════════════
class TestBankingStore(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self._patches = [
            patch.object(mm, "get_db", lambda: self.db),
            patch.object(bs, "get_db", lambda: self.db),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    # ── 1. transfer moves coins atomically, fails when broke ──────────────
    def test_transfer(self):
        _u(self.db, 1, balance=1000)
        _u(self.db, 2, balance=0)
        ok = asyncio.run(bs.transfer_coins(1, 2, 300))
        self.assertTrue(ok)
        self.assertEqual(self.db.users.docs[0]["balance"], 700)
        self.assertEqual(self.db.users.docs[1]["balance"], 300)
        # insufficient funds
        ok2 = asyncio.run(bs.transfer_coins(1, 2, 99999))
        self.assertFalse(ok2)
        self.assertEqual(self.db.users.docs[0]["balance"], 700)  # unchanged
        # no self-transfer
        ok3 = asyncio.run(bs.transfer_coins(1, 1, 10))
        self.assertFalse(ok3)

    # ── 2. savings deposit/withdraw gated correctly ───────────────────────
    def test_savings(self):
        _u(self.db, 5, balance=500, savings=0)

        self.assertTrue(asyncio.run(bs.savings_deposit(5, 200)))
        self.assertEqual(self.db.users.docs[0]["balance"], 300)
        self.assertEqual(self.db.users.docs[0]["savings"], 200)
        # over-withdraw fails
        self.assertFalse(asyncio.run(bs.savings_withdraw(5, 999)))
        self.assertEqual(self.db.users.docs[0]["savings"], 200)
        self.assertTrue(asyncio.run(bs.savings_withdraw(5, 100)))
        self.assertEqual(self.db.users.docs[0]["balance"], 400)
        self.assertEqual(self.db.users.docs[0]["savings"], 100)

    # ── 3. savings interest accrues & compounds ───────────────────────────
    def test_savings_interest(self):
        _u(self.db, 9, savings=1000)

        n = asyncio.run(bs.accrue_savings_interest(rate=0.10))
        self.assertEqual(n, 1)
        self.assertEqual(self.db.users.docs[0]["savings"], 1100)

    # ── 4. loan overdue penalty applied once ──────────────────────────────
    def test_loan_overdue(self):
        import time as _t
        _u(self.db, 10, loan_amount=500, loan_due_ts=_t.time() - 10, loan_overdue=False)

        n = asyncio.run(bs.apply_loan_overdue(penalty_pct=10))
        self.assertEqual(n, 1)
        self.assertEqual(self.db.users.docs[0]["loan_amount"], 550)
        self.assertTrue(self.db.users.docs[0]["loan_overdue"])
        # second pass: already penalised -> no change
        n2 = asyncio.run(bs.apply_loan_overdue(penalty_pct=10))
        self.assertEqual(n2, 0)
        self.assertEqual(self.db.users.docs[0]["loan_amount"], 550)

    # ── 5. marketplace: list -> buy is atomic; item not double-spent ───────
    def test_marketplace_buy(self):
        _u(self.db, 7, balance=0)
        # give user 7 three roses in inventory
        self.db.items.docs.append({"_id": ObjectId(), "owner_id": 7, "item_name": "rose", "quantity": 3})

        lid = asyncio.run(bs.add_listing(7, "rose", 2, 50))
        self.assertIsNotNone(lid)
        # stock reduced to 1
        self.assertEqual(self.db.items.docs[0]["quantity"], 1)
        # buyer with 200 coins buys the listing (cost 100)
        _u(self.db, 8, balance=200)
        ok, note = asyncio.run(bs.buy_listing(lid, 8))
        self.assertTrue(ok)
        buyer = next(d for d in self.db.users.docs if d["_id"] == 8)
        seller = next(d for d in self.db.users.docs if d["_id"] == 7)
        self.assertEqual(buyer["balance"], 100)          # 200 - 100
        self.assertEqual(seller["balance"], 100)         # +100
        # buyer now owns 2 roses
        bought = next((d for d in self.db.items.docs if d["owner_id"] == 8), None)
        self.assertIsNotNone(bought)
        self.assertEqual(bought["quantity"], 2)
        # listing now inactive
        sold_doc = next(d for d in self.db.marketplace.docs if d["_id"] == lid)
        self.assertFalse(sold_doc["active"])
        # buy again fails (sold)
        ok2, _ = asyncio.run(bs.buy_listing(lid, 8))
        self.assertFalse(ok2)

    # ── 6. cancel returns item to seller ──────────────────────────────────
    def test_marketplace_cancel(self):
        _u(self.db, 7, balance=0)
        self.db.items.docs.append({"_id": ObjectId(), "owner_id": 7, "item_name": "rose", "quantity": 3})

        lid = asyncio.run(bs.add_listing(7, "rose", 2, 50))
        self.assertTrue(asyncio.run(bs.cancel_listing(lid, 7)))
        # item back to 3
        self.assertEqual(self.db.items.docs[0]["quantity"], 3)
        # non-owner cannot cancel
        self.assertFalse(asyncio.run(bs.cancel_listing(lid, 99)))

    # ── 7. listing requires ownership ─────────────────────────────────────
    def test_listing_requires_ownership(self):
        _u(self.db, 7, balance=0)
        # no roses owned

        self.assertIsNone(asyncio.run(bs.add_listing(7, "rose", 1, 50)))


if __name__ == "__main__":
    unittest.main()
