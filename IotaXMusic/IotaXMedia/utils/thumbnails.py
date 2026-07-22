# Authored By Iota Coders © 2025
import os
import re
import aiofiles
import aiohttp
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from IotaXMedia.platforms.ytsearch import VideosSearch
from config import YOUTUBE_IMG_URL, youtube_thumb
from IotaXMedia.core.dir import CACHE_DIR


PANEL_W, PANEL_H = 763, 545
PANEL_X = (1280 - PANEL_W) // 2
PANEL_Y = 88
TRANSPARENCY = 170
INNER_OFFSET = 36

THUMB_W, THUMB_H = 542, 273
THUMB_X = PANEL_X + (PANEL_W - THUMB_W) // 2
THUMB_Y = PANEL_Y + INNER_OFFSET

TITLE_X = 377
META_X = 377
TITLE_Y = THUMB_Y + THUMB_H + 10
META_Y = TITLE_Y + 45

BAR_X, BAR_Y = 388, META_Y + 45
BAR_RED_LEN = 280
BAR_TOTAL_LEN = 480

ICONS_W, ICONS_H = 415, 45
ICONS_X = PANEL_X + (PANEL_W - ICONS_W) // 2
ICONS_Y = BAR_Y + 48

MAX_TITLE_WIDTH = 580


def trim_to_width(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> str:
    ellipsis = "…"
    if font.getlength(text) <= max_w:
        return text
    for i in range(len(text) - 1, 0, -1):
        if font.getlength(text[:i] + ellipsis) <= max_w:
            return text[:i] + ellipsis
    return ellipsis


def _is_valid_thumb_url(url: str) -> bool:
    if not url or not str(url).startswith("http"):
        return False
    # Reject old telegra placeholder
    if "telegra.ph/file/2c6d1a6f78eba6199933a" in url:
        return False
    return True


async def _download_bytes(url: str) -> bytes | None:
    if not url:
        return None
    # Local file path support
    if not str(url).startswith("http") and os.path.isfile(url):
        try:
            async with aiofiles.open(url, "rb") as f:
                data = await f.read()
            return data if data and len(data) > 500 else None
        except Exception:
            return None
    if not str(url).startswith("http"):
        return None
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    if data and len(data) > 500:
                        return data
    except Exception:
        return None
    return None


async def get_thumb(videoid: str, fallback_url: str | None = None) -> str:
    """
    Build styled now-playing card from the track's real thumbnail.
    Prefers YouTube CDN by video id, then search metadata, then fallback_url.
    """
    videoid = (videoid or "").strip()
    cache_path = os.path.join(CACHE_DIR, f"{videoid or 'unknown'}_v5.png")
    if videoid and os.path.exists(cache_path):
        return cache_path

    title = "Now Playing"
    duration = None
    views = "Unknown Views"
    thumbnail = None

    # 1) Direct YouTube CDN (always correct art for YT ids)
    if len(videoid) == 11:
        for q in ("maxresdefault", "sddefault", "hqdefault", "mqdefault"):
            candidate = youtube_thumb(videoid, q)
            raw = await _download_bytes(candidate)
            if raw:
                # maxresdefault sometimes returns a tiny gray placeholder (~1KB)
                if q == "maxresdefault" and len(raw) < 5000:
                    continue
                thumbnail = candidate
                break

    # 2) Metadata search for title + alternate thumb
    if videoid:
        try:
            results = VideosSearch(f"https://www.youtube.com/watch?v={videoid}", limit=1)
            results_data = await results.next()
            result_items = results_data.get("result", [])
            if result_items:
                data = result_items[0]
                title = re.sub(r"\W+", " ", data.get("title", title)).title()
                duration = data.get("duration")
                views = data.get("viewCount", {}).get("short", views)
                meta_thumb = data.get("thumbnail") or (
                    (data.get("thumbnails") or [{}])[0].get("url")
                )
                if not thumbnail and _is_valid_thumb_url(meta_thumb or ""):
                    thumbnail = str(meta_thumb).split("?")[0]
        except Exception:
            pass

    # 3) Caller-provided track thumb (e.g. SoundCloud art)
    if not thumbnail and _is_valid_thumb_url(fallback_url or ""):
        thumbnail = str(fallback_url).split("?")[0]

    if not thumbnail:
        if len(videoid) == 11:
            thumbnail = youtube_thumb(videoid)
        elif _is_valid_thumb_url(fallback_url or ""):
            thumbnail = str(fallback_url).split("?")[0]
        else:
            # Local branded asset path (not a random YouTube video)
            thumbnail = YOUTUBE_IMG_URL

    is_live = not duration or str(duration).strip().lower() in {"", "live", "live now"}
    duration_text = "Live" if is_live else duration or "Unknown Mins"

    thumb_path = os.path.join(CACHE_DIR, f"thumb{videoid or 'x'}.png")
    raw = await _download_bytes(thumbnail)
    if not raw and len(videoid) == 11:
        raw = await _download_bytes(youtube_thumb(videoid, "hqdefault"))
    if not raw:
        # Last resort: send direct URL (Telegram will fetch it)
        return thumbnail if _is_valid_thumb_url(thumbnail) else YOUTUBE_IMG_URL

    try:
        async with aiofiles.open(thumb_path, "wb") as f:
            await f.write(raw)
    except Exception:
        return thumbnail

    try:
        base = Image.open(thumb_path).convert("RGBA")
        base = base.resize((1280, 720))
        bg = ImageEnhance.Brightness(base.filter(ImageFilter.BoxBlur(10))).enhance(0.6)

        panel_area = bg.crop((PANEL_X, PANEL_Y, PANEL_X + PANEL_W, PANEL_Y + PANEL_H))
        overlay = Image.new("RGBA", (PANEL_W, PANEL_H), (255, 255, 255, TRANSPARENCY))
        frosted = Image.alpha_composite(panel_area, overlay)
        mask = Image.new("L", (PANEL_W, PANEL_H), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, PANEL_W, PANEL_H), 50, fill=255)
        bg.paste(frosted, (PANEL_X, PANEL_Y), mask)

        draw = ImageDraw.Draw(bg)
        try:
            title_font = ImageFont.truetype("IotaXMedia/assets/thumb/font2.ttf", 32)
            regular_font = ImageFont.truetype("IotaXMedia/assets/thumb/font.ttf", 18)
        except OSError:
            title_font = regular_font = ImageFont.load_default()

        thumb = base.resize((THUMB_W, THUMB_H))
        tmask = Image.new("L", thumb.size, 0)
        ImageDraw.Draw(tmask).rounded_rectangle((0, 0, THUMB_W, THUMB_H), 20, fill=255)
        bg.paste(thumb, (THUMB_X, THUMB_Y), tmask)

        draw.text(
            (TITLE_X, TITLE_Y),
            trim_to_width(title, title_font, MAX_TITLE_WIDTH),
            fill="black",
            font=title_font,
        )
        draw.text((META_X, META_Y), f"Music | {views}", fill="black", font=regular_font)

        draw.line([(BAR_X, BAR_Y), (BAR_X + BAR_RED_LEN, BAR_Y)], fill="red", width=6)
        draw.line(
            [(BAR_X + BAR_RED_LEN, BAR_Y), (BAR_X + BAR_TOTAL_LEN, BAR_Y)],
            fill="gray",
            width=5,
        )
        draw.ellipse(
            [
                (BAR_X + BAR_RED_LEN - 7, BAR_Y - 7),
                (BAR_X + BAR_RED_LEN + 7, BAR_Y + 7),
            ],
            fill="red",
        )

        draw.text((BAR_X, BAR_Y + 15), "00:00", fill="black", font=regular_font)
        end_text = "Live" if is_live else duration_text
        draw.text(
            (BAR_X + BAR_TOTAL_LEN - (90 if is_live else 60), BAR_Y + 15),
            end_text,
            fill="red" if is_live else "black",
            font=regular_font,
        )

        icons_path = "IotaXMedia/assets/thumb/play_icons.png"
        if os.path.isfile(icons_path):
            ic = Image.open(icons_path).resize((ICONS_W, ICONS_H)).convert("RGBA")
            r, g, b, a = ic.split()
            black_ic = Image.merge(
                "RGBA",
                (r.point(lambda *_: 0), g.point(lambda *_: 0), b.point(lambda *_: 0), a),
            )
            bg.paste(black_ic, (ICONS_X, ICONS_Y), black_ic)

        bg.save(cache_path)
        return cache_path
    except Exception:
        return thumbnail if _is_valid_thumb_url(thumbnail) else YOUTUBE_IMG_URL
    finally:
        try:
            os.remove(thumb_path)
        except OSError:
            pass
