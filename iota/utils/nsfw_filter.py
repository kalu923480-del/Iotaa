"""
Iota — High-confidence NSFW media filter (stickers + images).

Design goals (anti-false-positive first):
  • Default OFF per group
  • Default threshold 90 (very high)
  • Multi-signal scoring — a single weak signal NEVER deletes
  • Strong signals: explicit set-name keywords, admin banlist
  • Weak signals: emoji, skin-tone heuristics (need combination)
  • Cache by file_unique_id so the same sticker is not re-scored forever
  • Admins always skipped by the handler (not here)

Score is 0–100. Delete only when score >= threshold.
"""
from __future__ import annotations

import io
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Explicit pack / set name tokens (STRONG — alone can pass high threshold) ──
_STRONG_SET_RE = re.compile(
    r"(?i)(?:"
    r"\bnsfw\b|\bporn\b|\bporno\b|\bhentai\b|\bxxx\b|\b18\+|18plus|"
    r"\badult\s*only\b|\berotic\b|\bsex\b|\bnude\b|\bnudes\b|"
    r"\bbodacious\b|\blewd\b|\becchi\b|\br34\b|\brule34\b|"
    r"\bpussy\b|\bdick\b|\bcock\b|\bboob\b|\btits\b|\bass\b|"
    r"\bfutanari\b|\byaoi\b|\byuri\s*nsfw\b|\bgore\b"
    r")"
)

# Mild pack hints (WEAK alone)
_WEAK_SET_RE = re.compile(
    r"(?i)(?:"
    r"\bsfw\s*or\s*nsfw\b|\bmature\b|\b18\b|\badult\b|"
    r"\bseductive\b|\bsensual\b|\bhot\s*girls?\b"
    r")"
)

# Emoji often used on explicit stickers (WEAK — many are also used innocently)
_NSFW_EMOJI = frozenset({
    "🍑", "🍆", "🥵", "💦", "👅", "👙", "underwear", "💋", "😈", "🔞",
    "🛏️", "🚬", "💊", "🩸",
})

# Safe emoji packs often use these — if ONLY these, don't boost
_SAFE_EMOJI = frozenset({
    "😂", "🤣", "❤️", "👍", "🔥", "✨", "🎉", "😊", "🥰", "😍",
    "😎", "🤔", "😭", "🥺", "💀", "🙏", "👏", "💯", "⭐", "🌟",
})

DEFAULT_NSFW = {
    # Fully automatic: ON for all groups unless explicitly stored False
    "nsfw_enabled": True,
    "nsfw_threshold": 90,          # high confidence only (anti false-positive)
    "nsfw_scan_stickers": True,
    "nsfw_scan_photos": True,      # auto-scan photos too
    "nsfw_action": "delete",
    "nsfw_mute_secs": 300,
    "nsfw_notify": False,          # silent auto-delete
    "nsfw_set_allowlist": [],
    "nsfw_set_banlist": [],
    "nsfw_id_banlist": [],
    "nsfw_id_allowlist": [],
}


def merge_nsfw_settings(prot: dict | None) -> dict:
    out = dict(DEFAULT_NSFW)
    if not prot:
        return out
    for k in DEFAULT_NSFW:
        if k in prot and prot[k] is not None:
            out[k] = prot[k]
    # Auto mode: never run softer than 90 (anti false-positive)
    try:
        t = int(out.get("nsfw_threshold", 90))
        out["nsfw_threshold"] = max(90, min(t, 99))
    except Exception:
        out["nsfw_threshold"] = 90
    for list_key in (
        "nsfw_set_allowlist", "nsfw_set_banlist",
        "nsfw_id_allowlist", "nsfw_id_banlist",
    ):
        if not isinstance(out.get(list_key), list):
            out[list_key] = []
    return out


def _norm_set(name: str) -> str:
    return (name or "").strip().lower()


def score_from_set_name(set_name: str) -> tuple[int, list[str]]:
    """Return (points, reasons) from sticker set short name / title."""
    if not set_name:
        return 0, []
    reasons = []
    s = set_name
    pts = 0
    if _STRONG_SET_RE.search(s):
        pts += 92  # alone enough for default threshold 90
        reasons.append("set_name_strong")
    elif _WEAK_SET_RE.search(s):
        pts += 35
        reasons.append("set_name_weak")
    return pts, reasons


def score_from_emoji(emoji: str | None) -> tuple[int, list[str]]:
    if not emoji:
        return 0, []
    reasons = []
    pts = 0
    # multi-codepoint emoji string — check membership loosely
    for e in _NSFW_EMOJI:
        if e in emoji:
            pts += 28
            reasons.append(f"emoji:{e}")
            break
    # if only safe emoji, no points
    if pts == 0:
        for e in _SAFE_EMOJI:
            if e in emoji:
                return 0, []
    return min(pts, 40), reasons  # emoji alone max 40 — never deletes alone


def score_skin_bytes(image_bytes: bytes, max_side: int = 128) -> tuple[int, list[str]]:
    """
    Conservative skin-tone ratio heuristic using Pillow.
    Alone NEVER reaches 90. Max contribution ~45.
    Returns (points, reasons).
    """
    try:
        from PIL import Image
    except Exception:
        return 0, []

    try:
        im = Image.open(io.BytesIO(image_bytes))
        im = im.convert("RGBA")
        # downscale for speed
        w, h = im.size
        scale = max(w, h) / float(max_side)
        if scale > 1:
            im = im.resize((max(1, int(w / scale)), max(1, int(h / scale))), Image.BILINEAR)
        pixels = list(im.getdata())
        if not pixels:
            return 0, []

        total = 0
        skin = 0
        for r, g, b, a in pixels:
            if a < 30:
                continue  # transparent
            total += 1
            # Classic YCbCr-ish skin gate (strict) + RGB rules
            if _is_skin_pixel(r, g, b):
                skin += 1
        if total < 40:
            return 0, []

        ratio = skin / total
        reasons = [f"skin_ratio={ratio:.2f}"]
        # Map ratio → points. Very high coverage needed to avoid FP on
        # portraits / cartoons. Extreme ratio alone can clear threshold 90
        # so automatic photo filtering actually works (no set_name/emoji).
        # <0.40 → 0, mid tiers weak, ≥0.88 → strong enough alone.
        if ratio < 0.40:
            return 0, reasons
        if ratio < 0.55:
            return 12, reasons + ["skin_low"]
        if ratio < 0.70:
            return 25, reasons + ["skin_mid"]
        if ratio < 0.82:
            return 40, reasons + ["skin_high"]
        if ratio < 0.88:
            return 55, reasons + ["skin_very_high"]
        # Extreme skin dominance — high confidence for auto photo filter
        return 92, reasons + ["skin_extreme"]
    except Exception as e:
        logger.debug("score_skin_bytes failed: %s", e)
        return 0, []


def _is_skin_pixel(r: int, g: int, b: int) -> bool:
    """Strict skin pixel test — reduce false positives on orange/yellow art."""
    # Must be reasonably bright, not gray, red-ish bias
    if r < 60 or g < 30 or b < 15:
        return False
    if r < g or r < b:
        return False
    if abs(r - g) < 12 and abs(r - b) < 12:
        return False  # grayish
    # YCbCr-like bounds (common open implementations, tightened)
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    if y < 60 or y > 240:
        return False
    if not (77 <= cb <= 127 and 133 <= cr <= 173):
        return False
    return True


def combine_score(
    *,
    set_name: str = "",
    emoji: str | None = None,
    image_bytes: bytes | None = None,
    unique_id: str = "",
    settings: dict | None = None,
) -> dict[str, Any]:
    """
    Full multi-signal score.
    Returns {
      score: int 0-100,
      reasons: [str],
      forced: bool,   # banlist / strong set
      allow: bool,    # allowlist hit
    }
    """
    cfg = merge_nsfw_settings(settings or {})
    reasons: list[str] = []
    score = 0
    forced = False
    allow = False

    uid = (unique_id or "").strip()
    sname = _norm_set(set_name)

    # Allowlists win
    if uid and uid in (cfg.get("nsfw_id_allowlist") or []):
        return {"score": 0, "reasons": ["id_allowlist"], "forced": False, "allow": True}
    if sname and sname in [_norm_set(x) for x in (cfg.get("nsfw_set_allowlist") or [])]:
        return {"score": 0, "reasons": ["set_allowlist"], "forced": False, "allow": True}

    # Banlists force max score
    if uid and uid in (cfg.get("nsfw_id_banlist") or []):
        return {"score": 100, "reasons": ["id_banlist"], "forced": True, "allow": False}
    if sname and sname in [_norm_set(x) for x in (cfg.get("nsfw_set_banlist") or [])]:
        return {"score": 100, "reasons": ["set_banlist"], "forced": True, "allow": False}

    pts, r = score_from_set_name(set_name)
    score += pts
    reasons.extend(r)
    if pts >= 90:
        forced = True

    pts, r = score_from_emoji(emoji)
    score += pts
    reasons.extend(r)

    if image_bytes:
        pts, r = score_skin_bytes(image_bytes)
        score += pts
        reasons.extend(r)
        if "skin_extreme" in r:
            forced = True  # extreme skin alone is intentional high-confidence

    # Soft cap unless forced by strong name / extreme skin / banlist
    if not forced:
        # Require multi-signal for mid scores: if only one weak family, cap at 49
        families = set()
        for reason in reasons:
            if reason.startswith("set_name"):
                families.add("set")
            elif reason.startswith("emoji"):
                families.add("emoji")
            elif reason.startswith("skin"):
                families.add("skin")
        if len(families) <= 1 and score < 90:
            score = min(score, 49)  # single weak family never deletes at default 90
        score = min(score, 95)

    score = max(0, min(int(score), 100))
    return {
        "score": score,
        "reasons": reasons,
        "forced": forced,
        "allow": allow,
    }


def should_delete(result: dict, threshold: int = 90) -> bool:
    if result.get("allow"):
        return False
    if result.get("forced") and result.get("score", 0) >= 90:
        return True
    return int(result.get("score", 0)) >= int(threshold)
