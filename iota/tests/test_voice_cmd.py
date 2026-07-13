"""
Regression test for the /voice command's TTS-failure path.

Caught bug: voice_cmd referenced `safe_html` in the "no audio" branch, but a
function-local `from utils.safe_html import safe_html` inside the except block
made `safe_html` a function-local that was unbound on the non-exception path
→ UnboundLocalError ("cannot access local variable 'safe_html'"). This test
ensures /voice handles a TTS failure WITHOUT raising.
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

from handlers import utility  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _make_update(text_args):
    update = MagicMock()
    msg = MagicMock()
    thinking = MagicMock()
    thinking.delete = AsyncMock()
    thinking.edit_text = AsyncMock()
    msg.reply_html = AsyncMock(return_value=thinking)
    msg.reply_voice = AsyncMock()
    update.effective_message = msg
    update.message = msg
    return update, thinking


class TestVoiceCmdFailure(unittest.TestCase):
    def test_no_audio_does_not_raise_unboundlocalerror(self):
        update, thinking = _make_update([])
        context = MagicMock()
        context.args = ["hi", "hello world"]

        with patch.object(utility, "text_to_speech", new=AsyncMock(return_value=None)), \
             patch.object(utility, "get_last_tts_error", return_value="Sarvam API HTTP 401: unauthorized"), \
             patch.object(utility, "get_tts_config", return_value={"speaker": "shubh"}), \
             patch("utils.tts_engine.is_valid_voice", return_value=True), \
             patch("utils.tts_engine.get_voice_ids", return_value={"shubh"}):
            # Must not raise (previously UnboundLocalError on safe_html).
            _run(utility.voice_cmd(update, context))

        thinking.edit_text.assert_awaited()
        # The reason should have been included in the message.
        sent = thinking.edit_text.call_args[0][0]
        self.assertIn("401", sent)

    def test_success_sends_voice(self):
        update, thinking = _make_update([])
        context = MagicMock()
        context.args = ["hi", "hello world"]
        context.bot.send_voice = AsyncMock()

        with patch.object(utility, "text_to_speech", new=AsyncMock(return_value=b"WAVEDATA")), \
             patch.object(utility, "get_tts_config", return_value={"speaker": "shubh"}), \
             patch("utils.tts_engine.is_valid_voice", return_value=True), \
             patch("utils.tts_engine.get_voice_ids", return_value={"shubh"}), \
             patch("utils.tts_engine.voice_display", return_value="Shubh"), \
             patch("utils.tts_engine.send_tts_voice", new=AsyncMock(return_value=(True, None))):
            _run(utility.voice_cmd(update, context))

        thinking.delete.assert_awaited()
        context.bot.send_voice.assert_awaited()


if __name__ == "__main__":
    unittest.main()
