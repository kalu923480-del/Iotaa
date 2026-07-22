# Authored By Iota Coders © 2025
"""
Owner asset manager — upload welcome / brand images from Telegram to the server.

Commands (OWNER only, private chat recommended):
  /addwelcome   — reply to photo/document OR send with caption
  /addbg        — alias of /addwelcome
  /delwelcome N — delete bg_N or file name
  /listwelcome  — list pack (paginated)
  /welcomepack  — pack stats
  /setwelcome   — set default fallback welcome.png (reply photo)
  /setupic      — set fallback avatar upic.png
  /settiny      — set tiny.png banner
  /setcouple    — set couple.png frame
  /clearwelcome — wipe custom uploads only (keeps seed pack if any)
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path

from PIL import Image
from pyrogram import filters
from pyrogram.types import Message

from IotaXMedia import app
from config import OWNER_ID

ASSETS = Path("IotaXMedia/assets")
IOTA = ASSETS / "iota"
BG_DIR = IOTA / "welcome_bgs"
CUSTOM_DIR = BG_DIR / "custom"  # owner uploads land here first, then copied into pack

BG_DIR.mkdir(parents=True, exist_ok=True)
CUSTOM_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_BYTES = 12 * 1024 * 1024  # 12 MB
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp"}
TARGET_BG = (1280, 720)


def _is_owner(m: Message) -> bool:
    return bool(m.from_user and m.from_user.id == OWNER_ID)


async def _download_image(message: Message) -> Path | None:
    """Download photo/document image from message or reply."""
    src = message.reply_to_message or message
    if not src:
        return None

    file_id = None
    suggested = "upload.bin"
    if src.photo:
        file_id = src.photo.file_id
        suggested = "upload.jpg"
    elif src.document:
        mime = (src.document.mime_type or "").lower()
        name = src.document.file_name or "upload.bin"
        ext = Path(name).suffix.lower()
        if not (mime.startswith("image/") or ext in ALLOWED_EXT):
            return None
        if src.document.file_size and src.document.file_size > MAX_FILE_BYTES:
            return None
        file_id = src.document.file_id
        suggested = name if ext in ALLOWED_EXT else "upload.png"
    else:
        return None

    os.makedirs("downloads", exist_ok=True)
    path = await message._client.download_media(
        file_id, file_name=f"downloads/owner_{message.id}_{suggested}"
    )
    if not path or not os.path.exists(path):
        return None
    if os.path.getsize(path) > MAX_FILE_BYTES:
        try:
            os.remove(path)
        except OSError:
            pass
        return None
    return Path(path)


def _process_bg(src: Path, dest: Path) -> None:
    """Resize/crop to welcome canvas and save as JPEG pack member."""
    im = Image.open(src).convert("RGB")
    # cover fit
    tw, th = TARGET_BG
    sw, sh = im.size
    scale = max(tw / sw, th / sh)
    nw, nh = int(sw * scale), int(sh * scale)
    im = im.resize((nw, nh), Image.LANCZOS)
    left = (nw - tw) // 2
    top = (nh - th) // 2
    im = im.crop((left, top, left + tw, top + th))
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest, "JPEG", quality=85, optimize=True, progressive=True)


def _process_exact(src: Path, dest: Path, size: tuple[int, int] | None = None) -> None:
    im = Image.open(src).convert("RGB")
    if size:
        from PIL import ImageOps

        im = ImageOps.fit(im, size, method=Image.LANCZOS, centering=(0.5, 0.45))
    dest.parent.mkdir(parents=True, exist_ok=True)
    # keep png extension for brand assets
    if dest.suffix.lower() == ".png":
        im.save(dest, "PNG", optimize=True)
    else:
        im.save(dest, "JPEG", quality=88, optimize=True)


def _next_bg_index() -> int:
    nums = []
    for p in BG_DIR.glob("bg_*.jpg"):
        m = re.match(r"bg_(\d+)\.jpg$", p.name)
        if m:
            nums.append(int(m.group(1)))
    for p in BG_DIR.glob("bg_*.png"):
        m = re.match(r"bg_(\d+)\.png$", p.name)
        if m:
            nums.append(int(m.group(1)))
    return (max(nums) + 1) if nums else 1


def _list_bgs() -> list[Path]:
    files = []
    for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
        files.extend(BG_DIR.glob(ext))
    return sorted([p for p in files if p.is_file() and p.stat().st_size > 2000])


def _refresh_manifest() -> int:
    files = _list_bgs()
    try:
        import json

        (BG_DIR / "manifest.json").write_text(
            json.dumps(
                {
                    "count": len(files),
                    "files": [p.name for p in files],
                    "size": list(TARGET_BG),
                },
                indent=2,
            )
        )
    except Exception:
        pass
    return len(files)


@app.on_message(
    filters.command(["addwelcome", "addbg", "uploadwelcome"], prefixes=["/", "!"])
    & filters.user(OWNER_ID)
)
async def add_welcome_bg(_, message: Message):
    if not _is_owner(message):
        return
    status = await message.reply_text("⬇️ ᴅᴏᴡɴʟᴏᴀᴅɪɴɢ ɪᴍᴀɢᴇ…")
    src = await _download_image(message)
    if not src:
        return await status.edit_text(
            "❌ Reply to a **photo** / **image document**, or send photo with caption "
            "`/addwelcome`.\nMax **12 MB** · png/jpg/webp"
        )
    try:
        idx = _next_bg_index()
        dest = BG_DIR / f"bg_{idx:03d}.jpg"
        await asyncio.to_thread(_process_bg, src, dest)
        # also keep original in custom/
        custom = CUSTOM_DIR / f"owner_{idx:03d}{src.suffix.lower() or '.jpg'}"
        try:
            shutil.copy2(src, custom)
        except Exception:
            pass
        total = _refresh_manifest()
        # clear welcome module recent cache if loaded
        try:
            from IotaXMedia.plugins.Manager import welcome as wel

            if hasattr(wel, "_recent_bgs"):
                wel._recent_bgs.clear()
        except Exception:
            pass
        await status.edit_text(
            f"✅ **Welcome BG saved**\n"
            f"• File: `{dest.name}`\n"
            f"• Size: {dest.stat().st_size // 1024} KB · {TARGET_BG[0]}x{TARGET_BG[1]}\n"
            f"• Pack total: **{total}** images\n\n"
            f"Next joins will randomly use this pack."
        )
    except Exception as e:
        await status.edit_text(f"❌ Failed to process image:\n`{e}`")
    finally:
        try:
            if src and src.exists():
                src.unlink()
        except OSError:
            pass


@app.on_message(
    filters.command(["setwelcome"], prefixes=["/", "!"]) & filters.user(OWNER_ID)
)
async def set_default_welcome(_, message: Message):
    status = await message.reply_text("⬇️ sᴇᴛᴛɪɴɢ ᴅᴇғᴀᴜʟᴛ ᴡᴇʟᴄᴏᴍᴇ.ᴘɴɢ…")
    src = await _download_image(message)
    if not src:
        return await status.edit_text("❌ Reply to a photo with `/setwelcome`")
    try:
        dest = IOTA / "welcome.png"
        # process to 1280x720 then store as PNG path (JPEG bytes OK for PIL)
        tmp = BG_DIR / "_default_tmp.jpg"
        await asyncio.to_thread(_process_bg, src, tmp)
        # copy processed as welcome.png fallback
        shutil.copy2(tmp, dest)
        tmp.unlink(missing_ok=True)
        total = _refresh_manifest()
        await status.edit_text(
            f"✅ Default **welcome.png** updated.\nPack still has **{total}** random BGs."
        )
    except Exception as e:
        await status.edit_text(f"❌ `{e}`")
    finally:
        try:
            if src:
                src.unlink(missing_ok=True)
        except Exception:
            pass


async def _set_brand(message: Message, dest: Path, size: tuple[int, int] | None, label: str):
    status = await message.reply_text(f"⬇️ sᴇᴛᴛɪɴɢ {label}…")
    src = await _download_image(message)
    if not src:
        return await status.edit_text(f"❌ Reply to a photo with command for **{label}**")
    try:
        await asyncio.to_thread(_process_exact, src, dest, size)
        await status.edit_text(
            f"✅ **{label}** saved → `{dest}`\n{dest.stat().st_size // 1024} KB"
        )
    except Exception as e:
        await status.edit_text(f"❌ `{e}`")
    finally:
        try:
            src.unlink(missing_ok=True)
        except Exception:
            pass


@app.on_message(filters.command(["setupic"], prefixes=["/", "!"]) & filters.user(OWNER_ID))
async def set_upic(_, message: Message):
    await _set_brand(message, ASSETS / "upic.png", (500, 500), "upic.png")


@app.on_message(filters.command(["settiny"], prefixes=["/", "!"]) & filters.user(OWNER_ID))
async def set_tiny(_, message: Message):
    await _set_brand(message, ASSETS / "tiny.png", (500, 200), "tiny.png")


@app.on_message(filters.command(["setcouple"], prefixes=["/", "!"]) & filters.user(OWNER_ID))
async def set_couple(_, message: Message):
    await _set_brand(message, IOTA / "couple.png", (2288, 1496), "couple.png")


@app.on_message(
    filters.command(["listwelcome", "welcomelist", "listbg"], prefixes=["/", "!"])
    & filters.user(OWNER_ID)
)
async def list_welcome(_, message: Message):
    files = _list_bgs()
    if not files:
        return await message.reply_text("📭 Welcome pack empty.")
    page = 1
    if len(message.command) > 1 and message.command[1].isdigit():
        page = max(1, int(message.command[1]))
    per = 20
    start = (page - 1) * per
    chunk = files[start : start + per]
    if not chunk:
        return await message.reply_text(f"No items on page {page}.")
    lines = [f"**Welcome pack** — {len(files)} images · page {page}\n"]
    for p in chunk:
        lines.append(f"• `{p.name}` — {p.stat().st_size // 1024} KB")
    lines.append(f"\n`/delwelcome bg_001` or `/delwelcome 1` to remove")
    lines.append(f"`/listwelcome {page + 1}` next page")
    await message.reply_text("\n".join(lines))


@app.on_message(
    filters.command(["welcomepack", "bgpack"], prefixes=["/", "!"]) & filters.user(OWNER_ID)
)
async def welcome_pack_stats(_, message: Message):
    files = _list_bgs()
    total = sum(p.stat().st_size for p in files)
    custom = list(CUSTOM_DIR.glob("*")) if CUSTOM_DIR.is_dir() else []
    await message.reply_text(
        f"**🖼 Welcome pack**\n"
        f"• Images: **{len(files)}**\n"
        f"• Disk: **{total // 1024} KB**\n"
        f"• Canvas: {TARGET_BG[0]}x{TARGET_BG[1]}\n"
        f"• Owner originals: {len(custom)}\n"
        f"• Folder: `{BG_DIR}`\n\n"
        f"**Owner cmds**\n"
        f"`/addwelcome` reply photo — add to pack\n"
        f"`/setwelcome` — default fallback\n"
        f"`/listwelcome` `/delwelcome N`\n"
        f"`/setupic` `/settiny` `/setcouple`\n"
        f"`/welcome preview` — test card (group/admin)"
    )


@app.on_message(
    filters.command(["delwelcome", "delbg", "rmwelcome"], prefixes=["/", "!"])
    & filters.user(OWNER_ID)
)
async def del_welcome(_, message: Message):
    if len(message.command) < 2:
        return await message.reply_text("Usage: `/delwelcome 12` or `/delwelcome bg_012.jpg`")
    arg = message.command[1].strip()
    target = None
    if arg.isdigit():
        for ext in (".jpg", ".png", ".jpeg", ".webp"):
            p = BG_DIR / f"bg_{int(arg):03d}{ext}"
            if p.exists():
                target = p
                break
    else:
        name = arg if "." in arg else f"{arg}.jpg"
        p = BG_DIR / name
        if p.exists():
            target = p
        else:
            # fuzzy
            matches = list(BG_DIR.glob(f"*{arg}*"))
            if len(matches) == 1:
                target = matches[0]
    if not target or not target.exists():
        return await message.reply_text("❌ File not found in pack.")
    name = target.name
    target.unlink()
    total = _refresh_manifest()
    await message.reply_text(f"🗑 Deleted `{name}`\nPack now: **{total}** images")


@app.on_message(
    filters.command(["clearwelcome"], prefixes=["/", "!"]) & filters.user(OWNER_ID)
)
async def clear_custom_welcome(_, message: Message):
    """Delete only owner custom originals; optional full wipe with 'all'."""
    mode = message.command[1].lower() if len(message.command) > 1 else "custom"
    if mode == "all":
        n = 0
        for p in _list_bgs():
            p.unlink(missing_ok=True)
            n += 1
        _refresh_manifest()
        return await message.reply_text(f"⚠️ Cleared **entire** pack ({n} files).")
    n = 0
    if CUSTOM_DIR.is_dir():
        for p in CUSTOM_DIR.glob("*"):
            if p.is_file():
                p.unlink(missing_ok=True)
                n += 1
    await message.reply_text(
        f"Cleared **{n}** owner originals in `custom/`.\n"
        f"Pack files kept. Use `/clearwelcome all` to wipe pack."
    )


@app.on_message(
    filters.command(["getwelcome", "sendbg"], prefixes=["/", "!"]) & filters.user(OWNER_ID)
)
async def get_welcome_file(_, message: Message):
    """Send a pack image back to owner for inspection."""
    files = _list_bgs()
    if not files:
        return await message.reply_text("Pack empty.")
    if len(message.command) < 2:
        # send random
        import random

        p = random.choice(files)
    else:
        arg = message.command[1]
        p = None
        if arg.isdigit():
            for ext in (".jpg", ".png"):
                cand = BG_DIR / f"bg_{int(arg):03d}{ext}"
                if cand.exists():
                    p = cand
                    break
        else:
            cand = BG_DIR / arg
            p = cand if cand.exists() else None
        if not p:
            return await message.reply_text("Not found.")
    await message.reply_document(str(p), caption=f"`{p.name}` · {p.stat().st_size // 1024} KB")
