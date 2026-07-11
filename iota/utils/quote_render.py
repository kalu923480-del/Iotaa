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
CARD_W = 512
PADDING = 18
INSET = 14
AVATAR_SIZE = 92
RADIUS = 22
NAME_FONT_SIZE = 25
MAX_FONT_SIZE = 38
MIN_FONT_SIZE = 16
BODY_TARGET = 30

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
    "purple": {"bg": (44, 28, 62),     "accent": (188, 128, 255),
               "name": (206, 158, 255), "text": (240, 230, 252),
               "divider": (80, 58, 102),  "ts": (165, 145, 190),
               "reply": (175, 155, 205)},
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


def _itemize(cluster, chain, emoji_font, size):
    """Return a draw item: ('text', font) or ('emoji', None)."""
    if _is_emoji(cluster):
        return ('emoji', None)
    return ('text', _glyph_font(chain, cluster))


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
    return int(size * 1.34)


def _wrap_para(para, chain, emoji_font, size, max_w):
    """Word-wrap a single paragraph into lines of draw-items. Long words
    with no spaces are broken at grapheme-cluster boundaries."""
    tokens = para.split(' ')
    lines = []
    cur = []
    curw = 0
    sp = _space_w(chain[0], size)
    for ti, tok in enumerate(tokens):
        items = [_itemize(c, chain, emoji_font, size) for c in _graphemes(tok)]
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


# ── Main entrypoint ──────────────────────────────────────────────────────
def render_quote_card(messages, avatar_bytes, theme="dark", mode="sticker",
                      reply_preview=None, timestamp=None, border=True,
                      border_width=None, border_color=None):
    """Render a polished quote card. See module docstring for params."""
    if not messages:
        raise QuoteRenderError("❌ Nothing to quote.")

    pal = _parse_theme(theme)
    primary = messages[0]
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

    # Scale all geometry to HiDPI then downscale at the end.
    cw = CARD_W * SCALE
    pad = PADDING * SCALE
    inset = INSET * SCALE
    av = AVATAR_SIZE * SCALE
    radius = RADIUS * SCALE
    name_size = NAME_FONT_SIZE * SCALE

    header_h = av
    reply_h = 0
    if reply_preview:
        reply_h = 30 * SCALE
    text_top = inset + pad + header_h + 14 * SCALE + reply_h + 6 * SCALE
    body_max_w = cw - 2 * (pad + inset)

    max_body_h = (CARD_W if mode == "sticker" else 900) * SCALE - text_top - pad - inset
    chain, size, lines, lh = _fit_body(body_source, deva, ef, body_max_w, max_body_h)

    card_h = max(CARD_W, text_top + lh * len(lines) + pad + inset) * 1
    card_h = int(card_h)

    # ── Background (gradient) + soft drop shadow ────────────────────────
    bg = Image.new("RGBA", (cw, card_h), (0, 0, 0, 0))
    grad = Image.new("RGBA", (cw, card_h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    top = _lighten(pal["bg"], 26)
    for yy in range(card_h):
        t = yy / (card_h - 1) if card_h > 1 else 0
        col = tuple(int(top[i] + (pal["bg"][i] - top[i]) * t) for i in range(3)) + (255,)
        gd.line((0, yy, cw, yy), fill=col)
    mask = _rounded_mask((cw, card_h), inset, radius)
    bg.paste(grad, (0, 0), mask)

    shadow = Image.new("RGBA", (cw, card_h), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (inset, inset + 6 * SCALE, cw - inset, card_h - inset + 6 * SCALE),
        radius=radius, fill=(0, 0, 0, 50))
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))

    card = Image.new("RGBA", (cw, card_h), (0, 0, 0, 0))
    card.paste(shadow, (0, 0), shadow)
    card.paste(bg, (0, 0), bg)

    # Border: soft, auto-themed; can be disabled or overridden.
    if border:
        bw = (border_width if border_width else 2) * SCALE
        bcol = border_color if border_color else pal["accent"]
        ImageDraw.Draw(card).rounded_rectangle(
            (inset, inset, cw - inset, card_h - inset),
            radius=radius, outline=_alpha(bcol, 130), width=bw)

    draw = ImageDraw.Draw(card)

    # Avatar
    av_x = pad + inset
    av_y = pad + inset
    letter = (name[:1] or "?").upper()
    _draw_avatar(card, avatar_bytes, letter, pal["accent"], av_x, av_y, av)

    # Name
    nf_chain = [
        _pick_text_font(name_size, bold=True, devanagari=_has_devanagari(name)),
        _pick_text_font(name_size, bold=False, devanagari=_has_devanagari(name)),
        _dejavu(name_size),
    ]
    nef = _pick_emoji_font()
    name_x = av_x + av + 14 * SCALE
    name_y = av_y + (av - name_size) // 2 - 2 * SCALE
    max_name_w = cw - name_x - pad - inset - 8 * SCALE
    disp_items = [_itemize(c, nf_chain, nef, name_size) for c in _graphemes(name)]
    while disp_items and sum(_item_w(it, name_size) for it in disp_items) > max_name_w:
        disp_items = disp_items[:-1]
    if len(disp_items) < len(list(_graphemes(name))):
        disp_items = disp_items + [('text', nf_chain[0])] if False else \
            [('…', nf_chain[0])]
    _draw_line(draw, card, name_x, name_y, disp_items, name_size, pal["name"],
               _has_rtl(name), max_name_w, nef)

    # Reply preview (nested quote, Telegram-style)
    cur_y = av_y + av + 12 * SCALE
    if reply_preview:
        rp = _normalize_unicode((reply_preview.get("text") or "").strip())
        rp_name = _normalize_unicode((reply_preview.get("name") or "Someone").strip())
        if rp:
            rp_size = 16 * SCALE
            rp_chain = [
                _pick_text_font(rp_size, devanagari=_has_devanagari(rp)),
                _pick_text_font(rp_size, bold=True, devanagari=_has_devanagari(rp)),
                _dejavu(rp_size),
            ]
            rmax = cw - 2 * (pad + inset) - 10 * SCALE
            has_media = bool(reply_preview.get("media"))
            thumb = 34 * SCALE if has_media else 0
            text_x = pad + inset + (3 * SCALE) + thumb + (8 * SCALE if thumb else 0)
            # color strip
            draw.line((pad + inset, cur_y - 4 * SCALE, pad + inset, cur_y + 18 * SCALE),
                      fill=pal["accent"], width=3 * SCALE)
            if has_media:
                box = Image.new("RGBA", (thumb, thumb), _alpha(pal["accent"], 40))
                ImageDraw.Draw(box).rounded_rectangle(
                    (0, 0, thumb, thumb), radius=6 * SCALE,
                    outline=_alpha(pal["accent"], 160), width=2 * SCALE)
                card.paste(box, (int(pad + inset + 3 * SCALE), int(cur_y)), box)
            txt = rp_name + ": " + rp
            txt_items = [_itemize(c, rp_chain, ef, rp_size) for c in _graphemes(txt)]
            # fit one line, truncate with ellipsis
            while txt_items and sum(_item_w(it, rp_size) for it in txt_items) > rmax - (text_x - (pad + inset)):
                txt_items = txt_items[:-1]
            if len(txt_items) < len(list(_graphemes(txt))):
                txt_items = txt_items + [('…', rp_chain[0])]
            _draw_line(draw, card, text_x, cur_y, txt_items, rp_size,
                       pal["reply"], _has_rtl(rp), rmax, ef)
            cur_y += 30 * SCALE

    # Divider
    div_y = cur_y + 2 * SCALE
    draw.line((pad + inset, div_y, cw - pad - inset, div_y),
              fill=pal["divider"], width=max(1, SCALE // 2))

    # Message body
    y = float(div_y + 14 * SCALE)
    for line in lines:
        _draw_line(draw, card, float(pad + inset), y, line, size, pal["text"],
                   rtl, body_max_w, ef)
        y += lh

    # Timestamp (Telegram-style, bottom-right)
    if timestamp:
        ts_size = 15 * SCALE
        ts_font = _pick_text_font(ts_size)
        tw = ts_font.getlength(timestamp)
        drop = Image.new("RGBA", (int(tw), ts_size), (0, 0, 0, 0))
        ImageDraw.Draw(drop).text((0, 0), timestamp, font=ts_font,
                                  fill=_alpha(pal["bg"], 140))
        card.paste(drop, (int(cw - pad - inset - tw), int(card_h - pad - inset - ts_size)), drop)
        draw.text((cw - pad - inset - tw, card_h - pad - inset - ts_size),
                  timestamp, font=ts_font, fill=pal["ts"])

    # Downscale to final (anti-aliased) resolution.
    out_h = int(card_h / SCALE)
    final = card.resize((CARD_W, out_h), Image.LANCZOS)

    buf = io.BytesIO()
    if mode == "sticker":
        out = Image.new("RGBA", (CARD_W, CARD_W), (0, 0, 0, 0))
        paste_y = max(0, (CARD_W - out_h) // 2)
        out.paste(final, (0, paste_y), final)
        out.save(buf, format="WEBP", lossless=True)
    else:
        final.save(buf, format="PNG")
    return buf.getvalue()
