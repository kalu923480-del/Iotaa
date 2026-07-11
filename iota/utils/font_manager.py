"""
Iota Bot — Font Manager for /q (Quote Sticker Generator)

WHY THIS EXISTS
─────────────────
Rendering a quote sticker (utils/quote_render.py) needs fonts that cover
Hindi/Devanagari, emoji, and general Unicode — NOT just Latin text, since
messages can be in Hindi, English, or a mix, with emoji anywhere. Most
server environments only ship basic Latin fonts by default, so we need
proper Unicode fonts bundled or fetched.

STRATEGY
─────────
1. First choice: use fonts already bundled in assets/fonts/ (checked
   into the repo — see the bottom of this file for exact download URLs
   the bot owner should place there once, so this works with zero
   internet access at runtime after that).
2. Fallback: if not bundled, try to download the required Noto fonts
   once (cached to assets/fonts/ for every future call — only ever
   downloads once per font, not per sticker).
3. Final fallback: use whatever TrueType font the system already has,
   so the feature still works (Latin/English text) even with zero
   Devanagari/emoji support, rather than crashing outright.

This layered approach means the feature works out of the box on a
fresh clone in most cases, keeps working forever once fonts are cached
locally, and never hard-fails even in a fully offline environment.
"""
import os
import logging
from PIL import ImageFont

logger = logging.getLogger(__name__)

_FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
os.makedirs(_FONTS_DIR, exist_ok=True)

# Bundle these files yourself (one-time) for guaranteed, offline-safe
# rendering. Easiest way on a Debian/Ubuntu server:
#   sudo apt install fonts-noto-core fonts-noto-ui-core
# which installs system-wide Noto fonts (used automatically via
# _SYSTEM_FALLBACKS/_SYSTEM_FALLBACKS_DEVANAGARI below, zero code
# changes needed). Or download manually and place here:
#   assets/fonts/NotoSans-Regular.ttf     https://fonts.google.com/noto/specimen/Noto+Sans
#   assets/fonts/NotoSans-Bold.ttf        (same page, Bold weight)
#   assets/fonts/NotoSansDevanagari-Regular.ttf   https://fonts.google.com/noto/specimen/Noto+Sans+Devanagari
#   assets/fonts/NotoSansDevanagari-Bold.ttf      (same page, Bold weight)
# If missing, _try_download() below attempts to fetch them automatically
# the first time /q is used (needs the bot's host to have internet
# access — normal for any real deployment).

_FONT_URLS = {
    # 🔴 FIX: the old URLs pointed at googlefonts/noto-fonts's `main`
    # branch under a `hinted/ttf/...` path that no longer exists (Noto's
    # font repos were reorganized into notofonts/notofonts.github.io,
    # served under dated monthly release tags that change every month —
    # not something safe to hardcode). openmaptiles/fonts mirrors the
    # exact same static Noto Sans / Noto Sans Devanagari TTFs at stable,
    # untagged `master` branch paths, so this won't rot the same way.
    "NotoSans-Regular.ttf": "https://raw.githubusercontent.com/openmaptiles/fonts/master/noto-sans/NotoSans-Regular.ttf",
    "NotoSans-Bold.ttf": "https://raw.githubusercontent.com/openmaptiles/fonts/master/noto-sans/NotoSans-Bold.ttf",
    "NotoSansDevanagari-Regular.ttf": "https://raw.githubusercontent.com/openmaptiles/fonts/master/noto-sans/NotoSansDevanagari-Regular.ttf",
    "NotoSansDevanagari-Bold.ttf": "https://raw.githubusercontent.com/openmaptiles/fonts/master/noto-sans/NotoSansDevanagari-Bold.ttf",
    # Color emoji (Pillow only loads the 109px strike of this file, so we
    # always request it at size 109 and scale the glyphs down ourselves).
    "NotoColorEmoji.ttf": "https://raw.githubusercontent.com/googlefonts/noto-emoji/main/fonts/NotoColorEmoji.ttf",
}

# System fallback paths to try if bundled/downloaded fonts aren't
# available at all — covers common Linux server distros. Devanagari-
# capable ones listed first so Hindi text doesn't silently fall back to
# a Latin-only font and show tofu boxes for Hindi script.
# 💡 Most durable fix of all: `apt install fonts-noto-core fonts-noto-color-emoji`
# (Debian/Ubuntu) once on your server gives every font below for free,
# forever, with zero runtime downloads needed.
_SYSTEM_FALLBACKS_DEVANAGARI = [
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansDevanagari-Regular.ttf",
]
_SYSTEM_FALLBACKS = [
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]
_SYSTEM_FALLBACKS_EMOJI = [
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/opentype/noto/NotoColorEmoji.ttf",
]

_font_cache: dict = {}


def _try_download(filename: str) -> bool:
    path = os.path.join(_FONTS_DIR, filename)
    if os.path.exists(path):
        return True
    url = _FONT_URLS.get(filename)
    if not url:
        return False
    try:
        import urllib.request
        logger.info(f"Downloading font {filename} for /q quote stickers (one-time)...")
        urllib.request.urlretrieve(url, path)
        return os.path.exists(path)
    except Exception as e:
        logger.warning(f"Could not download font {filename}: {e}")
        return False


def get_font_path(name: str) -> str | None:
    """
    Returns a usable font file path for `name`.
    Priority: bundled (in assets/) → system fonts → network download → None.
    System fonts are now checked BEFORE attempting a network download so
    the bot doesn't waste 3 HTTP round-trips on every /q call when a
    perfectly good system font is already available locally.
    """
    bundled = os.path.join(_FONTS_DIR, name)
    if os.path.exists(bundled):
        return bundled

    # Check system first (fast, no network) — only try downloading if
    # nothing useful is found locally at all.
    fallback_list = _SYSTEM_FALLBACKS_DEVANAGARI + _SYSTEM_FALLBACKS if "Devanagari" in name else _SYSTEM_FALLBACKS
    for fb in fallback_list:
        if os.path.exists(fb):
            return fb

    # Last resort: try downloading (works on real servers with internet).
    if _try_download(name):
        return bundled

    return None


def load_font(name: str, size: int) -> ImageFont.FreeTypeFont | None:
    """Cached font loader — avoids re-reading the font file from disk
    for every single sticker rendered."""
    cache_key = (name, size)
    if cache_key in _font_cache:
        return _font_cache[cache_key]
    path = get_font_path(name)
    if not path:
        return None
    try:
        font = ImageFont.truetype(path, size)
        _font_cache[cache_key] = font
        return font
    except Exception as e:
        logger.warning(f"Failed to load font {name} at size {size}: {e}")
        return None


def get_emoji_font_path() -> str | None:
    """Locate the color-emoji font (bundled, system, or downloadable)."""
    bundled = os.path.join(_FONTS_DIR, "NotoColorEmoji.ttf")
    if os.path.exists(bundled):
        return bundled
    for fb in _SYSTEM_FALLBACKS_EMOJI:
        if os.path.exists(fb):
            return fb
    if _try_download("NotoColorEmoji.ttf"):
        return bundled
    return None


def load_emoji_font(size: int = 109) -> ImageFont.FreeTypeFont | None:
    """Load the color-emoji font. Pillow's FreeType backend can only open
    the NotoColorEmoji.ttf 109px strike, so we always request 109 and
    scale the individual glyphs down ourselves at draw time."""
    path = get_emoji_font_path()
    if not path:
        return None
    cache_key = ("emoji", size)
    if cache_key in _font_cache:
        return _font_cache[cache_key]
    try:
        font = ImageFont.truetype(path, size)
        _font_cache[cache_key] = font
        return font
    except Exception as e:
        # Fall back to whatever size the font does support if 109 fails.
        logger.debug(f"emoji font load at {size} failed: {e}")
        try:
            font = ImageFont.truetype(path, 64)
            _font_cache[cache_key] = font
            return font
        except Exception as e2:
            logger.warning(f"emoji font load failed entirely: {e2}")
            return None
