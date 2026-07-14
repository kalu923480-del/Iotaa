"""
Tests that owner-only command categories are NOT exposed to ordinary users
in the public /commands catalog (menu, category view, and full-file download),
while remaining visible to the owner / sudo staff.

Pure unit checks over handlers.commands_list._visible_categories — no Telegram
or DB needed.
"""
import os

os.environ.setdefault("BOT_TOKEN", "123456:fake")
os.environ.setdefault("OWNER_ID", "111111")
os.environ.setdefault(
    "MONGO_URI", "mongodb+srv://test:test@cluster0.mongodb.net/iota_bot"
)

import unittest

from handlers import commands_list as cl


class TestOwnerCategoryVisibility(unittest.TestCase):
    def test_owner_cats_constant(self):
        self.assertEqual(cl.OWNER_ONLY_CATS, {"Owner", "Owner Systems"})

    def test_non_privileged_hidden(self):
        cats = cl._visible_categories(privileged=False)
        self.assertNotIn("Owner", cats)
        self.assertNotIn("Owner Systems", cats)
        # a normal category is still present
        self.assertIn("Economy", cats)
        self.assertIn("Premium Banking", cats)

    def test_privileged_visible(self):
        cats = cl._visible_categories(privileged=True)
        self.assertIn("Owner", cats)
        self.assertIn("Owner Systems", cats)
        # owner categories carry the real owner commands
        self.assertTrue(len(cats["Owner"]) > 0)
        self.assertTrue(len(cats["Owner Systems"]) > 0)

    def test_full_file_excludes_owner_for_normal(self):
        # The downloadable file is built from _visible_categories, so a normal
        # user's export must never contain the owner-only commands — including
        # ones that used to be miscategorised under Stats/Stickers/Info.
        cats = cl._visible_categories(privileged=False)
        flat = [cmd for cmds in cats.values() for cmd, _ in cmds]
        for owner_cmd in (
            "lockdown", "massban", "sudoadd", "shieldstatus",
            "growth", "health", "online", "botstats",
            "userinfo", "addsticker", "previewsticker",
        ):
            self.assertNotIn(owner_cmd, flat)
        # user-facing categories must still be present (not emptied)
        for c in ("Stats", "Stickers", "Info", "Economy"):
            self.assertIn(c, cats)


if __name__ == "__main__":
    unittest.main()
