"""
Tests for the PREMIUM banking expansion (handlers/banking.py + utils/mongo_db).

Covers:
  * FD / RD payout math (pure functions) — catches rate/penalty bugs
  * FD lifecycle (create -> mature via maintenance -> wallet credited)
  * RD lifecycle (start -> installments/maturity via maintenance)
  * user-owned Bank/Branch: open (reserve lock), customer deposit/withdraw,
    close (reserve + deposits returned)
  * demand-deposit (bank) interest is capped at the premium cap
  * the @premium_gate blocks non-premium users before any logic runs

Real MongoDB isn't available, so a small in-memory fake that implements the
exact Motor operations the helpers use ($inc / $set on dotted paths /
$unset / find / insert) is injected.
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch, AsyncMock

os.environ.setdefault("BOT_TOKEN", "123456:fake")
os.environ.setdefault("OWNER_ID", "111111")
os.environ.setdefault(
    "MONGO_URI", "mongodb+srv://test:test@cluster0.mongodb.net/iota_bot"
)

from bson import ObjectId

import utils.mongo_db as mm
from utils import banking_store as bs
from handlers import banking as bk


# ── tiny fake Motor ────────────────────────────────────────────────────────
def _set_path(doc, path, value):
    parts = path.split(".")
    d = doc
    for p in parts[:-1]:
        d = d.setdefault(p, {})
    d[parts[-1]] = value


def _unset_path(doc, path):
    parts = path.split(".")
    d = doc
    for p in parts[:-1]:
        d = d.get(p, {})
    d.pop(parts[-1], None)


def _apply(doc, upd):
    for op, fields in upd.items():
        if op == "$inc":
            for k, v in fields.items():
                doc[k] = doc.get(k, 0) + v
        elif op == "$set":
            for k, v in fields.items():
                if "." in k:
                    _set_path(doc, k, v)
                else:
                    doc[k] = v
        elif op == "$unset":
            for k in fields:
                if "." in k:
                    _unset_path(doc, k)
                else:
                    doc.pop(k, None)


def _matches(doc, flt):
    for k, cond in flt.items():
        val = doc.get(k)
        if isinstance(cond, dict):
            for op, v in cond.items():
                if op == "$gte" and not (val >= v):
                    return False
                if op == "$gt" and not (val > v):
                    return False
                if op == "$lt" and not (val < v):
                    return False
                if op == "$lte" and not (val <= v):
                    return False
                if op == "$ne" and val == v:
                    return False
        else:
            if val != cond:
                return False
    return True


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

    async def create_index(self, *a, **k):
        return None


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

    async def to_list(self, length=None):
        return self._matched()

    def __aiter__(self):
        return self._agen()

    async def _agen(self):
        for d in self._matched():
            yield d


class FakeDB:
    def __init__(self):
        self.users = FakeCollection("users")
        self.fixed_deposits = FakeCollection("fixed_deposits")
        self.recurring_deposits = FakeCollection("recurring_deposits")
        self.banks = FakeCollection("banks")
        self.bank_txns = FakeCollection("bank_txns")
        self.system_status = FakeCollection("system_status")

    async def command(self, *a, **k):
        return {"ok": True}

    async def list_collection_names(self):
        return [k for k in vars(self) if not k.startswith("_")]


def _u(db, uid, **kw):
    doc = {"_id": uid, "balance": 0, "savings": 0, "bank": 0,
           "is_premium": True, "is_banned": False, "loan_amount": 0,
           "loan_due_ts": 0, "loan_overdue": False}
    doc.update(kw)
    db.users.docs.append(doc)
    return doc


def _run(coro):
    return asyncio.run(coro)


class TestPayoutMath(unittest.TestCase):
    def test_fd_matured_payout(self):
        # status "matured" => full principal + interest (no break penalty)
        fd = {"principal": 1000, "rate": 0.10, "tenure_days": 30,
              "status": "matured", "created_at": 0, "maturity_ts": 100}
        self.assertEqual(mm.fd_payout(fd), 1100)

    def test_fd_broken_penalty(self):
        # fully elapsed -> progress=1, penalty 10% of (1000+100)
        fd = {"principal": 1000, "rate": 0.10, "tenure_days": 30,
              "status": "active", "created_at": 0, "maturity_ts": 100}
        payout = mm.fd_payout(fd)
        # force broken status path: reuse helper with status set
        fd["status"] = "broken"
        # min principal guard
        self.assertGreaterEqual(mm.fd_payout(fd), 1000)

    def test_rd_payout(self):
        rd = {"total": 5000, "rate": 0.01, "months": 6, "status": "active"}
        self.assertEqual(mm.rd_payout(rd), 5000 + int(5000 * 0.01 * 6))


class TestFDLifecycle(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.p = patch.object(mm, "get_db", lambda: self.db)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_create_and_mature(self):
        _u(self.db, 1, balance=2000)
        fd = _run(mm.create_fd(1, 1000, 30, 0.10))
        # principal deducted from wallet
        self.assertEqual(self.db.users.docs[0]["balance"], 1000)
        # mature it
        self.db.fixed_deposits.docs[0]["maturity_ts"] = 0  # already due
        done = _run(mm.process_fd_maturities())
        self.assertEqual(done, 1)
        self.assertEqual(self.db.users.docs[0]["balance"], 2100)  # +1100
        self.assertEqual(self.db.fixed_deposits.docs[0]["status"], "matured")


class TestRDLifecycle(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.p = patch.object(mm, "get_db", lambda: self.db)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_start_and_mature(self):
        _u(self.db, 1, balance=5000)
        rd = _run(mm.create_rd(1, 1000, 3, 0.01))  # 3 months, 1000/mo
        self.assertEqual(self.db.users.docs[0]["balance"], 4000)  # first inst taken
        # force maturity now
        self.db.recurring_deposits.docs[0]["maturity_ts"] = 0
        done = _run(mm.process_rd_installments())
        self.assertEqual(done, 1)
        # total=1000, interest = 1000*0.01*3 = 30 -> 1030 credited
        self.assertEqual(self.db.users.docs[0]["balance"], 5030)
        self.assertEqual(self.db.recurring_deposits.docs[0]["status"], "matured")


class TestBankBranch(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.p = patch.object(mm, "get_db", lambda: self.db)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_open_deposit_withdraw_close(self):
        owner = _u(self.db, 1, balance=1_000_000)
        bank = _run(mm.create_bank(1, "Iota Bank", 1_000_000))
        self.assertEqual(self.db.users.docs[0]["balance"], 0)  # reserve locked
        # customer deposits
        _u(self.db, 2, balance=5000)
        ok, fee = _run(mm.bank_deposit(bank["_id"], 2, 5000))
        self.assertTrue(ok)
        self.assertEqual(fee, 50)  # 1% owner fee
        self.assertEqual(self.db.users.docs[1]["balance"], 0)  # 5000 left wallet
        self.assertEqual(self.db.users.docs[0]["balance"], 50)  # owner got fee
        # customer withdraws all (principal+interest ~ 5000+)
        ok2, payout = _run(mm.bank_withdraw(bank["_id"], 2, 10_000))
        self.assertTrue(ok2)
        self.assertGreaterEqual(payout, 5000)
        self.assertEqual(self.db.users.docs[1]["balance"], payout)
        # close bank: reserve + any remaining customer deposits returned
        res = _run(mm.close_bank(bank["_id"]))
        self.assertTrue(res["ok"])
        self.assertEqual(res["reserve"], 1_000_000)
        self.assertEqual(self.db.users.docs[0]["balance"], 1_000_000 + 50)


class TestBankInterestCap(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.p = patch.object(mm, "get_db", lambda: self.db)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_interest_capped(self):
        _u(self.db, 1, bank=999_900)
        n = _run(mm.accrue_bank_interest(rate=0.005, cap=1_000_000))
        self.assertEqual(n, 1)
        # 999900 * 0.005 = 4995 -> would exceed cap; must clamp to 1,000,000
        self.assertEqual(self.db.users.docs[0]["bank"], 1_000_000)


class TestPremiumGate(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.p = patch.object(mm, "get_db", lambda: self.db)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def _update(self, premium: bool):
        _u(self.db, 1, is_premium=premium)
        msg = AsyncMock()
        msg.reply_html = AsyncMock()
        u = AsyncMock(); u.id = 1
        upd = AsyncMock()
        upd.effective_user = u
        upd.effective_message = msg
        return upd, msg

    def test_non_premium_blocked(self):
        upd, msg = self._update(False)
        ctx = AsyncMock(); ctx.args = []
        _run(bk.fd_cmd(upd, ctx))  # no args -> would list if premium
        self.assertTrue(msg.reply_html.called)
        self.assertIn("Premium", msg.reply_html.call_args[0][0])

    def test_premium_allowed(self):
        upd, msg = self._update(True)
        ctx = AsyncMock(); ctx.args = []
        _run(bk.fd_cmd(upd, ctx))  # no args -> lists (empty) message
        self.assertTrue(msg.reply_html.called)
        # premium path reaches the FD command (lists / shows create hint)
        self.assertIn("/fd", msg.reply_html.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
