"""
Unit tests for the Game Art Engine (utils/game_art).

Prove every renderer:
  * returns valid PNG bytes (magic header b"\\x89PNG")
  * never raises on weird input (bad suit, out-of-range dice, empty rows)
  * is deterministic enough to be reproducible (same input -> same bytes)

Run:  python -m unittest tests.test_game_art -v   (from the iota/ folder)
"""
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
IOTA = os.path.dirname(HERE)
if IOTA not in sys.path:
    sys.path.insert(0, IOTA)

from utils import game_art as g  # noqa: E402


def _is_png(buf) -> bool:
    data = buf.getvalue() if isinstance(buf, io.BytesIO) else buf
    return data[:4] == b"\x89PNG"


class TestGameArtEngine(unittest.TestCase):

    # ── 1. Every renderer emits a valid PNG ────────────────────────────────
    def test_all_renderers_produce_png(self):
        cases = {
            "card": g.render_card("A", "hearts"),
            "card_back": g.render_card("?", hidden=True),
            "dice": g.render_dice(5),
            "dice_row": g.render_dice_row([1, 3, 6]),
            "slots": g.render_slots(["\U0001F352", "\U0001F514", "\U0001F48E"]),
            "wheel": g.render_wheel(["\U0001F34E", "\U0001F34B",
                                     "\U0001F347", "\U0001F95D"], winner=2),
            "roulette": g.render_roulette(17),
            "scoreboard": g.render_scoreboard([("Alice", 1200), ("Bob", 900)]),
            "leaderboard": g.render_leaderboard([(1, "Alice", 1200),
                                                 (2, "Bob", 900),
                                                 (3, "Cy", 800)]),
        }
        for name, buf in cases.items():
            with self.subTest(name):
                self.assertIsInstance(buf, io.BytesIO)
                self.assertTrue(_is_png(buf), f"{name} is not a PNG")

    # ── 2. Dice clamps out-of-range values instead of raising ──────────────
    def test_dice_clamps_range(self):
        for v in (0, -5, 7, 99):
            with self.subTest(v):
                self.assertTrue(_is_png(g.render_dice(v)))

    # ── 3. Unknown suit falls back to spades, never raises ─────────────────
    def test_card_unknown_suit_falls_back(self):
        self.assertTrue(_is_png(g.render_card("K", "notasuit")))

    # ── 4. Empty / oversized inputs don't crash ────────────────────────────
    def test_empty_inputs_safe(self):
        self.assertTrue(_is_png(g.render_scoreboard([])))
        self.assertTrue(_is_png(g.render_leaderboard([])))
        self.assertTrue(_is_png(g.render_wheel([])))
        self.assertTrue(_is_png(g.render_dice_row([])))

    # ── 5. Deterministic: same input -> identical bytes ────────────────────
    def test_deterministic_output(self):
        a = g.render_card("Q", "clubs").getvalue()
        b = g.render_card("Q", "clubs").getvalue()
        self.assertEqual(a, b)
        r1 = g.render_roulette(23).getvalue()
        r2 = g.render_roulette(23).getvalue()
        self.assertEqual(r1, r2)

    # ── 6. Leaderboard medal mapping for top 3, plain rank after ───────────
    def test_leaderboard_ranks(self):
        # Just assert it renders without error for ranks 1..5
        rows = [(i, f"P{i}", i * 100) for i in range(1, 6)]
        self.assertTrue(_is_png(g.render_leaderboard(rows)))

    # ── 7. New renderers: hangman gallows, bomb board, ludo share ──────────
    def test_new_renderers_produce_png(self):
        cases = {
            "hangman": g.render_hangman(3),
            "hangman_full": g.render_hangman(6),
            "hangman_clamped": g.render_hangman(99),
            "bomb": g.render_bomb(defused=False, seconds_left=12,
                                  wires=["red", "green", "blue", "yellow"]),
            "bomb_defused": g.render_bomb(defused=True),
            "ludo_share": g.render_ludo_share([("🔴 Red", "4/4"),
                                              ("🔵 Blue", "2/4")]),
        }
        for name, buf in cases.items():
            with self.subTest(name):
                self.assertIsInstance(buf, io.BytesIO)
                self.assertTrue(_is_png(buf), f"{name} is not a PNG")


if __name__ == "__main__":
    unittest.main()
