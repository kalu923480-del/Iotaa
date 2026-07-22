"""Unit tests for utils.linkban pure helpers (no Telegram / Mongo)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
IOTA = os.path.dirname(HERE)
if IOTA not in sys.path:
    sys.path.insert(0, IOTA)

from utils.linkban import (  # noqa: E402
    extract_link_hits,
    is_whitelisted,
    should_block_links,
    normalize_allow_entry,
    merge_linkban_settings,
)


class TestNormalize(unittest.TestCase):
    def test_domain(self):
        self.assertEqual(normalize_allow_entry("https://YouTube.com/watch"), "youtube.com")

    def test_at_user(self):
        self.assertEqual(normalize_allow_entry("@MyChannel"), "mychannel")

    def test_empty(self):
        self.assertEqual(normalize_allow_entry("  "), "")


class TestExtract(unittest.TestCase):
    def test_private_invite(self):
        hits = extract_link_hits("join https://t.me/+AbCdEf123 hello")
        self.assertTrue(any(h["kind"] == "invite" for h in hits))

    def test_public_username(self):
        hits = extract_link_hits("see t.me/SomeGroup now")
        self.assertTrue(any(h["kind"] == "username" and h["value"] == "somegroup" for h in hits))

    def test_joinchat(self):
        hits = extract_link_hits("https://t.me/joinchat/AAAAAEeeeee")
        self.assertTrue(any(h["kind"] == "invite" for h in hits))

    def test_generic_url(self):
        hits = extract_link_hits("check https://evil.example/path")
        self.assertTrue(any(h["kind"] == "url" and "evil.example" in h["value"] for h in hits))

    def test_no_links(self):
        self.assertEqual(extract_link_hits("hello world no links"), [])


class TestWhitelist(unittest.TestCase):
    def test_own_username(self):
        hit = {"kind": "username", "value": "mygroup", "match": "t.me/mygroup"}
        self.assertTrue(is_whitelisted(hit, [], own_username="MyGroup"))

    def test_allowlist_domain(self):
        hit = {"kind": "url", "value": "youtube.com", "match": "https://youtube.com/x"}
        self.assertTrue(is_whitelisted(hit, ["youtube.com"]))

    def test_not_allowed(self):
        hit = {"kind": "invite", "value": "abc", "match": "t.me/+abc"}
        self.assertFalse(is_whitelisted(hit, ["youtube.com"]))


class TestShouldBlock(unittest.TestCase):
    def test_disabled(self):
        blocked, hits = should_block_links(
            "https://t.me/+spam", enabled=False
        )
        self.assertFalse(blocked)
        self.assertEqual(hits, [])

    def test_blocks_foreign_invite(self):
        blocked, hits = should_block_links(
            "join https://t.me/+SpAmInViTe",
            enabled=True,
            allowlist=[],
            own_username="mygroup",
        )
        self.assertTrue(blocked)
        self.assertTrue(len(hits) >= 1)

    def test_allows_own_group(self):
        blocked, hits = should_block_links(
            "our group t.me/MyGroup rules",
            enabled=True,
            allowlist=[],
            own_username="mygroup",
            allow_own=True,
        )
        self.assertFalse(blocked)

    def test_urls_off_by_default(self):
        blocked, hits = should_block_links(
            "see https://google.com",
            enabled=True,
            block_urls=False,
        )
        self.assertFalse(blocked)

    def test_urls_on(self):
        blocked, hits = should_block_links(
            "see https://google.com",
            enabled=True,
            block_urls=True,
        )
        self.assertTrue(blocked)

    def test_allowlist_url(self):
        blocked, hits = should_block_links(
            "see https://youtube.com/watch?v=1",
            enabled=True,
            allowlist=["youtube.com"],
            block_urls=True,
        )
        self.assertFalse(blocked)


class TestMerge(unittest.TestCase):
    def test_defaults(self):
        m = merge_linkban_settings({})
        self.assertFalse(m["linkban_enabled"])
        self.assertEqual(m["linkban_mode"], "delete")
        self.assertEqual(m["link_allowlist"], [])

    def test_overlay(self):
        m = merge_linkban_settings({
            "linkban_enabled": True,
            "linkban_mode": "mute",
            "link_allowlist": ["a.com"],
        })
        self.assertTrue(m["linkban_enabled"])
        self.assertEqual(m["linkban_mode"], "mute")
        self.assertEqual(m["link_allowlist"], ["a.com"])


if __name__ == "__main__":
    unittest.main()
