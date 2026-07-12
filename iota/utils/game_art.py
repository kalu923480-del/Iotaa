"""
Iota Bot — Game Art Engine (Pillow PNG renderers)
═══════════════════════════════════════════════════
One shared renderer for every mini-game's raster art: cards, dice, slots,
roulette, prize wheel, scoreboards and leaderboards. The whole games section
renders from ONE palette + ONE font stack so it looks like one product.

Design contract (mirrors webapp/ludo/static/css/ludo.css):
  stage  #0f1220 · panel #171b2e · text #f0ece0 · amber #ffb648

Emoji rendering reuses the exact approach from utils/quote_render.py:
NotoColorEmoji is a color BITMAP font that only loads at its native 109px
size, so we render a glyph, crop, then downscale. Text + emoji are drawn as
separate segments so metrics never clash.

SAFETY: every public renderer returns PNG bytes (io.BytesIO). The async
`send_game_art` wrapper NEVER raises — on any failure it silently falls back
to the plain-text caption, exactly like utils/game_ui.send_gif_result. This
is what lets us add real art without ever breaking a game flow.
"""
import io
import logging
import re

from PIL import Image, ImageDraw, ImageFont

from utils.font_manager import load_font

logger = logging.getLogger(__name__)

# ── Palette (game-ui design tokens) ────────────────────────────────────────
STAGE    = (15, 18, 32)
PANEL    = (23, 27, 46)
PANEL_2  = (31, 36, 56)
LINE     = (42, 47, 71)
TEXT     = (240, 236, 224)
TEXT_DIM = (154, 160, 189)
AMBER    = (255, 182, 72)
AMBER_DIM= (184, 132, 58)
RED      = (231, 76, 90)
GREEN    = (86, 200, 120)
GOLD     = (255, 214, 102)

# ── Fonts / emoji ──────────────────────────────────────────────────────────
_EMOJI_FONT_PATH = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
_EMOJI_NATIVE = 109
_emoji_font_cache = None
_emoji_glyph_cache: dict = {}

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


# ── Low-level helpers ───────────────────────────────────────────────────────
def _text_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "NotoSans-Bold.ttf" if bold else "NotoSans-Regular.ttf"
    return load_font(name, size) or ImageFont.load_default()


def _emoji_font():
    global _emoji_font_cache
    if _emoji_font_cache is not None:
        return _emoji_font_cache
    try:
        if __import__("os").path.exists(_EMOJI_FONT_PATH):
            _emoji_font_cache = ImageFont.truetype(_EMOJI_FONT_PATH, _EMOJI_NATIVE)
        else:
            _emoji_font_cache = False
    except Exception as e:  # never fatal
        logger.debug(f"emoji font load failed: {e}")
        _emoji_font_cache = False
    return _emoji_font_cache or None


def _render_emoji_glyph(char: str, size: int):
    """Render one emoji to an RGBA image sized `size`x`size` (cached)."""
    key = (char, size)
    if key in _emoji_glyph_cache:
        return _emoji_glyph_cache[key]
    font = _emoji_font()
    if not font:
        _emoji_glyph_cache[key] = None
        return None
    try:
        canvas = Image.new("RGBA", (_EMOJI_NATIVE, _EMOJI_NATIVE), (0, 0, 0, 0))
        ImageDraw.Draw(canvas).text((0, 0), char, font=font, embedded_color=True)
        bbox = canvas.getbbox()
        if bbox:
            canvas = canvas.crop(bbox)
        out = canvas.resize((size, size), Image.LANCZOS)
        _emoji_glyph_cache[key] = out
        return out
    except Exception as e:
        logger.debug(f"emoji glyph failed for {char!r}: {e}")
        _emoji_glyph_cache[key] = None
        return None


def _new_canvas(w: int, h: int, bg=PANEL) -> Image.Image:
    return Image.new("RGBA", (w, h), bg + (255,))


def _finalize(img: Image.Image, bg=STAGE) -> io.BytesIO:
    """Composite transparent areas over `bg`, return RGB PNG bytes."""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    base = Image.new("RGB", img.size, bg)
    base.paste(img, (0, 0), img)
    buf = io.BytesIO()
    base.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _rounded_rect(draw: ImageDraw.ImageDraw, box, radius: int, fill=None,
                  outline=None, width: int = 2):
    try:
        draw.rounded_rectangle(box, radius=radius, fill=fill,
                               outline=outline, width=width)
    except Exception:
        # older Pillow without rounded_rectangle
        draw.rectangle(box, fill=fill, outline=outline, width=width)


def _segment(text: str) -> list:
    """Split into [(chunk, is_emoji), ...] runs."""
    if not text:
        return []
    parts = _EMOJI_RE.split(text)
    out = []
    for p in parts:
        if not p:
            continue
        e = bool(_EMOJI_RE.fullmatch(p))
        if out and out[-1][1] == e:
            out[-1] = (out[-1][0] + p, e)
        else:
            out.append((p, e))
    return out


def _line_width(chunks: list, size: int) -> int:
    total = 0
    try:
        dummy = ImageDraw.Draw(_new_canvas(10, 10))
    except Exception:
        dummy = None
    for chunk, is_emoji in chunks:
        if is_emoji:
            total += size
        else:
            f = _text_font(size)
            if dummy is not None:
                total += dummy.textlength(chunk, font=f)
            else:
                total += len(chunk) * size * 0.6
    return int(total)


def _draw_line_centered(img: Image.Image, cx: int, cy: int, text: str,
                        size: int, color=TEXT, bold: bool = False):
    """Draw a single line centered at (cx, cy). Emoji + text mixed safely."""
    chunks = _segment(text)
    if not chunks:
        return
    total = _line_width(chunks, size)
    x = cx - total // 2
    ascent = size  # approximate baseline offset
    for chunk, is_emoji in chunks:
        if is_emoji:
            gl = _render_emoji_glyph(chunk, size)
            if gl is not None:
                img.paste(gl, (int(x), int(cy - size // 2)), gl)
            x += size
        else:
            f = _text_font(size, bold)
            ImageDraw.Draw(img).text((x, cy - ascent // 2), chunk,
                                     font=f, fill=color)
            try:
                dummy = ImageDraw.Draw(_new_canvas(10, 10))
                x += dummy.textlength(chunk, font=f)
            except Exception:
                x += len(chunk) * size * 0.6


def _draw_text_left(img: Image.Image, x: int, y: int, text: str, size: int,
                    color=TEXT, bold: bool = False):
    """Draw a single line left-aligned at top-left (x, y)."""
    chunks = _segment(text)
    cx = x
    for chunk, is_emoji in chunks:
        if is_emoji:
            gl = _render_emoji_glyph(chunk, size)
            if gl is not None:
                img.paste(gl, (int(cx), int(y)), gl)
            cx += size
        else:
            f = _text_font(size, bold)
            ImageDraw.Draw(img).text((cx, y), chunk, font=f, fill=color)
            try:
                dummy = ImageDraw.Draw(_new_canvas(10, 10))
                cx += dummy.textlength(chunk, font=f)
            except Exception:
                cx += len(chunk) * size * 0.6


# ── Public renderers ───────────────────────────────────────────────────────
_SUITS = {
    "spades":   ("♠", TEXT),
    "hearts":   ("♥", RED),
    "diamonds": ("♦", RED),
    "clubs":    ("♣", TEXT),
}


def render_card(rank: str, suit: str = "spades", hidden: bool = False) -> io.BytesIO:
    """A themed playing-card face (or back when `hidden`)."""
    w, h = 220, 320
    img = _new_canvas(w, h)
    d = ImageDraw.Draw(img)
    _rounded_rect(d, (4, 4, w - 4, h - 4), 18, fill=PANEL_2,
                  outline=AMBER_DIM, width=3)
    if hidden:
        for i in range(0, w, 18):
            for j in range(0, h, 18):
                if ((i + j) // 18) % 2 == 0:
                    d.rectangle((i, j, i + 16, j + 16), fill=(28, 34, 56))
        _draw_line_centered(img, w // 2, h // 2, "🂠", 120, AMBER)
        return _finalize(img)
    sym, col = _SUITS.get(suit, _SUITS["spades"])
    _draw_text_left(img, 14, 12, f"{rank}", 30, col, bold=True)
    _draw_text_left(img, 12, 46, sym, 26, col, bold=True)
    _draw_line_centered(img, w // 2, h // 2 + 10, sym, 110, col)
    f = _text_font(30, True)
    try:
        dummy = ImageDraw.Draw(_new_canvas(10, 10))
        rw = dummy.textlength(rank, font=f)
    except Exception:
        rw = 30
    _draw_text_left(img, w - 14 - int(rw), h - 12 - 30, rank, 30, col, bold=True)
    _draw_text_left(img, w - 12 - 26, h - 12 - 30 - 34, sym, 26, col, bold=True)
    return _finalize(img)


def render_dice(value: int) -> io.BytesIO:
    """A single die face (1–6) with weighted pips."""
    value = max(1, min(6, int(value)))
    s = 160
    img = _new_canvas(s, s)
    d = ImageDraw.Draw(img)
    _rounded_rect(d, (6, 6, s - 6, s - 6), 28, fill=PANEL_2,
                  outline=AMBER_DIM, width=3)
    pad = 44
    lo, hi, mid = pad, s - pad, s // 2
    pip = 16
    layouts = {
        1: [(mid, mid)],
        2: [(lo, lo), (hi, hi)],
        3: [(lo, lo), (mid, mid), (hi, hi)],
        4: [(lo, lo), (hi, lo), (lo, hi), (hi, hi)],
        5: [(lo, lo), (hi, lo), (mid, mid), (lo, hi), (hi, hi)],
        6: [(lo, lo), (hi, lo), (lo, mid), (hi, mid), (lo, hi), (hi, hi)],
    }
    for (px, py) in layouts[value]:
        d.ellipse((px - pip // 2, py - pip // 2, px + pip // 2, py + pip // 2),
                  fill=AMBER)
    return _finalize(img)


def render_dice_row(values: list) -> io.BytesIO:
    """A row of dice (e.g. for /dice)."""
    cell = 170
    n = max(1, len(values))
    img = _new_canvas(cell * n, cell)
    for i, v in enumerate(values[:8]):
        sub = render_dice(v)
        face = Image.open(sub).convert("RGBA")
        img.paste(face, (i * cell, 0), face)
    return _finalize(img)


def render_slots(reels: list) -> io.BytesIO:
    """A 3-reel slot strip with a highlight frame."""
    cols = 3
    cw, ch = 150, 150
    img = _new_canvas(cw * cols + 20, ch + 20)
    d = ImageDraw.Draw(img)
    _rounded_rect(d, (4, 4, cw * cols + 16, ch + 16), 16,
                  fill=PANEL, outline=AMBER_DIM, width=3)
    for i, sym in enumerate(reels[:cols]):
        x = 10 + i * cw
        _rounded_rect(d, (x + 4, 14, x + cw - 4, 14 + ch - 8),
                      12, fill=PANEL_2, outline=LINE, width=2)
        _draw_line_centered(img, x + cw // 2, 14 + ch // 2, str(sym), 64, TEXT)
    return _finalize(img)


def render_wheel(segments: list, winner: int = None) -> io.BytesIO:
    """A prize/fortune wheel. `segments` = list of label strings.
    `winner` (index) segment is highlighted with a pointer."""
    size = 360
    img = _new_canvas(size, size)
    d = ImageDraw.Draw(img)
    cx = cy = size // 2
    r = size // 2 - 8
    n = max(1, len(segments))
    palette = [AMBER, PANEL_2, GOLD, PANEL_2, GREEN, PANEL_2, RED, PANEL_2]
    for i, label in enumerate(segments):
        a0 = 360 * i / n
        a1 = 360 * (i + 1) / n
        fill = palette[i % len(palette)]
        if winner is not None and i == (winner % n):
            fill = GREEN
        d.pieslice((cx - r, cy - r, cx + r, cy + r), a0, a1, fill=fill,
                   outline=LINE, width=2)
        ang = (a0 + a1) / 2
        rad = ang * 3.14159 / 180
        lx = cx + int((r * 0.62) * _cos(rad))
        ly = cy + int((r * 0.62) * _sin(rad))
        _draw_line_centered(img, lx, ly, str(label), 22, TEXT, bold=True)
    d.ellipse((cx - 22, cy - 22, cx + 22, cy + 22), fill=AMBER, outline=STAGE, width=3)
    d.polygon([(cx, 4), (cx - 14, 26), (cx + 14, 26)], fill=RED)
    return _finalize(img)


def render_roulette(result: int = None, pockets: int = 37) -> io.BytesIO:
    """Stylised roulette wheel (0 = green, rest red/black alternating)."""
    size = 360
    img = _new_canvas(size, size)
    d = ImageDraw.Draw(img)
    cx = cy = size // 2
    r = size // 2 - 8
    for i in range(pockets):
        a0 = 360 * i / pockets
        a1 = 360 * (i + 1) / pockets
        if i == 0:
            fill = GREEN
        elif i % 2 == 0:
            fill = RED
        else:
            fill = STAGE
        d.pieslice((cx - r, cy - r, cx + r, cy + r), a0, a1, fill=fill,
                   outline=LINE, width=1)
    if result is not None:
        a = 360 * (result % pockets) / pockets + 360 / pockets / 2
        rad = a * 3.14159 / 180
        px = cx + int((r - 6) * _cos(rad))
        py = cy + int((r - 6) * _sin(rad))
        d.ellipse((px - 12, py - 12, px + 12, py + 12), fill=GOLD, outline=STAGE, width=3)
    d.ellipse((cx - 40, cy - 40, cx + 40, cy + 40), fill=PANEL_2, outline=AMBER, width=3)
    _draw_line_centered(img, cx, cy, str(result) if result is not None else "🎡",
                        30, TEXT, bold=True)
    d.polygon([(cx, 4), (cx - 14, 26), (cx + 14, 26)], fill=AMBER)
    return _finalize(img)


def render_scoreboard(rows: list, title: str = "🏆 Scoreboard") -> io.BytesIO:
    """`rows` = list of (name, score). Renders a tidy table."""
    pad = 24
    row_h = 46
    w = 460
    h = pad * 2 + 44 + len(rows) * row_h
    img = _new_canvas(w, max(h, 160))
    d = ImageDraw.Draw(img)
    _rounded_rect(d, (4, 4, w - 4, img.height - 4), 16, fill=PANEL,
                  outline=AMBER_DIM, width=3)
    _draw_line_centered(img, w // 2, pad + 14, title, 26, AMBER, bold=True)
    y = pad + 44
    for i, (name, score) in enumerate(rows[:12]):
        if i % 2 == 0:
            d.rectangle((14, y - 4, w - 14, y + row_h - 6), fill=PANEL_2)
        _draw_text_left(img, 28, y + 12, f"{name}", 22, TEXT)
        _draw_text_left(img, w - 28 - 120, y + 12, f"{score}", 22, GOLD, bold=True)
        y += row_h
    return _finalize(img)


def render_leaderboard(rows: list, title: str = "📊 Leaderboard") -> io.BytesIO:
    """`rows` = list of (rank, name, score); rank drives medal emoji."""
    medals = ["🥇", "🥈", "🥉"]
    pad = 24
    row_h = 46
    w = 480
    h = pad * 2 + 44 + len(rows) * row_h
    img = _new_canvas(w, max(h, 160))
    d = ImageDraw.Draw(img)
    _rounded_rect(d, (4, 4, w - 4, img.height - 4), 16, fill=PANEL,
                  outline=AMBER_DIM, width=3)
    _draw_line_centered(img, w // 2, pad + 14, title, 26, AMBER, bold=True)
    y = pad + 44
    for i, (rank, name, score) in enumerate(rows[:12]):
        if i % 2 == 0:
            d.rectangle((14, y - 4, w - 14, y + row_h - 6), fill=PANEL_2)
        badge = medals[rank - 1] if 1 <= rank <= 3 else f"{rank}."
        _draw_text_left(img, 26, y + 12, badge, 22, TEXT)
        _draw_text_left(img, 78, y + 12, f"{name}", 22, TEXT)
        _draw_text_left(img, w - 28 - 120, y + 12, f"{score}", 22, GOLD, bold=True)
        y += row_h
    return _finalize(img)


def render_hangman(stage: int = 0) -> io.BytesIO:
    """Stick-figure gallows for /hangman. `stage` 0..6 draws progressively
    (base, post, beam, rope, head, body, limbs)."""
    stage = max(0, min(6, int(stage)))
    w, h = 300, 360
    img = _new_canvas(w, h)
    d = ImageDraw.Draw(img)
    line_w = 10
    # Base + upright post + top beam (always drawn)
    d.line((40, h - 20, w - 40, h - 20), fill=PANEL_2, width=line_w)
    d.line((70, h - 20, 70, 30), fill=PANEL_2, width=line_w)
    d.line((70, 30, 200, 30), fill=PANEL_2, width=line_w)
    d.line((200, 30, 200, 70), fill=PANEL_2, width=line_w)  # rope
    cx, cy = 200, 110
    r = 26
    if stage >= 1:  # head
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=AMBER, width=6)
    if stage >= 2:  # body
        d.line((cx, cy + r, cx, cy + r + 80), fill=AMBER, width=6)
    if stage >= 3:  # left arm
        d.line((cx, cy + r + 20, cx - 45, cy + r + 55), fill=AMBER, width=6)
    if stage >= 4:  # right arm
        d.line((cx, cy + r + 20, cx + 45, cy + r + 55), fill=AMBER, width=6)
    if stage >= 5:  # left leg
        d.line((cx, cy + r + 80, cx - 40, cy + r + 130), fill=AMBER, width=6)
    if stage >= 6:  # right leg
        d.line((cx, cy + r + 80, cx + 40, cy + r + 130), fill=AMBER, width=6)
    _draw_line_centered(img, w // 2, 20, f"ʜᴀɴɢᴍᴀɴ — {stage}/6", 22, AMBER, bold=True)
    return _finalize(img)


def render_bomb(defused: bool = False, seconds_left: int = 0,
                 wires: list = None) -> io.BytesIO:
    """Defuse board for /bomb. Shows a bomb, a countdown, and colour wires
    (green=cut safe, red=still live)."""
    w, h = 360, 320
    img = _new_canvas(w, h)
    d = ImageDraw.Draw(img)
    col = GREEN if defused else RED
    _rounded_rect(d, (20, 20, w - 20, h - 20), 18, fill=PANEL,
                  outline=AMBER_DIM, width=3)
    # bomb body
    d.ellipse((120, 120, 240, 240), fill=PANEL_2, outline=col, width=6)
    d.rectangle((168, 80, 192, 120), fill=PANEL_2, outline=col, width=4)
    d.line((180, 80, 200, 60), fill=AMBER, width=5)  # fuse
    _draw_line_centered(img, 180, 56, "🔥", 26, AMBER)
    _draw_line_centered(img, w // 2, 48,
                        "✅ sᴀғᴇ" if defused else f"⏳ {seconds_left}s",
                        26, col, bold=True)
    wires = wires or ["red", "green", "blue", "yellow"]
    y = 270
    for i, c in enumerate(wires):
        wc = {"red": RED, "green": GREEN, "blue": (90, 160, 255),
              "yellow": GOLD}.get(c, TEXT)
        x = 60 + i * 80
        d.line((x, 250, x, y), fill=wc, width=8)
    return _finalize(img)


def render_ludo_share(rows: list, title: str = "🎲 ʟᴜᴅᴏ") -> io.BytesIO:
    """Final-board share image for /ludo — a tidy scoreboard PNG."""
    return render_scoreboard(rows, title)


# tiny trig helpers (avoid importing math just for two calls readability)
def _cos(a):
    import math
    return math.cos(a)


def _sin(a):
    import math
    return math.sin(a)


# ── Async send wrapper (NEVER raises) ──────────────────────────────────────
async def send_game_art(context, chat_id: int, render, caption: str = "",
                        reply_markup=None, parse_mode: str = "HTML"):
    """
    Send a game-art PNG produced by callable `render` (returns io.BytesIO).
    On ANY failure, silently fall back to the plain-text caption so a game
    flow is never broken by a missing font / PIL quirk.
    """
    png = None
    try:
        png = render() if callable(render) else render
    except Exception as e:
        logger.debug(f"game_art render failed: {e}")
    if png is not None:
        try:
            await context.bot.send_photo(
                chat_id, photo=png, caption=caption,
                parse_mode=parse_mode, reply_markup=reply_markup,
            )
            return
        except Exception as e:
            logger.debug(f"send_photo failed, falling back to text: {e}")
    try:
        await context.bot.send_message(
            chat_id, caption or "🎮", parse_mode=parse_mode, reply_markup=reply_markup
        )
    except Exception:
        pass
