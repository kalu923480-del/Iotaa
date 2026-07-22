"""Tests for utils/group_session.py — pure unit, no live Telegram."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils import group_session as gs


@pytest.fixture(autouse=True)
def _clear_active():
    gs._active.clear()
    yield
    gs._active.clear()


def test_set_active_group_cache():
    asyncio.get_event_loop().run_until_complete(gs.set_active_group(1, 42))
    assert gs._active[1] == 42


def test_get_active_group_from_cache():
    gs._active[7] = 99
    result = asyncio.get_event_loop().run_until_complete(gs.get_active_group(7))
    assert result == 99


def test_get_active_group_from_db():
    async def _run():
        fake_doc = {"_id": 8, "active_chat_id": 55, "updated_at": 0}
        db = MagicMock()
        db.group_sessions.find_one = AsyncMock(return_value=fake_doc)
        with patch("utils.group_session.get_db", return_value=db):
            result = await gs.get_active_group(8)
        assert result == 55
        assert gs._active[8] == 55

    asyncio.get_event_loop().run_until_complete(_run())


def test_get_active_group_missing():
    async def _run():
        db = MagicMock()
        db.group_sessions.find_one = AsyncMock(return_value=None)
        with patch("utils.group_session.get_db", return_value=db):
            result = await gs.get_active_group(999)
        assert result is None

    asyncio.get_event_loop().run_until_complete(_run())


def test_clear_active_group():
    gs._active[3] = 77

    async def _run():
        db = MagicMock()
        db.group_sessions.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
        with patch("utils.group_session.get_db", return_value=db):
            await gs.clear_active_group(3)
        assert 3 not in gs._active

    asyncio.get_event_loop().run_until_complete(_run())


def test_is_user_group_admin_owner():
    bot = MagicMock()
    result = asyncio.get_event_loop().run_until_complete(
        gs.is_user_group_admin(bot, 1, 999)
    )
    assert result is True


def test_is_user_group_admin_creator():
    bot = MagicMock()
    member = MagicMock()
    member.status = "creator"
    bot.get_chat_member = AsyncMock(return_value=member)

    result = asyncio.get_event_loop().run_until_complete(
        gs.is_user_group_admin(bot, 1, 2)
    )
    assert result is True


def test_is_user_group_admin_not_admin():
    bot = MagicMock()
    member = MagicMock()
    member.status = "member"
    bot.get_chat_member = AsyncMock(return_value=member)

    result = asyncio.get_event_loop().run_until_complete(
        gs.is_user_group_admin(bot, 1, 2)
    )
    assert result is False


def test_is_user_group_admin_api_error():
    from telegram.error import TelegramError
    bot = MagicMock()
    bot.get_chat_member = AsyncMock(side_effect=TelegramError("boom"))

    result = asyncio.get_event_loop().run_until_complete(
        gs.is_user_group_admin(bot, 1, 2)
    )
    assert result is False


def test_list_candidate_groups_for_user():
    async def _run():
        docs = [
            {"_id": 10, "title": "Group A", "tracked_at": 1},
            {"_id": 20, "title": "Group B", "tracked_at": 2},
        ]
        db = MagicMock()
        db.group_settings.find = MagicMock(return_value=MagicMock(
            sort=MagicMock(return_value=MagicMock(to_list=AsyncMock(return_value=docs)))
        ))

        bot = MagicMock()
        # user is admin of group 10, not 20
        cm10 = MagicMock(); cm10.status = "administrator"
        cm20 = MagicMock(); cm20.status = "member"
        bot.get_chat_member = AsyncMock(side_effect=lambda cid, uid: cm10 if cid == 10 else cm20)
        ch10 = MagicMock(); ch10.title = "Group A Live"
        bot.get_chat = AsyncMock(return_value=ch10)

        with patch("utils.group_session.get_db", return_value=db):
            result = await gs.list_candidate_groups_for_user(bot, 1)

        assert len(result) == 1
        assert result[0]["id"] == 10
        assert result[0]["title"] == "Group A Live"

    asyncio.get_event_loop().run_until_complete(_run())


def test_list_candidate_groups_respects_limit():
    async def _run():
        docs = [{"_id": i, "title": f"G{i}", "tracked_at": i} for i in range(100)]
        db = MagicMock()
        db.group_settings.find = MagicMock(return_value=MagicMock(
            sort=MagicMock(return_value=MagicMock(to_list=AsyncMock(return_value=docs)))
        ))

        bot = MagicMock()
        cm = MagicMock(); cm.status = "administrator"
        bot.get_chat_member = AsyncMock(return_value=cm)
        bot.get_chat = AsyncMock(side_effect=Exception("skip"))

        with patch("utils.group_session.get_db", return_value=db):
            result = await gs.list_candidate_groups_for_user(bot, 1, limit=5)

        assert len(result) == 5

    asyncio.get_event_loop().run_until_complete(_run())


def test_list_candidate_groups_db_error():
    async def _run():
        db = MagicMock()
        db.group_settings.find = MagicMock(side_effect=Exception("db down"))
        bot = MagicMock()

        with patch("utils.group_session.get_db", return_value=db):
            result = await gs.list_candidate_groups_for_user(bot, 1)

        assert result == []

    asyncio.get_event_loop().run_until_complete(_run())
