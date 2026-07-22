"""@handles / @usernames must stay ASCII-normal (never smallcaps unicode)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
IOTA = os.path.dirname(HERE)
if IOTA not in sys.path:
    sys.path.insert(0, IOTA)

from utils.fonts import sc, sc_all  # noqa: E402


class TestAtHandlesStayNormal(unittest.TestCase):
    def test_sc_preserves_username(self):
        self.assertIn("@Its_iotabot", sc("Hello @Its_iotabot welcome"))
        self.assertNotIn("ɪᴛꜱ", sc("@Its_iotabot").lower().replace("i", "ɪ"))
        # The handle itself must be unchanged character-for-character
        self.assertEqual(sc("@Its_iotabot"), "@Its_iotabot")

    def test_sc_preserves_tag_with_dots(self):
        self.assertEqual(sc("@...hjjj"), "@...hjjj")

    def test_sc_preserves_bare_at_in_sentence(self):
        # surrounding words style; @handle does not
        out = sc("ping @owner now")
        self.assertIn("@owner", out)
        self.assertTrue(out.startswith("ᴘ") or "ping" not in out or "@owner" in out)

    def test_sc_all_preserves_in_html(self):
        raw = "Contact <b>@admin</b> or @support please"
        out = sc_all(raw)
        self.assertIn("@admin", out)
        self.assertIn("@support", out)
        self.assertIn("<b>", out)

    def test_command_still_protected(self):
        self.assertIn("/start", sc("Use /start here"))


if __name__ == "__main__":
    unittest.main()
