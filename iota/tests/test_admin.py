"""
Unit tests for the .promote / .demote admin commands.

These prove the handlers:
  * never leak raw Telegram errors like "Chat_admin_required"
  * show a clear message when the BOT is not an admin / lacks rights
  * refuse to touch the group owner or the bot itself
  * parse the level (1/2/3) correctly and grant the right rights
Run:  python -m unittest tests.test_admin -v   (from the iota/ folder)
"""
import asyncio
import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Make the iota package importable regardless of cwd.
HERE = os.path.dirname(os.path.abspath(__file__))
IOTA = os.path.dirname(HERE)
if IOTA not in sys.path:
    sys.path.insert(0, IOTA)

from telegram import ChatMember, ChatAdministratorRights  # noqa: E402


def _user(uid, name="U", is_bot=False):
    u = MagicMock()
    u.id = uid
    u.full_name = name
    u.first_name = name
    u.is_bot = is_bot
    return u


def _member(status, **rights):
    m = MagicMock()
    m.status = status
    for k, v in rights.items():
        setattr(m, k, v)
    return m


def _msg(text="", reply_user=None):
    msg = MagicMock()
    msg.text = text
    msg.message_id = 1
    msg.delete = AsyncMock()
    msg.reply_html = AsyncMock()
    msg.pin = AsyncMock()
    if reply_user is not None:
        rt = MagicMock()
        rt.from_user = _user(reply_user, "Target")
        msg.reply_to_message = rt
    else:
        msg.reply_to_message = None
    return msg


def _update(uid, chat_id, msg):
    up = MagicMock()
    up.effective_message = msg
    up.effective_user = _user(uid, "Caller")
    c = MagicMock()
    c.id = chat_id
    c.title = "TestGroup"
    c.type = "supergroup"
    up.effective_chat = c
    return up


def _ctx(bot_id, get_chat_member=None, get_chat=None,
         get_chat_administrators=None, promote=None):
    ctx = MagicMock()
    bot = MagicMock()
    bot.id = bot_id
    bot.get_chat_member = AsyncMock(side_effect=get_chat_member or (lambda *a, **k: _member("member")))
    bot.get_chat = AsyncMock(side_effect=get_chat or (lambda *a, **k: _user(999, "Resolved")))
    bot.get_chat_administrators = AsyncMock(
        side_effect=get_chat_administrators or (lambda *a, **k: []))
    bot.promote_chat_member = AsyncMock(side_effect=promote or (lambda *a, **k: None))
    bot.set_chat_administrator_custom_title = AsyncMock()
    bot.unban_chat_member = AsyncMock()
    bot.ban_chat_member = AsyncMock()
    ctx.bot = bot
    return ctx


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestAdminPromoteDemote(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # Import lazily so sys.path is ready, and patch mongo helpers.
        import handlers.admin as admin
        self.admin = admin
        self.patchers = [
            patch.object(admin, "track_promotion", AsyncMock()),
            patch.object(admin, "remove_promotion", AsyncMock()),
            patch.object(admin, "get_user_by_username", AsyncMock(return_value=None)),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()

    def _call(self, fn, rest, ctx, reply_user=None, chat_id=-100, caller=5):
        msg = _msg(text=".x " + rest, reply_user=reply_user)
        up = _update(caller, chat_id, msg)
        _run(fn(up, ctx, rest))
        return msg

    # ── 1. Bot is a plain member: friendly message, no API call ──────────
    def test_demote_bot_not_admin(self):
        ctx = _ctx(bot_id=777, get_chat_member=lambda *a, **k: _member("member"))
        msg = self._call(self.admin._demote, "", ctx, reply_user=42)
        out = msg.reply_html.call_args[0][0]
        self.assertIn("Make me an admin", out)
        self.assertNotIn("Chat_admin_required", out)
        ctx.bot.promote_chat_member.assert_not_called()

    # ── 2. Bot is admin but lacks Add-Admins right ──────────────────────
    def test_demote_bot_admin_no_promote_right(self):
        def gcm(*a, **k):
            uid = a[1] if len(a) > 1 else k.get("user_id")
            if uid == 777:
                return _member("administrator", can_promote_members=False)
            return _member("administrator")
        ctx = _ctx(bot_id=777, get_chat_member=gcm)
        msg = self._call(self.admin._demote, "", ctx, reply_user=42)
        out = msg.reply_html.call_args[0][0]
        self.assertIn("Add New Admins", out)
        ctx.bot.promote_chat_member.assert_not_called()

    # ── 3. promote 1 (reply) -> Junior Admin, promote_chat_member called ─
    def test_promote_1_reply_junior(self):
        def gcm(*a, **k):
            uid = a[1] if len(a) > 1 else k.get("user_id")
            if uid == 777:
                return _member("administrator", can_promote_members=True,
                               **{f"can_{r}": True for r in
                                  ["manage_chat", "delete_messages",
                                   "manage_video_chats", "restrict_members",
                                   "change_info", "invite_users", "pin_messages",
                                   "post_messages", "edit_messages",
                                   "post_stories", "edit_stories",
                                   "delete_stories", "manage_topics"]})
            return _member("member")
        ctx = _ctx(bot_id=777, get_chat_member=gcm)
        msg = self._call(self.admin._promote, "1", ctx, reply_user=42)
        ctx.bot.promote_chat_member.assert_called_once()
        # Title "Junior Admin" is passed to Telegram (sc() only styles output).
        title = ctx.bot.set_chat_administrator_custom_title.call_args[0][2]
        self.assertEqual(title, "Junior Admin")
        kw = ctx.bot.promote_chat_member.call_args.kwargs
        # Junior grants delete + restrict, NOT promote.
        self.assertTrue(kw["can_delete_messages"])
        self.assertTrue(kw["can_restrict_members"])
        self.assertFalse(kw["can_promote_members"])

    # ── 4. promote 3 -> Full Admin incl. can_promote_members ─────────────
    def test_promote_3_full_admin(self):
        def gcm(*a, **k):
            uid = a[1] if len(a) > 1 else k.get("user_id")
            if uid == 777:
                return _member("administrator", can_promote_members=True,
                               **{f"can_{r}": True for r in
                                  ["manage_chat", "delete_messages",
                                   "manage_video_chats", "restrict_members",
                                   "change_info", "invite_users", "pin_messages",
                                   "post_messages", "edit_messages",
                                   "post_stories", "edit_stories",
                                   "delete_stories", "manage_topics"]})
            return _member("member")
        ctx = _ctx(bot_id=777, get_chat_member=gcm)
        msg = self._call(self.admin._promote, "3", ctx, reply_user=42)
        title = ctx.bot.set_chat_administrator_custom_title.call_args[0][2]
        self.assertEqual(title, "Full Admin")
        kw = ctx.bot.promote_chat_member.call_args.kwargs
        self.assertTrue(kw["can_promote_members"])

    # ── 5. Promoting the group owner is refused (no API call) ────────────
    def test_promote_owner_refused(self):
        creator_id = 999
        def gcm(*a, **k):
            uid = a[1] if len(a) > 1 else k.get("user_id")
            if uid == 777:
                return _member("administrator", can_promote_members=True)
            return _member("member")
        def gadm(*a, **k):
            return [_member("creator", user=_user(creator_id, "Owner"))]
        ctx = _ctx(bot_id=777, get_chat_member=gcm, get_chat_administrators=gadm)
        msg = self._call(self.admin._promote, "", ctx, reply_user=creator_id)
        out = msg.reply_html.call_args[0][0]
        self.assertIn("group owner", out.lower())
        ctx.bot.promote_chat_member.assert_not_called()

    # ── 6. .promote @user 2 (no reply) resolves by username, level 2 ────
    def test_promote_username_level2(self):
        resolved_id = 4242
        def gcm(*a, **k):
            uid = a[1] if len(a) > 1 else k.get("user_id")
            if uid == 777:
                return _member("administrator", can_promote_members=True,
                               **{f"can_{r}": True for r in
                                  ["manage_chat", "delete_messages",
                                   "manage_video_chats", "restrict_members",
                                   "change_info", "invite_users", "pin_messages",
                                   "post_messages", "edit_messages",
                                   "post_stories", "edit_stories",
                                   "delete_stories", "manage_topics"]})
            return _member("member")
        def gchat(*a, **k):
            u = _user(resolved_id, "ByName")
            return u
        ctx = _ctx(bot_id=777, get_chat_member=gcm, get_chat=gchat)
        msg = _msg(text=".promote @user 2")
        up = _update(5, -100, msg)
        _run(self.admin._promote(up, ctx, "@user 2"))
        ctx.bot.promote_chat_member.assert_called_once()
        args = ctx.bot.promote_chat_member.call_args.args
        self.assertEqual(args[1], resolved_id)  # user_id is 2nd positional arg
        title = ctx.bot.set_chat_administrator_custom_title.call_args[0][2]
        self.assertEqual(title, "Senior Admin")  # level 2 title

    # ── 7. Bot OWNER with no target promotes themselves (even if a normal
    #        member of the group) when the bot has the Add-Admins right. ──
    def test_owner_self_promote_no_target(self):
        from config import OWNER_ID
        def gcm(*a, **k):
            uid = a[1] if len(a) > 1 else k.get("user_id")
            if uid == 777:
                return _member("administrator", can_promote_members=True,
                               **{f"can_{r}": True for r in
                                  ["manage_chat", "delete_messages",
                                   "manage_video_chats", "restrict_members",
                                   "change_info", "invite_users", "pin_messages",
                                   "post_messages", "edit_messages",
                                   "post_stories", "edit_stories",
                                   "delete_stories", "manage_topics"]})
            return _member("member")
        ctx = _ctx(bot_id=777, get_chat_member=gcm)
        msg = _msg(text=".promote")
        up = _update(OWNER_ID, -100, msg)  # owner is a normal member here
        _run(self.admin._promote(up, ctx, ""))
        ctx.bot.promote_chat_member.assert_called_once()
        args = ctx.bot.promote_chat_member.call_args.args
        self.assertEqual(args[1], OWNER_ID)  # promoted self


    # ── 8. .demote_all demotes EVERY admin, including those promoted by
    #        other admins / the owner — not just Iota's tracked ones. ──────
    def test_demote_all_includes_others_promotions(self):
        bot_id = 777
        # creator (untouched), the bot herself (untouched), an admin
        # Iota promoted, and an admin promoted by a HUMAN/other admin.
        def gadm(*a, **k):
            return [
                _member("creator", user=_user(999, "Owner")),
                _member("administrator", user=_user(bot_id, "Iota")),
                _member("administrator", user=_user(111, "ByIota")),
                _member("administrator", user=_user(222, "ByOtherAdmin")),
            ]
        def gcm(*a, **k):
            uid = a[1] if len(a) > 1 else k.get("user_id")
            if uid == bot_id:
                return _member("administrator", can_promote_members=True)
            return _member("member")
        ctx = _ctx(bot_id=bot_id, get_chat_member=gcm,
                   get_chat_administrators=gadm)
        msg = _msg(text=".demote_all")
        up = _update(5, -100, msg)
        _run(self.admin._demote_all(up, ctx))
        # Only the two real human admins get demoted (creator + bot skipped).
        self.assertEqual(ctx.bot.promote_chat_member.call_count, 2)
        demoted_ids = {c.args[1] for c in ctx.bot.promote_chat_member.call_args_list}
        self.assertEqual(demoted_ids, {111, 222})
        out = msg.reply_html.call_args[0][0]
        self.assertIn("2", out)  # two admins demoted


if __name__ == "__main__":
    unittest.main()
