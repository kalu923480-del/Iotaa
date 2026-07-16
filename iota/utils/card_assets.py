"""
Iota Bot — Real Playing-Card Assets

Loads the bundled 52 real card-face PNGs (assets/cards/png/) plus a
locally-generated card back. Provides a single cache-backed entry point,
``get_card_image(rank, suit)`` and ``get_card_back()``, so every card game
(/card, /bet, /bluff, /uno, …) can show genuine card images instead of
drawn glyphs.

All card assets are public-domain (Byron Knoll SVG-cards set, downloaded
from Wikimedia Commons) and converted to PNG at load time. If a specific
face is ever missing, ``render_fallback_card()`` draws a clean themed
card so a game never breaks.

Public API
----------
  get_card_image(rank, suit) -> PIL.Image.Image   (RGBA, transparent bg)
  get_card_back()            -> PIL.Image.Image
  RANKS, SUITS, SUIT_SYMBOLS
"""
import os
import logging
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

_ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "assets", "cards", "png")
_CARD_W, _CARD_H = 300, 420

RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
SUITS = ["spade", "heart", "diamond", "club"]
SUIT_SYMBOLS = {"spade": "♠", "heart": "♥", "diamond": "♦", "club": "♣"}

# Pillow palette (matches utils/game_art.py design tokens).
_PANEL = (23, 27, 46)
_PANEL2 = (31, 36, 56)
_AMBER = (255, 182, 72)
_AMBER_DIM = (184, 132, 58)
_RED = (231, 76, 90)
_LINE = (42, 47, 71)
_TEXT = (240, 236, 224)

_cache: dict = {}


def _load(name: str):
    if name in _cache:
        return _cache[name]
    path = os.path.join(_ASSETS, f"{name}.png")
    img = None
    if os.path.exists(path):
        try:
            img = Image.open(path).convert("RGBA")
        except Exception as e:
            logger.debug(f"card asset load failed {name}: {e}")
    _cache[name] = img
    return img


def get_card_image(rank: str, suit: str) -> Image.Image:
    """Return a real card-face Image (RGBA). Falls back to a drawn card."""
    suit = (suit or "spade").lower()
    rank = str(rank or "A").upper()
    img = _load(f"{suit}_{rank}")
    if img is not None:
        return img
    logger.warning(f"Missing card asset {suit}_{rank}, using drawn fallback")
    return _draw_card(rank, suit, hidden=False)


def get_card_back() -> Image.Image:
    """Return the card-back Image (RGBA). Falls back to a drawn back."""
    img = _load("back")
    if img is not None:
        return img
    return _draw_card("A", "spade", hidden=True)


# ── Drawn fallbacks (only used if a PNG is missing) ────────────────────────

def _font(size: int, bold: bool = False):
    from utils.font_manager import load_font
    name = "NotoSans-Bold.ttf" if bold else "NotoSans-Regular.ttf"
    return load_font(name, size) or ImageFont.load_default()


def _rounded(d, box, r, fill=None, outline=None, width=2):
    try:
        d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)
    except Exception:
        d.rectangle(box, fill=fill, outline=outline, width=width)


def _draw_card(rank: str, suit: str, hidden: bool = False) -> Image.Image:
    img = Image.new("RGBA", (_CARD_W, _CARD_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    _rounded(d, (6, 6, _CARD_W - 6, _CARD_H - 6), 22, fill=(255, 255, 255, 255),
             outline=_AMBER_DIM, width=4)
    if hidden:
        for i in range(0, _CARD_W, 20):
            for j in range(0, _CARD_H, 20):
                if ((i + j) // 20) % 2 == 0:
                    d.rectangle((i, j, i + 18, j + 18), fill=(28, 34, 56, 255))
        d.text((_CARD_W // 2 - 30, _CARD_H // 2 - 30), "🂠", font=_font(80, True),
               fill=_AMBER + (255,))
        return img
    sym, col = SUIT_SYMBOLS.get(suit, "♠"), _RED if suit in ("heart", "diamond") else (0, 0, 0, 255)
    col = col if len(col) == 4 else col + (255,)
    d.text((18, 16), rank, font=_font(48, True), fill=col)
    d.text((16, 70), sym, font=_font(40, True), fill=col)
    d.text((_CARD_W // 2 - 40, _CARD_H // 2 - 40), sym, font=_font(110, True), fill=col)
    d.text((_CARD_W - 70, _CARD_H - 70), rank, font=_font(48, True), fill=col)
    d.text((_CARD_W - 60, _CARD_H - 120), sym, font=_font(40, True), fill=col)
    return img


def _ensure_back():
    """Generate the card back once if the bundled PNG is missing."""
    if _load("back") is not None:
        return
    img = _draw_card("A", "spade", hidden=True)
    _cache["back"] = img


# Build a bundled back PNG if it doesn't exist yet (keeps repo self-contained).
if not os.path.exists(os.path.join(_ASSETS, "back.png")):
    try:
        _ensure_back()
        _cache["back"].save(os.path.join(_ASSETS, "back.png"))
    except Exception as e:
        logger.debug(f"could not write back.png: {e}")


def card_to_bytes(img: Image.Image) -> bytes:
    """Return PNG bytes for sending via Telegram."""
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def compose_cards(images: list, gap: int = 14, bg: tuple = (15, 18, 32),
                  label: str = None) -> Image.Image:
    """Compose several card Images into one horizontal strip (RGB PNG bytes
    via card_to_bytes). `images` is a list of PIL Images (already 300x420)."""
    if not images:
        return _draw_card("A", "spade", hidden=False)
    w, h = images[0].size
    pad = 18
    total_w = pad * 2 + w * len(images) + gap * (len(images) - 1)
    label_h = 46 if label else 0
    canvas = Image.new("RGB", (total_w, h + pad * 2 + label_h), bg)
    for i, im in enumerate(images):
        x = pad + i * (w + gap)
        canvas.paste(im, (x, pad), im)
    if label:
        try:
            d = ImageDraw.Draw(canvas)
            f = _font(28, True)
            d.text((pad, pad + h + 8), label, font=f, fill=_AMBER + (255,))
        except Exception:
            pass
    return canvas


def compose_cards_bytes(images: list, gap: int = 14, bg: tuple = (15, 18, 32),
                        label: str = None) -> bytes:
    return card_to_bytes(compose_cards(images, gap=gap, bg=bg, label=label))
