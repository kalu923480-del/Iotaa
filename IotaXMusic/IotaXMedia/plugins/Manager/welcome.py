# Authored By Iota Coders © 2025
"""Group welcome cards with a rotating pack of 100+ internet music backgrounds."""
from __future__ import annotations

import asyncio
import os
import random
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pyrogram import filters, enums
from pyrogram.types import (
    Message,
    ChatMemberUpdated,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from IotaXMedia import app
from IotaXMedia.mongo.welcomedb import is_on, set_state, bump, cool, auto_on

ASSETS = Path("IotaXMedia/assets")
BG_DIR = ASSETS / "iota" / "welcome_bgs"
BG_FALLBACK = ASSETS / "iota" / "welcome.png"
FALLBACK_PIC = str(ASSETS / "upic.png")
FONT_PATH = str(ASSETS / "iota" / "Arimo.ttf")

BTN_VIEW = "๏ ᴠɪᴇᴡ ɴᴇᴡ ᴍᴇᴍʙᴇʀ ๏"
BTN_ADD = "๏ ᴋɪᴅɴᴀᴘ ᴍᴇ ๏"

CAPTION_TXT = """
**❅────✦ ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ ✦────❅
{chat_title}
▰▰▰▰▰▰▰▰▰▰▰▰▰
➻ Nᴀᴍᴇ ✧ {mention}
➻ Iᴅ ✧ `{uid}`
➻ Usᴇʀɴᴀᴍᴇ ✧ @{uname}
➻ Tᴏᴛᴀʟ Mᴇᴍʙᴇʀs ✧ {count}
▰▰▰▰▰▰▰▰▰▰▰▰▰**
**❅─────✧❅✦❅✧─────❅**
"""

JOIN_THRESHOLD = 20
TIME_WINDOW = 10
COOL_MINUTES = 5
WELCOME_LIMIT = 5

last_messages: dict = {}
_recent_bgs: list[str] = []  # avoid immediate repeats
_RECENT_MAX = 12


def _list_backgrounds() -> list[Path]:
    files: list[Path] = []
    if BG_DIR.is_dir():
        for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
            files.extend(BG_DIR.glob(ext))
    files = [p for p in files if p.is_file() and p.stat().st_size > 5000]
    if not files and BG_FALLBACK.is_file():
        files = [BG_FALLBACK]
    return sorted(files)


def pick_background() -> Path:
    """Random pack image; avoid reusing the last few."""
    files = _list_backgrounds()
    if not files:
        return BG_FALLBACK
    if len(files) == 1:
        return files[0]
    choices = [p for p in files if p.name not in _recent_bgs] or files
    chosen = random.choice(choices)
    _recent_bgs.append(chosen.name)
    if len(_recent_bgs) > _RECENT_MAX:
        _recent_bgs.pop(0)
    return chosen


@lru_cache(maxsize=4)
def cached_font(size: int = 48):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except OSError:
        return ImageFont.load_default()


def circle(im: Image.Image, size: tuple[int, int] = (320, 320)) -> Image.Image:
    im = im.resize(size, Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size[0] - 1, size[1] - 1), fill=255)
    im.putalpha(mask)
    return im


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> str:
    text = (text or "").strip() or "User"
    if draw.textlength(text, font=font) <= max_w:
        return text
    while len(text) > 1 and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def build_pic(av: str, fn: str, uid: int, un: str) -> str:
    """
    Size-independent welcome card:
      • random music background from 100+ pack
      • frosted left panel + circular avatar with neon ring
      • name / id / username (no hard-coded 2880x1620 coords)
    """
    os.makedirs("downloads", exist_ok=True)
    bg_path = pick_background()
    try:
        bg = Image.open(bg_path).convert("RGBA")
    except Exception:
        bg = Image.open(BG_FALLBACK).convert("RGBA")

    # Normalize canvas to 1280x720 (or keep if already)
    W, H = 1280, 720
    if bg.size != (W, H):
        bg = bg.resize((W, H), Image.LANCZOS)

    # Darken overall slightly for text contrast
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 70))
    bg = Image.alpha_composite(bg, shade)

    # Left frosted panel
    panel = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pd = ImageDraw.Draw(panel)
    pd.rounded_rectangle(
        (40, 50, 620, H - 50),
        radius=28,
        fill=(12, 10, 28, 165),
        outline=(24, 224, 255, 140),
        width=3,
    )
    bg = Image.alpha_composite(bg, panel)

    # Avatar (right side)
    try:
        avatar = circle(Image.open(av), (300, 300))
    except Exception:
        avatar = circle(Image.open(FALLBACK_PIC), (300, 300))
    ax, ay = 820, 210
    # neon ring
    ring = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    cx, cy, r = ax + 150, ay + 150, 158
    for i, a in ((6, 50), (3, 100), (0, 200)):
        rd.ellipse(
            (cx - r - i, cy - r - i, cx + r + i, cy + r + i),
            outline=(24, 224, 255, a),
            width=4,
        )
    bg = Image.alpha_composite(bg, ring)
    bg.paste(avatar, (ax, ay), avatar)

    d = ImageDraw.Draw(bg)
    title_f = cached_font(42)
    label_f = cached_font(26)
    value_f = cached_font(36)
    small_f = cached_font(22)

    d.text((70, 80), "˹𝐈ᴏᴛᴀ ✘ 𝙼ᴜꜱɪᴄ˼", font=title_f, fill=(255, 255, 255, 255))
    d.text((70, 140), "Welcome", font=label_f, fill=(24, 224, 255, 255))

    name = _fit_text(d, str(fn), value_f, 500)
    uname = _fit_text(d, f"@{un}" if un and not str(un).startswith("@") else str(un), value_f, 500)

    d.text((70, 240), "NAME", font=small_f, fill=(180, 180, 220, 255))
    d.text((70, 275), name, font=value_f, fill=(242, 242, 242, 255))

    d.text((70, 360), "ID", font=small_f, fill=(180, 180, 220, 255))
    d.text((70, 395), str(uid), font=value_f, fill=(242, 242, 242, 255))

    d.text((70, 480), "USERNAME", font=small_f, fill=(180, 180, 220, 255))
    d.text((70, 515), uname, font=value_f, fill=(242, 242, 242, 255))

    d.text((70, 620), "Feel the beat • Stay in the vibe", font=small_f, fill=(200, 200, 230, 220))

    path = f"downloads/welcome_{uid}.png"
    bg.convert("RGB").save(path, "JPEG", quality=88, optimize=True)
    return path


async def safe_send(func, *args, **kwargs):
    try:
        return await func(*args, **kwargs)
    except Exception:
        return None


@app.on_message(filters.command("welcome") & filters.group)
async def toggle(client, m: Message):
    if len(m.command) < 2:
        n = len(_list_backgrounds())
        return await m.reply_text(
            f"**Usage:** `/welcome on|off|preview`\n"
            f"➤ Iota Special Welcome\n"
            f"🖼 Background pack: **{n}** images (random each join)"
        )
    user_id = m.from_user.id if m.from_user else (m.sender_chat.id if m.sender_chat else None)
    if not user_id:
        return
    try:
        u = await client.get_chat_member(m.chat.id, user_id)
    except Exception:
        return
    if u.status not in (enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER):
        return await m.reply_text("**sᴏʀʀʏ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴄʜᴀɴɢᴇ ᴡᴇʟᴄᴏᴍᴇ ɴᴏᴛɪғɪᴄᴀᴛɪᴏɴ sᴛᴀᴛᴜs!**")

    flag = m.command[1].lower()
    if flag == "preview":
        av = FALLBACK_PIC
        if m.from_user and m.from_user.photo:
            try:
                av = await client.download_media(
                    m.from_user.photo.big_file_id,
                    file_name=f"downloads/pp_{m.from_user.id}.png",
                )
            except Exception:
                av = FALLBACK_PIC
        img = build_pic(
            av,
            m.from_user.first_name if m.from_user else "User",
            m.from_user.id if m.from_user else 0,
            (m.from_user.username if m.from_user else None) or "No Username",
        )
        await m.reply_photo(img, caption=f"🖼 Random pack preview ({len(_list_backgrounds())} bgs)")
        try:
            if av != FALLBACK_PIC and os.path.exists(av):
                os.remove(av)
            if os.path.exists(img):
                os.remove(img)
        except OSError:
            pass
        return

    if flag not in ("on", "off"):
        return await m.reply_text("**Usage:** `/welcome on|off|preview`")
    cur = await is_on(m.chat.id)
    if flag == "off" and not cur:
        return await m.reply_text("**ᴡᴇʟᴄᴏᴍᴇ ɴᴏᴛɪғɪᴄᴀᴛɪᴏɴ ᴀʟʀᴇᴀᴅʏ ᴅɪsᴀʙʟᴇᴅ!**")
    if flag == "on" and cur:
        return await m.reply_text("**ᴡᴇʟᴄᴏᴍᴇ ɴᴏᴛɪғɪᴄᴀᴛɪᴏɴ ᴀʟʀᴇᴀᴅʏ ᴇɴᴀʙʟᴇᴅ!**")
    await set_state(m.chat.id, flag)
    await m.reply_text(
        f"**{'ᴇɴᴀʙʟᴇᴅ' if flag == 'on' else 'ᴅɪsᴀʙʟᴇᴅ'} ᴡᴇʟᴄᴏᴍᴇ ɪɴ {m.chat.title}**\n"
        f"🖼 Pack size: {len(_list_backgrounds())}"
    )


@app.on_chat_member_updated(filters.group, group=-3)
async def welcome(client, update: ChatMemberUpdated):
    new = update.new_chat_member
    old = update.old_chat_member
    cid = update.chat.id

    if not new or new.status != enums.ChatMemberStatus.MEMBER:
        return
    if old and old.status == enums.ChatMemberStatus.MEMBER:
        return

    if not hasattr(client, "cached_me"):
        try:
            client.cached_me = await client.get_me()
        except Exception:
            return
    me = client.cached_me

    try:
        await client.get_chat_member(cid, me.id)
    except Exception:
        return

    if not await is_on(cid):
        if await auto_on(cid):
            await safe_send(client.send_message, cid, "**ᴡᴇʟᴄᴏᴍᴇ ᴍᴇssᴀɢᴇs ʀᴇ-ᴇɴᴀʙʟᴇᴅ.**")
        else:
            return

    burst = await bump(cid, TIME_WINDOW)
    if burst >= JOIN_THRESHOLD:
        minutes = min(60, COOL_MINUTES + max(0, burst - JOIN_THRESHOLD) * 2)
        await cool(cid, minutes)
        await safe_send(
            client.send_message,
            cid,
            f"**ᴍᴀssɪᴠᴇ ᴊᴏɪɴ ᴅᴇᴛᴇᴄᴛᴇᴅ (x{burst}). ᴡᴇʟᴄᴏᴍᴇ ᴍᴇssᴀɢᴇs ᴅɪsᴀʙʟᴇᴅ ғᴏʀ {minutes} ᴍɪɴᴜᴛᴇs.**",
        )
        return

    user = new.user
    file_id = None
    if user.photo and hasattr(user.photo, "big_file_id"):
        file_id = user.photo.big_file_id

    avatar = (
        await safe_send(
            client.download_media, file_id, file_name=f"downloads/pp_{user.id}.png"
        )
        if file_id
        else None
    )
    if not avatar:
        avatar = FALLBACK_PIC

    img = build_pic(avatar, user.first_name, user.id, user.username or "No Username")

    members = await safe_send(client.get_chat_members_count, cid) or "?"

    caption = CAPTION_TXT.format(
        chat_title=update.chat.title,
        mention=user.mention,
        uid=user.id,
        uname=user.username or "No Username",
        count=members,
    )

    sent = await safe_send(
        client.send_photo,
        cid,
        img,
        caption=caption,
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(BTN_VIEW, url=f"tg://openmessage?user_id={user.id}")],
                [InlineKeyboardButton(BTN_ADD, url=f"https://t.me/{me.username}?startgroup=true")],
            ]
        ),
    )

    if sent:
        last_messages.setdefault(cid, []).append(sent)
        if len(last_messages[cid]) > WELCOME_LIMIT:
            old_msg = last_messages[cid].pop(0)
            if old_msg:
                await safe_send(old_msg.delete)

    async def cleanup(path):
        if (
            path
            and os.path.exists(path)
            and not os.path.abspath(path).startswith(os.path.abspath("IotaXMedia/assets"))
        ):
            try:
                os.remove(path)
            except OSError:
                pass

    asyncio.create_task(cleanup(avatar))
    asyncio.create_task(cleanup(img))
