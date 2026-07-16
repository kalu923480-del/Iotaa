"""
Iota Bot — Quote Sticker Renderer (/q)

Renders a replied message (or a short thread) into a polished, Telegram-style
quote card: circular avatar, sender name, message text, full-color emoji and
Devanagari, a nested reply preview, multiple themes, a soft drop shadow, a
subtle gradient background, a timestamp, and WEBP sticker or PNG output.

Rendering notes:
  • Pure Pillow, no cairo/svg. Emoji use NotoColorEmoji when present.
  • Everything is drawn at 2x (HiDPI) and downscaled with LANCZOS so edges,
    text and the avatar circle are anti-aliased and crisp on any display.
  • A per-glyph font fallback chain (grapheme aware) is used, so a character
    the primary font lacks is rendered with the next font that has it instead
    of a □ tofu box.
  • Grapheme-aware word wrapping keeps emoji ZWJ sequences and combining
    marks intact and never breaks a word mid-cluster.
"""
import io
import re
import unicodedata
import logging
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from utils.font_manager import load_font, load_emoji_font

logger = logging.getLogger(__name__)

SCALE = 2
CARD_W = 512               # max canvas width for sticker (css px)
MIN_W = 200                # min card width (css px)
PADDING = 18               # left/right inner padding (css px)
TOP_PAD = 14
BOTTOM_PAD = 22
HEADER_GAP = 8             # gap between header and message / reply
AVATAR_SIZE = 52           # avatar diameter (css px)
AV_GAP = 10                # gap between avatar and card (css px)
RADIUS = 24
NAME_FONT_SIZE = 16
MAX_FONT_SIZE = 20
MIN_FONT_SIZE = 15
BODY_TARGET = 19

THEMES = {
    "dark":   {"bg": (26, 28, 42),     "accent": (255, 178, 64),
               "name": (255, 196, 120), "text": (236, 233, 224),
               "divider": (58, 61, 80),  "ts": (150, 150, 165),
               "reply": (150, 165, 190)},
    "light":  {"bg": (244, 244, 247),  "accent": (255, 149, 41),
               "name": (210, 110, 18),  "text": (38, 40, 48),
               "divider": (214, 216, 222), "ts": (150, 150, 162),
               "reply": (120, 120, 130)},
    "white":  {"bg": (255, 255, 255),  "accent": (255, 149, 41),
               "name": (210, 110, 18),  "text": (28, 28, 28),
               "divider": (226, 226, 226), "ts": (150, 150, 150),
               "reply": (120, 120, 120)},
    "purple": {"bg": (34, 22, 44),     "accent": (94, 58, 115),
               "name": (238, 232, 245), "text": (212, 206, 222),
               "divider": (60, 40, 75),  "ts": (150, 132, 165),
               "reply": (170, 152, 188)},
    "blue":   {"bg": (17, 32, 58),     "accent": (94, 176, 255),
               "name": (132, 196, 255), "text": (224, 238, 255),
               "divider": (46, 72, 110),  "ts": (140, 165, 200),
               "reply": (150, 172, 205)},
    "telegram": {"bg": (229, 237, 247), "accent": (90, 160, 235),
               "name": (40, 130, 210),  "text": (20, 32, 48),
               "divider": (205, 216, 230), "ts": (140, 155, 175),
               "reply": (90, 140, 190)},
}

_EMOJI_RE = re.compile(
    "("
    "[\U0001F300-\U0001FAFF]"
    "|[\U0001F1E0-\U0001F1FF]"
    "|[\u2600-\u27BF]"
    "|[\u2B00-\u2BFF]"
    "|[\u2190-\u21FF]"
    "|[\uFE00-\uFE0F]"
    "|\u200D"
    "|[\u20E3]"
    ")",
    flags=re.UNICODE,
)

_SMALLCAP_MAP = {
    "\u1D00": "a", "\u1D01": "ae", "\u1D02": "g", "\u1D03": "b", "\u1D04": "c",
    "\u1D05": "d", "\u1D06": "e", "\u1D07": "e", "\u1D08": "e", "\u1D09": "i",
    "\u1D0A": "j", "\u1D0B": "k", "\u1D0C": "l", "\u1D0D": "m", "\u1D0E": "n",
    "\u1D0F": "o", "\u1D10": "o", "\u1D11": "o", "\u1D12": "o", "\u1D13": "o",
    "\u1D14": "o", "\u1D15": "o", "\u1D18": "p", "\u1D19": "r", "\u1D1A": "r",
    "\u1D1B": "t", "\u1D1C": "u", "\u1D1D": "u", "\u1D1E": "u", "\u1D20": "v",
    "\u1D21": "w", "\u1D22": "z", "\u1D23": "z", "\u1D24": "z", "\u1D25": "z",
    "\u0299": "b", "\u0280": "r", "\u0274": "n", "\u1D1F": "r",
}


class QuoteRenderError(Exception):
    pass


# ── Unicode normalization ────────────────────────────────────────────────
def _normalize_unicode(text: str) -> str:
    """Turn fancy display Unicode into renderable ASCII so no □ boxes
    appear. NFKC collapses math-bold / full-width / superscript forms; the
    explicit map collapses small-caps IPA letters."""
    text = unicodedata.normalize("NFKC", text)
    return "".join(_SMALLCAP_MAP.get(ch, ch) for ch in text)


def _has_devanagari(text: str) -> bool:
    return any('\u0900' <= ch <= '\u097F' for ch in text)


def _has_rtl(text: str) -> bool:
    return any(('\u0590' <= ch <= '\u05FF') or ('\u0600' <= ch <= '\u06FF')
               or ('\u0750' <= ch <= '\u077F') for ch in text)


# ── Color helpers ─────────────────────────────────────────────────────────
def _lighten(rgb, amt):
    return tuple(min(255, c + amt) for c in rgb)


def _alpha(rgb, a):
    return tuple(list(rgb[:3]) + [a])


def _hex_to_rgb(h: str):
    h = h.lstrip("#").strip()
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def _parse_theme(theme: str):
    t = (theme or "dark").strip().lower()
    if t in THEMES:
        pal = dict(THEMES[t])
        pal.setdefault("reply", pal["ts"])
        return pal
    if t.startswith("color"):
        hexv = None
        for p in t.split()[1:]:
            rgb = _hex_to_rgb(p)
            if rgb:
                hexv = rgb
                break
        if hexv:
            r, g, b = hexv
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            fg = (30, 30, 30) if lum > 140 else (236, 236, 236)
            accent = (255, 255, 255) if lum <= 140 else (20, 20, 20)
            return {"bg": hexv, "accent": accent, "name": accent,
                    "text": fg, "divider": hexv, "ts": accent, "reply": accent}
    default = dict(THEMES["dark"])
    default.setdefault("reply", default["ts"])
    return default


# ── Fonts ─────────────────────────────────────────────────────────────────
_dejavu_cache = {}


def _dejavu(size):
    if size in _dejavu_cache:
        return _dejavu_cache[size]
    f = load_font("DejaVuSans.ttf", size)
    _dejavu_cache[size] = f
    return f


def _pick_text_font(size, bold=False, devanagari=False):
    name = ("NotoSansDevanagari-Bold.ttf" if bold else "NotoSansDevanagari-Regular.ttf") \
        if devanagari else ("NotoSans-Bold.ttf" if bold else "NotoSans-Regular.ttf")
    return load_font(name, size) or _dejavu(size) or ImageFont.load_default()


def _pick_emoji_font():
    return load_emoji_font(109)


# Per-font .notdef bitmap cache, used to detect missing glyphs so we can
# fall back to another font instead of drawing a tofu box.
_notdef_cache: dict = {}


def _has_glyph(font, ch):
    if font is None or not ch:
        return False
    key = id(font)
    if key not in _notdef_cache:
        _notdef_cache[key] = bytes(font.getmask('\U0010FFFF'))
    b = bytes(font.getmask(ch))
    if b == _notdef_cache[key]:
        return False
    # A glyph that is entirely transparent is also "missing" for our purposes.
    return b != bytes(font.getmask(' ')) or ch.strip() != ""


def _glyph_font(chain, ch):
    for f in chain:
        if _has_glyph(f, ch):
            return f
    return chain[0]


# ── Grapheme clustering ─────────────────────────────────────────────────
def _is_emoji(s: str) -> bool:
    return bool(_EMOJI_RE.fullmatch(s))


def _graphemes(text: str):
    """Yield grapheme clusters: base chars + combining marks, variation
    selectors, and emoji ZWJ sequences are kept together."""
    chars = list(text)
    n = len(chars)
    i = 0
    while i < n:
        j = i + 1
        while j < n and chars[j] == '\u200D':
            k = j + 1
            while k < n and (unicodedata.category(chars[k]) in ('Mn', 'Me')
                             or chars[k] in '\uFE00\uFE0F'
                             or _is_emoji(chars[k])):
                k += 1
            j = k
        while j < n and (unicodedata.category(chars[j]) in ('Mn', 'Me')
                         or chars[j] in '\uFE00\uFE0F'):
            j += 1
        yield "".join(chars[i:j])
        i = j


_arabic_cache: dict = {}
_cjk_cache: dict = {}


def _arabic_font(size):
    if size not in _arabic_cache:
        _arabic_cache[size] = load_font("NotoNaskhArabic-Regular.ttf", size)
    return _arabic_cache[size]


def _cjk_font(size):
    if size not in _cjk_cache:
        _cjk_cache[size] = load_font("NotoSansCJKtc-Regular.otf", size)
    return _cjk_cache[size]


def _script_of(ch):
    if _is_emoji(ch):
        return "emoji"
    if not ch:
        return "latin"
    cp = ord(ch[0])
    if (0x0600 <= cp <= 0x06FF) or (0x0750 <= cp <= 0x077F) or \
       (0x08A0 <= cp <= 0x08FF) or (0xFB50 <= cp <= 0xFDFF) or \
       (0xFE70 <= cp <= 0xFEFF):
        return "arabic"
    if (0x4E00 <= cp <= 0x9FFF) or (0x3400 <= cp <= 0x4DBF) or \
       (0x3000 <= cp <= 0x30FF) or (0x3040 <= cp <= 0x309F) or \
       (0x30A0 <= cp <= 0x30FF) or (0xAC00 <= cp <= 0xD7AF) or \
       (0x1100 <= cp <= 0x11FF) or (0x20000 <= cp <= 0x2A6DF) or \
       (0xF900 <= cp <= 0xFAFF):
        return "cjk"
    if 0x0900 <= cp <= 0x097F:
        return "deva"
    return "latin"


def _font_for_script(script, text, chain, size):
    """Pick the best font for a same-script run of text."""
    if script == "arabic":
        f = _arabic_font(size)
        if f and _has_glyph(f, text):
            return f
    elif script == "cjk":
        f = _cjk_font(size)
        if f and _has_glyph(f, text):
            return f
    elif script == "deva":
        f = _pick_text_font(size, devanagari=True)
        if f and _has_glyph(f, text):
            return f
    return _glyph_font(chain, text)


def _itemize_line(text, chain, emoji_font, size):
    """Turn a line/word into draw items, grouping consecutive same-script
    graphemes into one run so FreeType can shape/join them (Arabic, CJK,
    Devanagari) and kern Latin text properly."""
    items = []
    cur_script = None
    cur = ""
    for cl in _graphemes(text):
        if _is_emoji(cl):
            if cur:
                items.append(('text', _font_for_script(cur_script, cur, chain, size)))
                cur = ""
                cur_script = None
            items.append(('emoji', None))
            continue
        s = _script_of(cl)
        if s != cur_script:
            if cur:
                items.append(('text', _font_for_script(cur_script, cur, chain, size)))
            cur = cl
            cur_script = s
        else:
            cur += cl
    if cur:
        items.append(('text', _font_for_script(cur_script, cur, chain, size)))
    return items


# ── Measurement & layout ─────────────────────────────────────────────────
def _space_w(font, size):
    if font is None:
        return int(size * 0.28)
    return font.getlength(' ')


def _item_w(item, size):
    kind, payload = item
    if kind == 'emoji':
        return size * 1.15
    font = payload if payload is not None else _dejavu(size)
    return font.getlength(kind)


def _line_h(size):
    return int(size * 1.35)


def _wrap_para(para, chain, emoji_font, size, max_w):
    """Word-wrap a single paragraph into lines of draw-items. Long words
    with no spaces are broken at grapheme-cluster boundaries."""
    tokens = para.split(' ')
    lines = []
    cur = []
    curw = 0
    sp = _space_w(chain[0], size)
    for ti, tok in enumerate(tokens):
        items = _itemize_line(tok, chain, emoji_font, size)
        tok_w = sum(_item_w(it, size) for it in items)
        if ti > 0:
            if cur and curw + sp + tok_w <= max_w:
                cur.append(('text', chain[0]))
                curw += sp
                cur.extend(items)
                curw += tok_w
            else:
                if cur:
                    lines.append(cur)
                if tok_w > max_w:
                    run = []
                    runw = 0
                    for it in items:
                        iw = _item_w(it, size)
                        if run and runw + iw > max_w:
                            lines.append(run)
                            run = []
                            runw = 0
                        run.append(it)
                        runw += iw
                    cur = run
                    curw = runw
                else:
                    cur = items
                    curw = tok_w
        else:
            if cur:
                lines.append(cur)
            if tok_w > max_w:
                run = []
                runw = 0
                for it in items:
                    iw = _item_w(it, size)
                    if run and runw + iw > max_w:
                        lines.append(run)
                        run = []
                        runw = 0
                    run.append(it)
                    runw += iw
                cur = run
                curw = runw
            else:
                cur = items
                curw = tok_w
    if cur:
        lines.append(cur)
    return lines or [[]]


def _fit_body(text, devanagari, ef, max_w, max_h):
    for size in range(MAX_FONT_SIZE, MIN_FONT_SIZE - 1, -2):
        chain = [
            _pick_text_font(size, devanagari=devanagari),
            _pick_text_font(size, bold=True, devanagari=devanagari),
            _dejavu(size),
        ]
        lines = []
        for para in text.split("\n"):
            lines.extend(_wrap_para(para, chain, ef, size, max_w))
        lh = _line_h(size)
        if lh * len(lines) <= max_h:
            return chain, size, lines, lh
    size = MIN_FONT_SIZE
    chain = [
        _pick_text_font(size, devanagari=devanagari),
        _pick_text_font(size, bold=True, devanagari=devanagari),
        _dejavu(size),
    ]
    lines = []
    for para in text.split("\n"):
        lines.extend(_wrap_para(para, chain, ef, size, max_w))
    lh = _line_h(size)
    max_lines = max(1, max_h // lh)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and sum(_item_w(it, size) for it in last) + _item_w(('text', chain[0]), size) > max_w:
            last = last[:-1]
        lines[-1] = last + [('text', chain[0])]
    return chain, size, lines, lh


# ── Drawing primitives ───────────────────────────────────────────────────
_emoji_cache: dict = {}


def _render_emoji_glyph(ch, emoji_font, target):
    key = (ch, target)
    if key in _emoji_cache:
        return _emoji_cache[key]
    try:
        canvas = Image.new("RGBA", (109, 109), (0, 0, 0, 0))
        ImageDraw.Draw(canvas).text((0, 0), ch, font=emoji_font, embedded_color=True)
        bbox = canvas.getbbox()
        if bbox:
            canvas = canvas.crop(bbox)
        out = canvas.resize((target, target), Image.LANCZOS)
        _emoji_cache[key] = out
        return out
    except Exception as e:
        logger.debug(f"emoji glyph failed {ch!r}: {e}")
        _emoji_cache[key] = None
        return None


def _draw_line(draw, card, x, y, items, size, color, rtl, max_w, ef):
    if rtl:
        total = sum(_item_w(it, size) for it in items)
        x = x + max_w - total
    for item in items:
        kind, payload = item
        if kind == 'emoji':
            epx = int(size * 1.15)
            g = _render_emoji_glyph(kind, ef, epx)
            if g:
                paste_y = int(y + size * 0.5 - epx / 2)
                card.paste(g, (int(x), paste_y), g)
            x += epx
        else:
            font = payload if payload is not None else _dejavu(size)
            draw.text((x, y), kind, font=font, fill=color)
            x += font.getlength(kind)
    return x


def _rounded_mask(size, inset, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle(
        (inset, inset, size[0] - inset, size[1] - inset), radius=radius, fill=255)
    return m.filter(ImageFilter.GaussianBlur(1.0))


def _draw_avatar(card, avatar_bytes, letter, color, x, y, size):
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(0.8))
    if avatar_bytes:
        try:
            av = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
            av = av.resize((size, size), Image.LANCZOS)
            card.paste(av, (x, y), mask)
            return
        except Exception:
            pass
    circle = Image.new("RGBA", (size, size), color)
    d = ImageDraw.Draw(circle)
    font = _pick_text_font(int(size * 0.40), bold=True)
    bb = d.textbbox((0, 0), letter, font=font)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    d.text(((size - tw) / 2 - bb[0], (size - th) / 2 - bb[1]),
           letter, font=font, fill=(255, 255, 255, 255))
    card.paste(circle, (x, y), mask)


def _draw_round_thumb(media_bytes, size, radius):
    """Cover-crop a media thumbnail to a square and round its corners (AA)."""
    try:
        im = Image.open(io.BytesIO(media_bytes)).convert("RGBA")
    except Exception:
        return None
    w, h = im.size
    if w <= 0 or h <= 0:
        return None
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    im = im.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size, size), radius=radius, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(0.8))
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out


def _reply_placeholder(card, thumb, tx, ty, pal):
    """Fallback box shown when a replied media thumbnail can't be fetched."""
    box = Image.new("RGBA", (thumb, thumb), _alpha(pal["accent"], 40))
    ImageDraw.Draw(box).rounded_rectangle(
        (0, 0, thumb, thumb), radius=6 * SCALE,
        outline=_alpha(pal["accent"], 160), width=2 * SCALE)
    card.paste(box, (tx, int(ty)), box)


# ── Main entrypoint ──────────────────────────────────────────────────────
def render_quote_card(messages, avatar_bytes, theme="dark", mode="sticker",
                      reply_preview=None, timestamp=None, border=True,
                      border_width=None, border_color=None,
                      scale=1.0, crop=False, emoji_brand=None, privacy=False):
    """Render a polished quote card. See module docstring for params.

    The card is sized dynamically: its width follows the content (clamped to
    a min/max) and its height grows with the text, with equal top/bottom
    padding so there is no wasted blank space."""
    if not messages:
        raise QuoteRenderError("❌ Nothing to quote.")

    pal = _parse_theme(theme)
    primary = messages[0]
    if privacy:
        name = "Anonymous"
        avatar_bytes = None
    else:
        name = _normalize_unicode((primary.get("name") or "User").strip()) or "User"
    single = len(messages) == 1

    if single:
        body_source = primary["text"]
    else:
        body_source = "\n".join(f"{m['name']}: {m['text']}" for m in messages)
    if not body_source or not body_source.strip():
        raise QuoteRenderError("❌ That message has no text to quote.")
    body_source = _normalize_unicode(body_source.strip())

    ef = _pick_emoji_font()
    deva = _has_devanagari(body_source) or _has_devanagari(name)
    rtl = _has_rtl(name) or _has_rtl(body_source)

    # ── Scaled geometry (css px * SCALE) ────────────────────────────────
    pad = PADDING * SCALE
    top_pad = TOP_PAD * SCALE
    bot_pad = BOTTOM_PAD * SCALE
    hgap = HEADER_GAP * SCALE
    av_d = AVATAR_SIZE * SCALE
    av_gap = AV_GAP * SCALE
    radius = RADIUS * SCALE
    name_size = NAME_FONT_SIZE * SCALE
    name_lh = int(name_size * 1.35)
    ts_size = 14 * SCALE
    M = 14 * SCALE

    has_reply = False
    rp = rp_name = ""
    has_media = False
    if reply_preview:
        rp = _normalize_unicode((reply_preview.get("text") or "").strip())
        rp_name = _normalize_unicode((reply_preview.get("name") or "Someone").strip())
        if rp:
            has_reply = True
            has_media = bool(reply_preview.get("media"))

    # ── Sizing: dynamic width/height from content ────────────────────────
    MAX_CANVAS_W = CARD_W if mode == "sticker" else 560
    avatar_total = AVATAR_SIZE + AV_GAP
    card_max_w = MAX_CANVAS_W - (14 + avatar_total + 14)
    inner_max = max(40, (card_max_w - 2 * PADDING)) * SCALE

    reply_block = 28 * SCALE if has_reply else 0
    header_block = top_pad + name_lh + hgap + reply_block + hgap
    allowed_total = MAX_CANVAS_W * SCALE
    max_body_h = max(40 * SCALE, allowed_total - header_block - bot_pad - 2 * M)

    chain, size, lines, lh = _fit_body(body_source, deva, ef, inner_max, max_body_h)

    def _w(items, sz):
        return sum(_item_w(it, sz) for it in items)

    body_widest = max((_w(ln, size) for ln in lines), default=0)

    nf_chain = [
        _pick_text_font(name_size, bold=True, devanagari=_has_devanagari(name)),
        _pick_text_font(name_size, bold=False, devanagari=_has_devanagari(name)),
        _dejavu(name_size),
    ]
    nef = _pick_emoji_font()
    name_items_full = _itemize_line(name, nf_chain, nef, name_size)
    name_w = _w(name_items_full, name_size)

    reply_inner = 0
    rp_size = 15 * SCALE
    if has_reply:
        rp_chain = [
            _pick_text_font(rp_size, devanagari=_has_devanagari(rp)),
            _pick_text_font(rp_size, bold=True, devanagari=_has_devanagari(rp)),
            _dejavu(rp_size),
        ]
        thumb = 34 * SCALE if has_media else 0
        strip = 3 * SCALE
        gap_thumb = 8 * SCALE if thumb else 0
        txt = rp_name + ": " + rp
        rp_items_full = _itemize_line(txt, rp_chain, ef, rp_size)
        reply_inner = strip + thumb + gap_thumb + _w(rp_items_full, rp_size) + 8 * SCALE

    desired_inner = max(body_widest, name_w, reply_inner)
    card_w = int(min(card_max_w * SCALE,
                     max(MIN_W * SCALE, desired_inner + 2 * pad)))
    inner_w = card_w - 2 * pad

    card_h = int(header_block + lh * len(lines) + bot_pad)
    card_h = max(card_h, int(top_pad + name_lh + bot_pad + 20 * SCALE))

    # Canvas: avatar to the LEFT of the card, with a small gap.
    card_x = M + av_d + av_gap
    card_y = M
    canvas_w = int(card_x + card_w + M)
    canvas_h = int(card_h + 2 * M)

    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))

    # Soft drop shadow under the card only.
    shadow = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (card_x, card_y + 8 * SCALE, card_x + card_w, card_y + card_h + 8 * SCALE),
        radius=radius, fill=(0, 0, 0, 34))
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    canvas.paste(shadow, (0, 0), shadow)

    # Card background (subtle vertical gradient) + thin auto-themed border.
    card = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    grad = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    top = _lighten(pal["bg"], 14)
    for yy in range(card_h):
        t = yy / (card_h - 1) if card_h > 1 else 0
        col = tuple(int(top[i] + (pal["bg"][i] - top[i]) * t) for i in range(3)) + (255,)
        gd.line((0, yy, card_w, yy), fill=col)
    cmask = _rounded_mask((card_w, card_h), 0, radius)
    card.paste(grad, (0, 0), cmask)

    if border:
        bw = (border_width if border_width else 1) * SCALE
        bcol = border_color if border_color else pal["accent"]
        ImageDraw.Draw(card).rounded_rectangle(
            (0, 0, card_w, card_h), radius=radius,
            outline=_alpha(bcol, 170), width=bw)

    draw = ImageDraw.Draw(card)

    # Avatar — outside the card, centered on the header + first message line
    # (not the whole card) so it reads as aligned with the username/text.
    letter = (name[:1] or "?").upper()
    msg_top_local = top_pad + name_lh + hgap + (reply_block + hgap if has_reply else 0)
    avatar_center_local = (top_pad + (msg_top_local + lh)) / 2
    av_cx = M + av_d / 2
    av_cy = card_y + avatar_center_local
    _draw_avatar(canvas, avatar_bytes, letter, pal["accent"],
                 int(av_cx - av_d / 2), int(av_cy - av_d / 2), av_d)

    # Name (header).
    name_x = card_x + pad
    name_y = card_y + top_pad
    max_name_w = card_w - (name_x - card_x) - pad - 8 * SCALE
    disp_items = list(name_items_full)
    while disp_items and _w(disp_items, name_size) > max_name_w:
        disp_items = disp_items[:-1]
    if len(disp_items) < len(name_items_full):
        disp_items = disp_items + [('…', nf_chain[0])]
    _draw_line(draw, card, name_x - card_x, name_y - card_y, disp_items,
               name_size, pal["name"], _has_rtl(name), max_name_w, nef)

    # Reply preview (color strip + optional thumbnail + text).
    cur_y = card_y + top_pad + name_lh + hgap
    if has_reply:
        text_x = card_x + pad + 3 * SCALE + (34 * SCALE if has_media else 0) \
            + (8 * SCALE if has_media else 0)
        rmax = card_w - pad - (text_x - card_x)
        draw.line((pad, (cur_y - card_y) - 4 * SCALE, pad, (cur_y - card_y) + 18 * SCALE),
                  fill=pal["accent"], width=3 * SCALE)
        if has_media:
            tx = int(card_x + pad + 3 * SCALE)
            mb = reply_preview.get("media_bytes")
            if mb:
                timg = _draw_round_thumb(mb, 34 * SCALE, 6 * SCALE)
                if timg is not None:
                    card.paste(timg, (tx - card_x, int(cur_y - card_y)), timg)
                else:
                    _reply_placeholder(card, 34 * SCALE, tx - card_x,
                                       int(cur_y - card_y), pal)
            else:
                _reply_placeholder(card, 34 * SCALE, tx - card_x,
                                   int(cur_y - card_y), pal)
        txt_items = list(rp_items_full)
        while txt_items and _w(txt_items, rp_size) > rmax:
            txt_items = txt_items[:-1]
        if len(txt_items) < len(rp_items_full):
            txt_items = txt_items + [('…', rp_chain[0])]
        _draw_line(draw, card, text_x - card_x, cur_y - card_y, txt_items,
                   rp_size, pal["reply"], _has_rtl(rp), rmax, ef)
        cur_y += reply_block

    # Message body.
    y = float(cur_y + hgap - card_y)
    for line in lines:
        _draw_line(draw, card, float(pad), y, line, size, pal["text"],
                   rtl, inner_w, ef)
        y += lh

    # Timestamp (bottom-right, inside the card).
    if timestamp:
        ts_font = _pick_text_font(ts_size)
        tw = ts_font.getlength(timestamp)
        draw.text((card_w - pad - tw, card_h - bot_pad - ts_size),
                  timestamp, font=ts_font, fill=pal["ts"])

    # Emoji brand (bottom-right corner, optional — quote-bot "emoji brand").
    if emoji_brand and len(emoji_brand) <= 4:
        try:
            ebsize = 22 * SCALE
            eimg = _render_emoji_glyph(emoji_brand, _pick_emoji_font(), ebsize)
            if eimg is not None:
                ex = int(card_w - pad - ebsize)
                ey = int(card_h - bot_pad - ebsize)
                card.paste(eimg, (ex, ey), eimg)
        except Exception as e:
            logger.debug(f"emoji brand draw failed: {e}")

    # Composite card onto the canvas, then downscale (HiDPI → AA).
    canvas.paste(card, (card_x, card_y), card)
    out_w = int(canvas_w / SCALE)
    out_h = int(canvas_h / SCALE)
    final = canvas.resize((out_w, out_h), Image.LANCZOS)

    # Optional crop: trim the surrounding transparent margin (shadow/M padding).
    if crop:
        try:
            bbox = final.getbbox()
            if bbox:
                final = final.crop(bbox)
                out_w, out_h = final.size
        except Exception:
            pass

    buf = io.BytesIO()
    if mode == "sticker":
        sq = Image.new("RGBA", (CARD_W, CARD_W), (0, 0, 0, 0))
        paste_x = max(0, (CARD_W - out_w) // 2)
        paste_y = max(0, (CARD_W - out_h) // 2)
        sq.paste(final, (paste_x, paste_y), final)
        sq.save(buf, format="WEBP", lossless=True)
    else:
        # Optional hi-res scale (stickers stay fixed 512, so scale is PNG-only).
        if scale and scale != 1.0:
            try:
                final = final.resize((max(1, int(out_w * scale)),
                                      max(1, int(out_h * scale))), Image.LANCZOS)
            except Exception:
                pass
        final.save(buf, format="PNG")
    return buf.getvalue()
