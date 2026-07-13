"""
Tests for TTS setting-key aliases + the resilient voice sender.

Proves:
  * /ttssettings typo "temprature" maps to "temperature" (no "Unknown setting").
  * send_tts_voice retries once on TimedOut and falls back to send_audio on
    BadRequest, and NEVER raises (so a flaky Telegram send can't bubble up to
    the owner-panel crash reporter).
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

import utils.tts_engine as eng  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


class TestSettingAliases(unittest.TestCase):
    def setUp(self):
        eng._tts_config.update(
            {"model": "bulbul:v3", "speaker": "shubh",
             "pace": 1.0, "temperature": 0.6, "sample_rate": 24000})

    def test_typo_temperature_resolves(self):
        self.assertEqual(eng.normalize_setting_key("temprature"), "temperature")
        ok, _ = eng.set_tts_setting("temprature", "0.7")
        self.assertTrue(ok)
        self.assertEqual(eng.get_tts_config()["temperature"], 0.7)

    def test_alias_sr_and_voice(self):
        self.assertEqual(eng.normalize_setting_key("sr"), "sample_rate")
        self.assertEqual(eng.normalize_setting_key("voice"), "speaker")
        ok, _ = eng.set_tts_setting("sr", "32000")
        self.assertTrue(ok)
        self.assertEqual(eng.get_tts_config()["sample_rate"], 32000)


class _FakeBot:
    def __init__(self):
        self.voice_calls = 0
        self.audio_calls = 0
        self._fail_voice_with = None
        self._fail_voice_persist = False  # True => keep failing every attempt
        self._fail_audio_with = None

    async def send_voice(self, *a, **k):
        self.voice_calls += 1
        if self._fail_voice_with:
            err = self._fail_voice_with
            if not self._fail_voice_persist:
                # Model a transient blip: fail this once, then succeed on retry.
                self._fail_voice_with = None
            raise err
        return MagicMock()

    async def send_audio(self, *a, **k):
        self.audio_calls += 1
        if self._fail_audio_with:
            raise self._fail_audio_with
        return MagicMock()


class TestSendTtsVoice(unittest.TestCase):
    def test_success_first_try(self):
        bot = _FakeBot()
        ok, err = _run(eng.send_tts_voice(bot, 1, b"data", caption="x"))
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(bot.voice_calls, 1)

    def test_retries_on_timedout_then_succeeds(self):
        from telegram.error import TimedOut
        bot = _FakeBot()
        bot._fail_voice_with = TimedOut("timed out")
        ok, err = _run(eng.send_tts_voice(bot, 1, b"data"))
        self.assertTrue(ok)
        self.assertEqual(bot.voice_calls, 2)  # initial + 1 retry

    def test_badrequest_falls_back_to_audio(self):
        from telegram.error import BadRequest
        bot = _FakeBot()
        bot._fail_voice_with = BadRequest("bad format")
        ok, err = _run(eng.send_tts_voice(bot, 1, b"data"))
        self.assertTrue(ok)
        self.assertEqual(bot.audio_calls, 1)

    def test_never_raises(self):
        from telegram.error import TimedOut
        bot = _FakeBot()
        bot._fail_voice_with = TimedOut("x")
        bot._fail_voice_persist = True  # keep failing every attempt
        bot._fail_audio_with = TimedOut("y")  # only matters on fallback path
        # Force both voice attempts to fail with TimedOut (no audio fallback
        # triggered because the first failure is TimedOut, not BadRequest).
        ok, err = _run(eng.send_tts_voice(bot, 1, b"data"))
        self.assertFalse(ok)
        self.assertIn("timed out", err.lower())


if __name__ == "__main__":
    unittest.main()
