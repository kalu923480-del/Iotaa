"""
Tests for utils.telegram_safe.safe_call — the helper that keeps transient
Telegram timeouts/network blips from bubbling up as owner-panel "crashed!"
reports.

Proves:
  * transient errors (TimedOut / NetworkError) are retried once,
  * RetryAfter is honoured (sleeps, then retries),
  * permanent errors (BadRequest / Forbidden) are NOT retried,
  * it NEVER raises, even if the wrapped call itself raises.
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock

HERE = os.path.dirname(os.path.abspath(__file__))
IOTA = os.path.dirname(HERE)
if IOTA not in sys.path:
    sys.path.insert(0, IOTA)

os.environ.setdefault("BOT_TOKEN", "123456:fake-test-token")
os.environ.setdefault("OWNER_ID", "111111")
os.environ.setdefault("MONGO_URI",
                      "mongodb+srv://test:test@cluster0.tjpjh4k.mongodb.net/iota_bot")

import utils.telegram_safe as ts  # noqa: E402
from telegram.error import TimedOut, NetworkError, RetryAfter, BadRequest, Forbidden  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


class TestSafeCall(unittest.TestCase):
    def test_returns_result_on_success(self):
        out = _run(ts.safe_call(lambda: AsyncMock(return_value="ok")()))
        self.assertEqual(out, "ok")

    def test_retries_transient_then_succeeds(self):
        calls = {"n": 0}

        async def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimedOut("slow")
            return "done"

        out = _run(ts.safe_call(flaky, retries=1, label="t"))
        self.assertEqual(out, "done")
        self.assertEqual(calls["n"], 2)

    def test_transient_exhausted_returns_none(self):
        async def always_down():
            raise NetworkError("nope")

        out = _run(ts.safe_call(always_down, retries=1, label="t"))
        self.assertIsNone(out)

    def test_retryafter_is_honoured(self):
        calls = {"n": 0}

        async def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RetryAfter(0)
            return "ok"

        out = _run(ts.safe_call(flaky, retries=1, label="t"))
        self.assertEqual(out, "ok")
        self.assertEqual(calls["n"], 2)

    def test_permanent_error_not_retried(self):
        calls = {"n": 0}

        async def boom():
            calls["n"] += 1
            raise BadRequest("bad")

        out = _run(ts.safe_call(boom, retries=2, label="t"))
        self.assertIsNone(out)
        self.assertEqual(calls["n"], 1)  # no retry on permanent errors

    def test_forbidden_not_retried(self):
        calls = {"n": 0}

        async def boom():
            calls["n"] += 1
            raise Forbidden("no")

        out = _run(ts.safe_call(boom, retries=2, label="t"))
        self.assertIsNone(out)
        self.assertEqual(calls["n"], 1)

    def test_never_raises(self):
        async def explode():
            raise RuntimeError("unexpected")

        out = _run(ts.safe_call(explode, retries=1, label="t"))
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
