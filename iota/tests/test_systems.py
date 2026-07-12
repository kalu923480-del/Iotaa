"""
Unit tests for the new systems:
  * utils/progress  — achievements / daily / quests / stats (in-memory DB mock)
  * utils/callback_codec — safe_cb fallback + 64-byte guard
  * handlers/connect_four — board drop + win detection (pure logic)

Run: python -m unittest tests.test_systems -v   (from the iota/ folder)
"""
import os
import sys
import asyncio
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
IOTA = os.path.dirname(HERE)
if IOTA not in sys.path:
    sys.path.insert(0, IOTA)


# ── minimal in-memory mongo mock (motor-compatible surface used by progress) ──
class _FakeColl:
    def __init__(self):
        self.docs = {}

    async def find_one(self, q):
        return self.docs.get(q.get("_id"))

    def find(self, q=None):
        return self

    async def to_list(self, n=0):
        items = list(self.docs.values())
        return items if n == 0 else items[:n]

    async def insert_one(self, doc):
        self.docs[doc["_id"]] = dict(doc)

    async def update_one(self, q, upd, upsert=False):
        d = self.docs.get(q["_id"])
        if d is None:
            if not upsert:
                return
            d = {"_id": q["_id"]}
            self.docs[q["_id"]] = d
        for k, v in upd.get("$set", {}).items():
            d[k] = v
        for k, v in upd.get("$inc", {}).items():
            d[k] = d.get(k, 0) + v


class _FakeDB:
    def __init__(self):
        self.user_progress = _FakeColl()
        self.sticky = _FakeColl()
        self.schedules = _FakeColl()
        self.rss = _FakeColl()
        self.feedback = _FakeColl()

    def __getitem__(self, key):
        return getattr(self, key)


_fake = _FakeDB()
import utils.progress as progress
progress.get_db = lambda: _fake


def run(coro):
    return asyncio.run(coro)


class TestProgress(unittest.TestCase):
    def setUp(self):
        _fake.user_progress.docs.clear()

    def test_first_win_unlocks(self):
        run(progress.record_game_result(101, won=True, bet=500, game="dice"))
        rows = run(progress.achievements_list(101))
        first_win = next(r for r in rows if r[0] == "first_win")
        self.assertTrue(first_win[4])  # unlocked

    def test_loss_does_not_unlock_win(self):
        run(progress.record_game_result(102, won=False, bet=10))
        rows = run(progress.achievements_list(102))
        self.assertFalse(next(r for r in rows if r[0] == "first_win")[4])

    def test_high_roller_on_big_bet(self):
        run(progress.record_game_result(103, won=True, bet=15000, game="card"))
        rows = run(progress.achievements_list(103))
        self.assertTrue(next(r for r in rows if r[0] == "high_roller")[4])

    def test_daily_claim_flow(self):
        run(progress.get_or_init_daily(104))
        # force complete
        doc = run(progress.ensure_progress(104))
        doc["daily"]["completed"] = True
        run(progress.get_db().user_progress.update_one(
            {"_id": 104}, {"$set": {"daily": doc["daily"]}}))
        already, amt = run(progress.claim_daily(104))
        self.assertFalse(already)
        self.assertGreater(amt, 0)
        already2, _ = run(progress.claim_daily(104))
        self.assertTrue(already2)  # can't claim twice

    def test_global_leaders_rank(self):
        run(progress.record_game_result(201, won=True, bet=500))
        run(progress.record_game_result(202, won=True, bet=500))
        run(progress.record_game_result(202, won=True, bet=500))
        leaders = run(progress.global_achievement_leaders(10))
        self.assertTrue(any(uid == 202 for uid, _ in leaders))


class TestCallbackCodec(unittest.TestCase):
    def test_safe_cb_under_limit(self):
        from utils.callback_codec import safe_cb
        tok = safe_cb("wsp", {"w": "abc123"})
        self.assertTrue(tok.startswith("wsp:"))
        self.assertLessEqual(len(tok), 64)

    def test_safe_cb_fallback_when_too_long(self):
        from utils.callback_codec import safe_cb
        big = {"x": "z" * 200}
        self.assertEqual(safe_cb("t", big, fallback="home"), "home")

    def test_guard_install_does_not_raise(self):
        from utils.callback_codec import install_callback_guard
        install_callback_guard()  # idempotent, must not raise


class TestConnectFour(unittest.TestCase):
    def test_drop_returns_lowest_row(self):
        from handlers.connect_four import _new_board, _drop, _P1, ROWS
        b = _new_board()
        self.assertEqual(_drop(b, 0, _P1), ROWS - 1)
        self.assertEqual(_drop(b, 0, _P1), ROWS - 2)

    def test_horizontal_win(self):
        from handlers.connect_four import _new_board, _drop, _win, _P1
        b = _new_board()
        for c in range(4):
            _drop(b, c, _P1)
        self.assertTrue(_win(b, _P1))

    def test_vertical_win(self):
        from handlers.connect_four import _new_board, _drop, _win, _P1
        b = _new_board()
        _drop(b, 2, _P1)
        _drop(b, 2, _P1)
        _drop(b, 2, _P1)
        _drop(b, 2, _P1)
        self.assertTrue(_win(b, _P1))

    def test_no_false_win(self):
        from handlers.connect_four import _new_board, _drop, _win, _P1, _P2
        b = _new_board()
        for c in range(3):
            _drop(b, c, _P1)
        _drop(b, 3, _P2)
        self.assertFalse(_win(b, _P1))


class TestUno(unittest.TestCase):
    def test_deck_size(self):
        from handlers.uno import _build_deck, _playable
        deck = _build_deck()
        self.assertEqual(len(deck), 108)  # standard UNO deck

    def test_playable_rules(self):
        from handlers.uno import _playable
        self.assertTrue(_playable(("R", "5"), ("R", "9")))   # same colour
        self.assertTrue(_playable(("G", "5"), ("R", "5")))   # same number
        self.assertTrue(_playable(("W", "+4"), ("R", "9")))  # wild always
        self.assertFalse(_playable(("B", "3"), ("R", "9")))  # no match


if __name__ == "__main__":
    unittest.main()
