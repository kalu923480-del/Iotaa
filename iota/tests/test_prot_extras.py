"""
Unit tests for the pure protection helpers (no telegram imports required).

Run: python -m unittest tests.test_prot_extras -v   (from the iota/ folder)
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
IOTA = os.path.dirname(HERE)
if IOTA not in sys.path:
    sys.path.insert(0, IOTA)

from handlers.protection import (
    word_matches,
    caps_ratio,
    zalgo_score,
    count_mentions,
)


class TestWordMatches(unittest.TestCase):
    def test_contains_match(self):
        self.assertTrue(word_matches("contains", "badword", "this is a badword here"))

    def test_contains_no_match(self):
        self.assertFalse(word_matches("contains", "badword", "this is fine"))

    def test_contains_case_insensitive(self):
        self.assertTrue(word_matches("contains", "badword", "BadWord is here"))

    def test_exact_match(self):
        self.assertTrue(word_matches("exact", "bad", "bad is here"))

    def test_exact_no_substring_match(self):
        self.assertFalse(word_matches("exact", "bad", "badword is here"))

    def test_exact_case_insensitive(self):
        self.assertTrue(word_matches("exact", "bad", "BAD is here"))

    def test_regex_match(self):
        self.assertTrue(word_matches("regex", r"b[ad]+", "bad is here"))

    def test_regex_no_match(self):
        self.assertFalse(word_matches("regex", r"b[ad]+", "xyz is here"))

    def test_regex_case_insensitive(self):
        self.assertTrue(word_matches("regex", r"hello", "HELLO WORLD"))

    def test_invalid_regex_falls_back_false(self):
        self.assertFalse(word_matches("regex", r"[invalid(", "anything"))


class TestCapsRatio(unittest.TestCase):
    def test_all_upper(self):
        self.assertAlmostEqual(caps_ratio("ABC"), 1.0)

    def test_all_lower(self):
        self.assertAlmostEqual(caps_ratio("abc"), 0.0)

    def test_mixed(self):
        text = "AbC"
        self.assertAlmostEqual(caps_ratio(text), 2 / 3)

    def test_no_letters(self):
        self.assertAlmostEqual(caps_ratio("123!@#"), 0.0)

    def test_empty(self):
        self.assertAlmostEqual(caps_ratio(""), 0.0)

    def test_numeric_preserved(self):
        text = "ABC123def"
        letters = [c for c in text if c.isalpha()]
        self.assertAlmostEqual(caps_ratio(text), 3 / 6)


class TestZalgoScore(unittest.TestCase):
    def test_no_combining(self):
        self.assertEqual(zalgo_score("hello world"), 0)

    def test_single_combining(self):
        self.assertEqual(zalgo_score("h\u0308ello"), 1)

    def test_multiple_combining(self):
        text = "h\u0308e\u0301l\u0302l\u0308o\u0303"
        self.assertEqual(zalgo_score(text), 5)

    def test_empty(self):
        self.assertEqual(zalgo_score(""), 0)


class TestCountMentions(unittest.TestCase):
    def _entity(self, type_name):
        class E:
            pass
        e = E()
        t = E()
        t.name = type_name
        e.type = t
        return e

    def test_count_bare_mentions(self):
        self.assertEqual(count_mentions("@user hello", []), 1)

    def test_count_short_ignored(self):
        self.assertEqual(count_mentions("@u hello", []), 0)

    def test_count_entity_mentions(self):
        ents = [self._entity("mention")]
        self.assertEqual(count_mentions("hello", ents), 1)

    def test_count_text_mention(self):
        ents = [self._entity("text_mention")]
        self.assertEqual(count_mentions("hello", ents), 1)

    def test_count_both(self):
        ents = [self._entity("mention")]
        text = "hi @there"
        self.assertEqual(count_mentions(text, ents), 2)

    def test_no_duplicate_for_entity_and_bare(self):
        ents = [self._entity("mention")]
        text = "@same @same"
        self.assertEqual(count_mentions(text, ents), 2)


if __name__ == "__main__":
    unittest.main()
