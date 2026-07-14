"""
Iota — /commands : the master command catalog.

Behaviour:
  • In a GROUP: tells the user to use it in DM and shows a clickable
    "Open in DM" button (deep-linked to /start commands).
  • In DM: shows a category menu (buttons) + a button to download the
    FULL catalog (all 400+ commands with use cases) as a text file.
    Clicking a category sends that category's commands (chunked).
"""
import io
import logging
from collections import OrderedDict

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from utils.command_catalog import all_categories, CATALOG, get_usecase, total_documented
from utils.dm_redirect import require_dm
from utils.safe_html import safe_html
from handlers.owner_panel import _is_privileged

logger = logging.getLogger(__name__)

_CATS_PER_PAGE = 6
_CMDS_PER_MSG = 25

# Categories that expose owner-only / sudo commands. These must NOT be shown
# to ordinary users in the public /commands catalog (they may still see the
# category name is simply hidden; the commands remain execution-gated anyway).
OWNER_ONLY_CATS = {"Owner", "Owner Systems"}


def _visible_categories(privileged: bool):
    cats = all_categories()
    if privileged:
        return cats
    return OrderedDict(
        (c, cmds) for c, cmds in cats.items() if c not in OWNER_ONLY_CATS
    )


def _menu_kb(page: int = 0, privileged: bool = False):
    cats = list(_visible_categories(privileged).items())
    start = page * _CATS_PER_PAGE
    chunk = cats[start:start + _CATS_PER_PAGE]
    rows = []
    for cat, cmds in chunk:
        rows.append([InlineKeyboardButton(
            f"📂 {cat} ({len(cmds)})",
            callback_data=f"cmds_cat_{_slug(cat)}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("« Prev", callback_data=f"cmds_page_{page-1}"))
    if start + _CATS_PER_PAGE < len(cats):
        nav.append(InlineKeyboardButton("Next »", callback_data=f"cmds_page_{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("📄 Download Full List",
                                      callback_data="cmds_download")])
    return InlineKeyboardMarkup(rows)


def _slug(cat: str) -> str:
    return cat.lower().replace(" ", "_").replace("/", "_")


_CAT_BY_SLUG = {_slug(c): c for c in all_categories()}


async def commands_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Groups: redirect to DM with a clickable button.
    if not await require_dm(update, context, "/commands", "commands"):
        return
    uid = update.effective_user.id
    privileged = await _is_privileged(uid)
    total = total_documented()
    text = (
        f"📜 <b>Iota — Full Command Catalog</b>\n\n"
        f"Total commands documented: <b>{total}+</b>\n\n"
        f"👇 Ek category chunein niche se, ya 📄 button se poori list "
        f"(saare commands + use cases) download karein.\n\n"
        f"Har command ka use-case bhi likha hua hai 👇"
    )
    await update.effective_message.reply_html(text, reply_markup=_menu_kb(0, privileged))


async def _send_category(update, cat: str, privileged: bool = False):
    msg = _resolve_message(update)
    if msg is None:
        return
    # Hide owner-only categories from non-privileged users.
    if cat in OWNER_ONLY_CATS and not privileged:
        await msg.reply_html("🔒 <b>Ye category sirf Owner ke liye hai.</b>")
        return
    cats = _visible_categories(privileged)
    cmds = cats.get(cat, [])
    if not cmds:
        await msg.reply_html(f"📂 {safe_html(cat)} — koi command nahi.")
        return
    header = f"📂 <b>{safe_html(cat)}</b> — {len(cmds)} commands\n\n"
    lines = []
    for cmd, uc in cmds:
        lines.append(f"/{cmd} — {safe_html(uc)}")
    # chunk into messages of _CMDS_PER_MSG lines
    chunks = [lines[i:i + _CMDS_PER_MSG] for i in range(0, len(lines), _CMDS_PER_MSG)]
    first = True
    for ch in chunks:
        body = (header if first else "") + "\n".join(ch)
        first = False
        await msg.reply_html(body)


def _resolve_message(target):
    msg = getattr(target, "effective_message", None)
    if msg is None:
        msg = getattr(target, "message", None)
    return msg


async def _send_full_file(target, privileged: bool = False):
    lines = ["IOTA BOT — FULL COMMAND CATALOG", "=" * 40, ""]
    for cat, cmds in _visible_categories(privileged).items():
        lines.append(f"\n## {cat} ({len(cmds)})\n")
        for cmd, uc in cmds:
            lines.append(f"/{cmd}  —  {uc}")
    data = ("\n".join(lines)).encode("utf-8")
    bio = io.BytesIO(data)
    bio.name = "iota_commands.txt"
    msg = _resolve_message(target)
    if msg is None:
        return
    await msg.reply_document(
        document=bio, filename="iota_commands.txt",
        caption=f"📄 Iota full command list — {total_documented()}+ commands with use cases."
    )


async def commands_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    uid = q.from_user.id
    privileged = await _is_privileged(uid)
    try:
        await q.answer()
    except Exception:
        pass
    if data.startswith("cmds_page_"):
        page = int(data.split("_")[-1])
        try:
            await q.edit_message_text(
                f"📜 <b>Iota — Command Catalog</b>\n\n"
                f"Total commands: <b>{total_documented()}+</b>\n\n"
                f"Ek category chunein 👇",
                parse_mode="HTML", reply_markup=_menu_kb(page, privileged))
        except Exception:
            pass
    elif data.startswith("cmds_cat_"):
        slug = data[len("cmds_cat_"):]
        cat = _CAT_BY_SLUG.get(slug)
        if cat:
            await _send_category(q, cat, privileged)
        else:
            await q.answer("Category not found", show_alert=True)
    elif data == "cmds_download":
        await _send_full_file(q, privileged)
