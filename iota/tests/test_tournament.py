"""Tests for the generic tournament bracket + spectator helpers."""
import asyncio
import random
import unittest

from utils.tournament import (
    build_bracket, current_matches, report_winner, champion,
)
from utils.spectator import add_watcher, remove_watcher, is_watching, notify_watchers


def _ids(n):
    return [f"u{i}" for i in range(n)]


class TestTournament(unittest.TestCase):
    def test_build_bracket_power_of_two(self):
        rounds = build_bracket(_ids(4))
        self.assertEqual(len(rounds), 2)
        self.assertEqual(len(rounds[0]), 2)
        self.assertEqual(len(rounds[1]), 1)
        self.assertTrue(all(m["winner"] is None for r in rounds for m in r))

    def test_build_bracket_pads_to_power_of_two(self):
        rounds = build_bracket(_ids(3))
        self.assertEqual(len(rounds), 2)
        self.assertEqual(len(rounds[0]), 2)
        byes = sum(1 for m in rounds[0] if m["p1"] is None or m["p2"] is None)
        self.assertEqual(byes, 1)

    def test_build_bracket_eight(self):
        rounds = build_bracket(_ids(8))
        self.assertEqual([len(r) for r in rounds], [4, 2, 1])

    def test_report_winner_advances_and_champion(self):
        players = _ids(4)
        rounds = build_bracket(players)
        m0, m1 = rounds[0][0], rounds[0][1]
        w0, w1 = m0["p1"], m1["p1"]
        self.assertTrue(report_winner(rounds, 0, w0))
        self.assertTrue(report_winner(rounds, 1, w1))
        self.assertEqual(rounds[1][0]["p1"], w0)
        self.assertEqual(rounds[1][0]["p2"], w1)
        self.assertIsNone(champion(rounds))
        self.assertTrue(report_winner(rounds, 0, w0))
        self.assertEqual(champion(rounds), w0)

    def test_report_winner_invalid(self):
        players = _ids(4)
        rounds = build_bracket(players)
        self.assertFalse(report_winner(rounds, 0, "nobody"))
        self.assertFalse(report_winner(rounds, 99, players[0]))

    def test_report_winner_twice_rejected(self):
        players = _ids(4)
        rounds = build_bracket(players)
        w0 = rounds[0][0]["p1"]
        l0 = rounds[0][0]["p2"]
        self.assertTrue(report_winner(rounds, 0, w0))
        self.assertFalse(report_winner(rounds, 0, l0))

    def test_current_matches_returns_first_unresolved(self):
        rounds = build_bracket(_ids(4))
        self.assertIs(current_matches(rounds), rounds[0])
        for i, m in enumerate(rounds[0]):
            report_winner(rounds, i, m["p1"])
        self.assertIs(current_matches(rounds), rounds[1])


class TestSpectator(unittest.TestCase):
    def test_add_remove(self):
        game = {"watchers": set()}
        self.assertTrue(add_watcher(game, 1))
        self.assertFalse(add_watcher(game, 1))
        self.assertTrue(is_watching(game, 1))
        self.assertTrue(remove_watcher(game, 1))
        self.assertFalse(is_watching(game, 1))

    def test_notify_dms_each(self):
        game = {"watchers": {7, 8}}
        bot = _FakeBot()
        asyncio.run(notify_watchers(bot, game, "board"))
        self.assertEqual(len(bot.sent), 2)
        self.assertEqual({c for c, _ in bot.sent}, {7, 8})

    def test_notify_excludes(self):
        game = {"watchers": {7, 8}}
        bot = _FakeBot()
        asyncio.run(notify_watchers(bot, game, "x", exclude=8))
        self.assertEqual([c for c, _ in bot.sent], [7])


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


if __name__ == "__main__":
    unittest.main()
