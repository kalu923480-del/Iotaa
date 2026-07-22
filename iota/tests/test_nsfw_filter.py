"""
Pure unit tests for utils/nsfw_filter.py.

No Telegram, no DB, no network — just scoring logic.
"""
from __future__ import annotations

import pytest

from utils.nsfw_filter import (
    DEFAULT_NSFW,
    merge_nsfw_settings,
    score_from_set_name,
    score_from_emoji,
    combine_score,
    should_delete,
)


# ── score_from_set_name ────────────────────────────────────────────────

@pytest.mark.parametrize("name,expected_pts", [
    ("nsfw_stickers_v2", 92),
    ("hentai_collection", 92),
    ("pornhub_stickers", 92),
    ("xxx_pack", 92),
    ("adult_only_fun", 92),
    ("erotic_dreams", 92),
    ("sex_stickers", 92),
    ("nude_art", 92),
    ("lewd_memes", 92),
    ("ecchi_love", 92),
    ("r34_art", 92),
    ("futanari_pack", 92),
    ("pussy_cats", 92),
    ("dick_pics", 92),
    ("boob_fun", 92),
    ("tits_out", 92),
    ("ass_butts", 92),
    ("yaoi_boys", 92),
    ("gore_gore", 92),
    ("18+_pack", 92),
    ("18plus_fun", 92),
    ("yuri_nsfw", 92),
    ("bdsm_stickers", 92),
    ("mature_content", 35),
    ("seductive_look", 35),
    ("hot_girls", 35),
    ("sfw_or_nsfw", 35),
    ("adult_fun", 35),
    ("18_plus", 35),
    ("happy_stickers", 0),
    ("cute_cats", 0),
    ("", 0),
    (None, 0),
])
def test_strong_set_name_scores(name, expected_pts):
    pts, _ = score_from_set_name(name)
    assert pts == expected_pts


# ── score_from_emoji ──────────────────────────────────────────────────

def test_weak_emoji_alone_scores_below_50():
    pts, reasons = score_from_emoji("🍑")
    assert pts < 50
    assert reasons


def test_safe_emoji_scores_zero():
    pts, _ = score_from_emoji("😂")
    assert pts == 0


def test_nsfw_emoji_emoji_caps_at_40():
    pts, _ = score_from_emoji("🍆")
    assert pts <= 40


def test_nsfw_emoji_underwear():
    pts, _ = score_from_emoji("underwear")
    assert pts > 0


def test_none_emoji():
    pts, _ = score_from_emoji(None)
    assert pts == 0


def test_empty_emoji():
    pts, _ = score_from_emoji("")
    assert pts == 0


# ── allowlist / banlist ────────────────────────────────────────────────

def test_allowlist_forces_score_zero():
    settings = {"nsfw_set_allowlist": ["nsfw_stickers_v2"]}
    result = combine_score(set_name="nsfw_stickers_v2", settings=settings)
    assert result["score"] == 0
    assert result["allow"] is True


def test_banlist_forces_score_100():
    settings = {"nsfw_set_banlist": ["banned_pack"]}
    result = combine_score(set_name="banned_pack", settings=settings)
    assert result["score"] == 100
    assert result["forced"] is True


# ── should_delete threshold logic ─────────────────────────────────────

def test_should_delete_at_threshold():
    result = {"score": 90, "reasons": ["test"], "forced": False, "allow": False}
    assert should_delete(result, 90) is True


def test_should_delete_above_threshold():
    result = {"score": 95, "reasons": ["test"], "forced": False, "allow": False}
    assert should_delete(result, 90) is True


def test_should_delete_below_threshold():
    result = {"score": 89, "reasons": ["test"], "forced": False, "allow": False}
    assert should_delete(result, 90) is False


def test_should_delete_allowlist_wins():
    result = {"score": 100, "reasons": ["id_allowlist"], "forced": False, "allow": True}
    assert should_delete(result, 90) is False


def test_should_delete_forced_high_score():
    result = {"score": 92, "reasons": ["set_name_strong"], "forced": True, "allow": False}
    assert should_delete(result, 90) is True


# ── combine_score single family caps at 49 ────────────────────────────

def test_single_weak_family_caps_at_49():
    settings = {
        "nsfw_set_allowlist": [],
        "nsfw_set_banlist": [],
        "nsfw_id_allowlist": [],
        "nsfw_id_banlist": [],
    }
    # emoji only — single weak family
    result = combine_score(emoji="🍆", settings=settings)
    assert result["score"] <= 49
    assert result["forced"] is False


# ── merge_nsfw_settings ───────────────────────────────────────────────

def test_merge_defaults():
    out = merge_nsfw_settings(None)
    # Fully automatic: ON by default, high threshold, photos+stickers
    assert out["nsfw_enabled"] is True
    assert out["nsfw_threshold"] == 90
    assert out["nsfw_scan_photos"] is True
    assert out["nsfw_scan_stickers"] is True
    assert out["nsfw_action"] == "delete"
    assert out["nsfw_notify"] is False


def test_merge_overrides():
    out = merge_nsfw_settings({"nsfw_enabled": True, "nsfw_threshold": 80})
    assert out["nsfw_enabled"] is True
    assert out["nsfw_threshold"] == 80


def test_merge_clamps_threshold():
    out = merge_nsfw_settings({"nsfw_threshold": 120})
    assert out["nsfw_threshold"] == 99
    # Floor is 90 in automatic mode (never soft enough for false positives)
    out2 = merge_nsfw_settings({"nsfw_threshold": 10})
    assert out2["nsfw_threshold"] == 90


def test_merge_list_defaults():
    out = merge_nsfw_settings({})
    assert out["nsfw_set_banlist"] == []
    assert out["nsfw_set_allowlist"] == []
    assert out["nsfw_id_banlist"] == []
    assert out["nsfw_id_allowlist"] == []


def test_default_nsfw_keys():
    expected_keys = {
        "nsfw_enabled", "nsfw_threshold", "nsfw_scan_stickers",
        "nsfw_scan_photos", "nsfw_action", "nsfw_mute_secs",
        "nsfw_notify", "nsfw_set_allowlist", "nsfw_set_banlist",
        "nsfw_id_banlist", "nsfw_id_allowlist",
    }
    assert expected_keys.issubset(set(DEFAULT_NSFW.keys()))
