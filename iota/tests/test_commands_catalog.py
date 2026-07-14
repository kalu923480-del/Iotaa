"""
Tests for the /commands catalog + DM-redirect system.
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

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


class TestCommandCatalog(unittest.TestCase):
    def test_catalog_loads(self):
        import utils.command_catalog as c
        cats = c.all_categories()
        self.assertTrue(len(cats) > 10)
        total = c.total_documented()
        self.assertGreaterEqual(total, 400)  # user asked for 400+ listed
        # every entry has a category + use case
        for cmd, (cat, uc) in c.CATALOG.items():
            self.assertTrue(cat)
            self.assertTrue(uc)

    def test_usecase_fallback(self):
        import utils.command_catalog as c
        self.assertEqual(c.get_category("definitely_not_a_real_cmd"), "Misc")
        self.assertTrue(c.get_usecase("definitely_not_a_real_cmd"))


class TestRequireDm(unittest.TestCase):
    def _update(self, chat_type):
        msg = AsyncMock()
        msg.reply_html = AsyncMock()
        chat = AsyncMock()
        chat.type = chat_type
        upd = AsyncMock()
        upd.effective_chat = chat
        upd.effective_message = msg
        return upd, msg

    def test_group_redirects(self):
        from utils.dm_redirect import require_dm
        upd, msg = self._update("group")
        ctx = AsyncMock()
        res = _run(require_dm(upd, ctx, "/pay", "pay"))
        self.assertFalse(res)
        self.assertTrue(msg.reply_html.called)
        # the clickable DM button must be attached
        kw = msg.reply_html.call_args.kwargs
        self.assertIn("reply_markup", kw)
        self.assertIn("Open in DM", str(kw["reply_markup"].inline_keyboard))

    def test_private_passes(self):
        from utils.dm_redirect import require_dm
        upd, msg = self._update("private")
        ctx = AsyncMock()
        res = _run(require_dm(upd, ctx, "/pay", "pay"))
        self.assertTrue(res)
        self.assertFalse(msg.reply_html.called)


class TestCommandsCmd(unittest.TestCase):
    def _update(self, chat_type):
        msg = AsyncMock()
        msg.reply_html = AsyncMock()
        msg.reply_document = AsyncMock()
        chat = AsyncMock()
        chat.type = chat_type
        user = AsyncMock()
        user.id = 111111
        upd = AsyncMock()
        upd.effective_user = user
        upd.effective_chat = chat
        upd.effective_message = msg
        return upd, msg

    def test_group_redirects_to_dm(self):
        import handlers.commands_list as m
        upd, msg = self._update("supergroup")
        ctx = AsyncMock()
        _run(m.commands_cmd(upd, ctx))
        self.assertTrue(msg.reply_html.called)
        self.assertIn("Open in DM", str(msg.reply_html.call_args.kwargs["reply_markup"].inline_keyboard))

    def test_dm_shows_menu(self):
        import handlers.commands_list as m
        upd, msg = self._update("private")
        ctx = AsyncMock()
        _run(m.commands_cmd(upd, ctx))
        self.assertTrue(msg.reply_html.called)
        self.assertIn("Command Catalog", msg.reply_html.call_args[0][0])

    def test_download_sends_file(self):
        import handlers.commands_list as m
        q = AsyncMock()
        q.answer = AsyncMock()
        q.effective_message = None  # a real CallbackQuery has no effective_message
        msg = AsyncMock()
        msg.reply_document = AsyncMock()
        q.message = msg
        _run(m._send_full_file(q))
        self.assertTrue(msg.reply_document.called)


if __name__ == "__main__":
    unittest.main()
