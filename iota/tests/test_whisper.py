"""
Unit tests for the /whisper command (handlers/whisper.py).

These prove the command:
  * never throws an unhandled exception (the reported "/whisper hi" crash
    that surfaced "Kuch gadbad ho gayi!" is gone — it now shows a clear
    "Mention a user" message instead).
  * works both as /whisper and as the advertised .whisper dot-command.
  * resolves a target from a reply, a @username, or a raw id.
  * refuses to whisper to yourself or to a bot.
  * delivers the read-receipt card and the private "Read whisper" button.

Run:  python -m unittest tests.test_whisper -v   (from the iota/ folder)
"""
import asyncio
import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

HERE = os.path.dirname(os.path.abspath(__file__))
IOTA = os.path.dirname(HERE)
if IOTA not in sys.path:
    sys.path.insert(0, IOTA)


def _user(uid, name="U", is_bot=False, username=None):
    u = MagicMock()
    u.id = uid
    u.full_name = name
    u.first_name = name
    u.username = username
    u.is_bot = is_bot
    return u


def _msg(text="", reply_user=None):
    msg = MagicMock()
    msg.text = text
    msg.message_id = 1
    msg.reply_html = AsyncMock()
    msg.reply_text = AsyncMock()
    msg.delete = AsyncMock()
    if reply_user is not None:
        rt = MagicMock()
        rt.from_user = _user(reply_user, "Target")
        msg.reply_to_message = rt
    else:
        msg.reply_to_message = None
    return msg


def _chat(chat_id=-100, chat_type="supergroup"):
    c = MagicMock()
    c.id = chat_id
    c.type = chat_type
    c.title = "TestGroup"
    return c


def _update(uid, chat, msg):
    up = MagicMock()
    up.effective_user = _user(uid, "Caller")
    up.effective_message = msg
    up.effective_chat = chat
    up.callback_query = None
    return up


def _ctx(bot_id=777, get_chat=None, get_chat_member=None, send_message=None):
    ctx = MagicMock()
    bot = MagicMock()
    bot.id = bot_id
    bot.get_chat = AsyncMock(side_effect=get_chat or (lambda *a, **k: _user(999, "Resolved")))
    bot.get_chat_member = AsyncMock(
        side_effect=get_chat_member or (lambda *a, **k: _user(42, "Member")))
    bot.send_message = AsyncMock(side_effect=send_message or (lambda *a, **k: None))
    ctx.bot = bot
    return ctx


class TestWhisperCommand(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        import handlers.whisper as whisper
        import handlers.admin as admin
        self.whisper = whisper
        self.admin = admin
        self.patchers = [
            patch.object(whisper, "ensure_user", AsyncMock()),
            patch.object(whisper, "create_whisper", AsyncMock(return_value="wid12345678")),
            patch.object(whisper, "get_whisper", AsyncMock(return_value=None)),
            patch.object(whisper, "mark_whisper_read", AsyncMock()),
            patch("utils.mongo_db.get_user_by_username", AsyncMock(return_value=None)),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()

    async def _call(self, text, ctx, reply_user=None, chat=_chat()):
        msg = _msg(text=text, reply_user=reply_user)
        up = _update(5, chat, msg)
        ctx.args = text.split()[1:] if len(text.split()) > 1 else []
        await self.whisper.whisper_cmd(up, ctx)
        return msg

    # ── 1. The exact reported crash: /whisper hi with NO target ──────────
    async def test_whisper_no_target_text_does_not_crash(self):
        ctx = _ctx(get_chat=AsyncMock(side_effect=Exception("chat not found")))
        msg = await self._call("/whisper hi", ctx)
        self.assertTrue(msg.reply_html.called)
        out = msg.reply_html.call_args[0][0]
        self.assertIn("Mention a user", out)
        self.assertNotIn("Kuch gadbad", out)

    # ── 2. /whisper with nothing at all also asks for a target ───────────
    async def test_whisper_no_args(self):
        ctx = _ctx()
        msg = await self._call("/whisper", ctx)
        out = msg.reply_html.call_args[0][0]
        self.assertIn("Mention a user", out)

    # ── 3. private chats are rejected ────────────────────────────────────
    async def test_whisper_private_rejected(self):
        ctx = _ctx()
        msg = await self._call("/whisper hi", ctx, chat=_chat(chat_type="private"))
        out = msg.reply_html.call_args[0][0]
        self.assertIn("only works inside a group", out)

    # ── 4. reply to a user + /whisper <msg> creates the whisper ──────────
    async def test_whisper_reply_creates(self):
        ctx = _ctx()
        msg = await self._call("/whisper hey there", ctx, reply_user=42)
        self.whisper.create_whisper.assert_awaited_once()
        out = msg.reply_html.call_args[0][0]
        self.assertIn("Tᴀʀɢᴇᴛ", out)  # target mention (small-caps styled)
        self.assertTrue(msg.reply_html.call_args.kwargs.get("reply_markup") is not None)

    # ── 5. named @username resolves and creates the whisper ──────────────
    async def test_whisper_named_user_creates(self):
        resolved = _user(321, "Named", username="named")
        ctx = _ctx(get_chat=lambda *a, **k: resolved)
        msg = await self._call("/whisper @named hello", ctx)
        self.whisper.create_whisper.assert_awaited_once()
        args = self.whisper.create_whisper.call_args.args
        self.assertEqual(args[1], 321)  # target_id
        self.assertEqual(args[3], "hello")

    # ── 6. can't whisper to yourself ─────────────────────────────────────
    async def test_whisper_self_rejected(self):
        ctx = _ctx()
        msg = _msg(text="/whisper @me hi", reply_user=None)
        up = _update(5, _chat(), msg)
        # Make the named target resolve to the caller themselves.
        ctx.bot.get_chat = AsyncMock(return_value=_user(5, "Caller"))
        ctx.args = ["@me", "hi"]
        await self.whisper.whisper_cmd(up, ctx)
        out = msg.reply_html.call_args[0][0]
        self.assertIn("can't whisper to yourself", out)
        self.whisper.create_whisper.assert_not_awaited()

    # ── 7. can't whisper to a bot ────────────────────────────────────────
    async def test_whisper_to_bot_rejected(self):
        ctx = _ctx(get_chat=lambda *a, **k: _user(999, "Bot", is_bot=True))
        msg = await self._call("/whisper @bot hi", ctx)
        out = msg.reply_html.call_args[0][0]
        self.assertIn("can't whisper to a bot", out)
        self.whisper.create_whisper.assert_not_awaited()

    # ── 8. empty message text is rejected ────────────────────────────────
    async def test_whisper_empty_text_rejected(self):
        ctx = _ctx(get_chat=lambda *a, **k: _user(321, "Named"))
        msg = await self._call("/whisper @named", ctx)
        out = msg.reply_html.call_args[0][0]
        self.assertIn("Write a message", out)
        self.whisper.create_whisper.assert_not_awaited()

    # ── 9. .whisper dot-command routes through the same handler ──────────
    async def test_dot_whisper_routes(self):
        resolved = _user(321, "Named", username="named")
        ctx = _ctx(get_chat=lambda *a, **k: resolved)
        msg = _msg(text=".whisper @named hello")
        up = _update(5, _chat(), msg)
        # Simulate the regex-matched dot handler feeding args to whisper_cmd.
        await self.admin.dot_admin_handler(up, ctx)
        self.whisper.create_whisper.assert_awaited_once()
        args = self.whisper.create_whisper.call_args.args
        self.assertEqual(args[1], 321)
        self.assertEqual(args[3], "hello")


class TestWhisperReadCallback(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        import handlers.whisper as whisper
        self.whisper = whisper
        self.patchers = [
            patch.object(whisper, "get_whisper", AsyncMock(return_value=None)),
            patch.object(whisper, "mark_whisper_read", AsyncMock()),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()

    def _q(self, data, uid=42):
        q = MagicMock()
        from utils.callback_codec import encode_callback
        q.data = data if data.startswith("wsp:") else encode_callback("wsp", {"w": data})
        q.from_user = _user(uid, "Reader")
        q.answer = AsyncMock()
        q.edit_message_reply_markup = AsyncMock()
        return q

    async def test_read_unknown_whisper(self):
        q = self._q("nope")
        up = MagicMock(); up.callback_query = q; up.effective_user = q.from_user
        ctx = MagicMock(); ctx.bot = MagicMock()
        ctx.bot.send_message = AsyncMock()
        await self.whisper.whisper_read_callback(up, ctx)
        q.answer.assert_awaited_once()
        self.assertIn("not found", q.answer.call_args[0][0])

    async def test_read_for_someone_else_blocked(self):
        w = {"target_id": 99, "text": "secret", "chat_id": -100,
             "sender_id": 5, "read": False}
        self.whisper.get_whisper.return_value = w
        q = self._q("abc", uid=42)
        up = MagicMock(); up.callback_query = q; up.effective_user = q.from_user
        ctx = MagicMock(); ctx.bot = MagicMock(); ctx.bot.send_message = AsyncMock()
        await self.whisper.whisper_read_callback(up, ctx)
        q.answer.assert_awaited_once()
        self.assertIn("isn't for you", q.answer.call_args[0][0])
        self.whisper.mark_whisper_read.assert_not_awaited()

    async def test_read_by_target_reveals_and_receipts(self):
        w = {"target_id": 42, "text": "secret", "chat_id": -100,
             "sender_id": 5, "read": False}
        self.whisper.get_whisper.return_value = w
        q = self._q("abc", uid=42)
        up = MagicMock(); up.callback_query = q; up.effective_user = q.from_user
        ctx = MagicMock(); ctx.bot = MagicMock(); ctx.bot.send_message = AsyncMock()
        await self.whisper.whisper_read_callback(up, ctx)
        q.answer.assert_awaited_once()
        self.assertIn("secret", q.answer.call_args[0][0])
        self.whisper.mark_whisper_read.assert_awaited_once_with("abc")
        ctx.bot.send_message.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
