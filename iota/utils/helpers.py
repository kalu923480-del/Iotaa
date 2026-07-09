import re, time, asyncio
from telegram import Update, ChatAdministratorRights
from telegram.ext import ContextTypes

def ts(): return int(time.time())

def mention(u) -> str:
    name = (u.full_name or u.first_name or "User").strip()
    return f'<a href="tg://user?id={u.id}">{name}</a>'

def mention_id(uid, name) -> str:
    return f'<a href="tg://user?id={uid}">{name}</a>'

def mention_owner() -> str:
    """Clickable owner name (e.g. 'ɴᴏᴛʜɪɴɢ') that opens the owner's profile."""
    from config import OWNER_ID, OWNER_NAME
    return f'<a href="tg://user?id={OWNER_ID}">{OWNER_NAME}</a>'

def fmt(n: int) -> str:
    if n >= 1_00_00_000: return f"${n/1_00_00_000:.1f}Cr"
    if n >= 1_00_000:    return f"${n/1_00_000:.1f}L"
    if n >= 1_000:       return f"${n/1_000:.1f}k"
    return f"${n}"

def parse_duration(s: str) -> int:
    if not s: return 0
    m = re.match(r'^(\d+)([smhd])$', s.lower())
    if not m: return 0
    v, u = int(m.group(1)), m.group(2)
    return v * {"s":1,"m":60,"h":3600,"d":86400}[u]

def xp_level(xp: int) -> int:
    from config import XP_PER_LEVEL
    lv = 1
    while xp >= lv * XP_PER_LEVEL:
        xp -= lv * XP_PER_LEVEL; lv += 1
    return lv

def rank_title(lv: int) -> str:
    t = {1:"Rookie",2:"Beginner",3:"Fighter",4:"Warrior",5:"Elite",
         6:"Champion",7:"Legend",8:"Master",9:"GrandMaster",10:"Emperor"}
    return t.get(min(lv,10),"Emperor")

def user_icon(u: dict) -> str:
    if u.get("premium_emoji"): return u["premium_emoji"]
    return "💓" if u.get("is_premium") else "👤"

async def get_profile_photo_id(context, uid: int):
    """
    Fetch the file_id of a user's current Telegram profile photo (highest
    resolution available), or None if they have no profile photo set / it
    can't be fetched (privacy settings, deleted account, etc.).

    This is the piece that was completely missing before — commands like
    /pfp and /profile only showed text stats and never actually looked up
    the user's picture from Telegram, so no PFP was ever displayed.
    """
    try:
        photos = await context.bot.get_user_profile_photos(uid, limit=1)
        if photos and photos.total_count > 0 and photos.photos:
            # photos.photos[0] is a list of PhotoSize objects (small→large);
            # take the largest for best quality.
            return photos.photos[0][-1].file_id
    except Exception:
        return None
    return None


async def send_profile_photo_or_text(msg, context, uid: int, caption: str, reply_markup=None):
    """
    Send `caption` (HTML) together with the target user's real Telegram
    profile picture when available. If the user has no profile photo, or
    fetching/sending it fails for any reason (privacy, deleted account,
    network hiccup), this cleanly falls back to a text-only message so
    the command never errors out or shows a broken/placeholder image.
    """
    file_id = await get_profile_photo_id(context, uid)
    if file_id:
        try:
            return await msg.reply_photo(
                photo=file_id, caption=caption,
                parse_mode="HTML", reply_markup=reply_markup
            )
        except Exception:
            pass  # fall through to text-only
    return await msg.reply_html(caption, reply_markup=reply_markup)


async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, uid=None) -> bool:
    if uid is None: uid = update.effective_user.id
    # The bot owner can always use admin-only commands, in any group,
    # even if they're not formally an admin there — matches the behaviour
    # of @admin_only in utils/permissions.py so the two systems never
    # disagree about who counts as privileged.
    from config import OWNER_ID
    if uid == OWNER_ID:
        return True
    try:
        m = await context.bot.get_chat_member(update.effective_chat.id, uid)
        return m.status in ("administrator","creator")
    except Exception:
        return False

async def resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE, args: list):
    """
    🔴 FIX: same bug as handlers/admin.py's _resolve — a bare @username
    (not a reply) only tried bot.get_chat("@username"), which fails
    often even for real, known users, silently returning (None, None).
    Now falls back to our own MongoDB user records before giving up.
    """
    msg = update.effective_message
    if msg.reply_to_message and msg.reply_to_message.from_user:
        u = msg.reply_to_message.from_user
        return u.id, mention(u), args
    if args:
        t = args[0].lstrip("@"); rest = args[1:]
        if t.isdigit():
            uid = int(t)
            try:
                m = await context.bot.get_chat_member(update.effective_chat.id, uid)
                return uid, mention(m.user), rest
            except Exception:
                return uid, f"User {uid}", rest
        else:
            try:
                ch = await context.bot.get_chat(f"@{t}")
                return ch.id, mention_id(ch.id, ch.first_name), rest
            except Exception:
                pass
            try:
                from utils.mongo_db import get_user_by_username
                u = await get_user_by_username(t)
                if u:
                    uid = u["_id"]
                    name = u.get("full_name") or u.get("username") or f"User {uid}"
                    return uid, mention_id(uid, name), rest
            except Exception:
                pass
            return None, None, rest
    return None, None, args

async def delete_later(bot, cid, mid, delay=300):
    await asyncio.sleep(delay)
    try: await bot.delete_message(cid, mid)
    except Exception: pass


async def promote_with_rights(bot, chat_id, user_id, rights: ChatAdministratorRights):
    """
    Apply a ChatAdministratorRights object via promote_chat_member.

    Works regardless of python-telegram-bot version: some builds accept a
    single `rights=` keyword, while others (e.g. the installed 21.3) only
    accept the individual boolean flags — passing `rights=` there raises
    "unexpected keyword argument 'rights'". This helper always uses the
    individual-flag form so promotion/demotion works everywhere.
    """
    try:
        await bot.promote_chat_member(
            chat_id, user_id,
            is_anonymous=rights.is_anonymous,
            can_manage_chat=rights.can_manage_chat,
            can_delete_messages=rights.can_delete_messages,
            can_manage_video_chats=rights.can_manage_video_chats,
            can_restrict_members=rights.can_restrict_members,
            can_promote_members=rights.can_promote_members,
            can_change_info=rights.can_change_info,
            can_invite_users=rights.can_invite_users,
            can_post_messages=rights.can_post_messages,
            can_edit_messages=rights.can_edit_messages,
            can_pin_messages=rights.can_pin_messages,
            can_post_stories=rights.can_post_stories,
            can_edit_stories=rights.can_edit_stories,
            can_delete_stories=rights.can_delete_stories,
            can_manage_topics=getattr(rights, "can_manage_topics", None),
        )
    except Exception:
        # Some bot accounts / chat types reject the can_manage_topics
        # parameter (or any single param) with a 400. Retry once without
        # it so promotion/demotion still works everywhere.
        await bot.promote_chat_member(
            chat_id, user_id,
            is_anonymous=rights.is_anonymous,
            can_manage_chat=rights.can_manage_chat,
            can_delete_messages=rights.can_delete_messages,
            can_manage_video_chats=rights.can_manage_video_chats,
            can_restrict_members=rights.can_restrict_members,
            can_promote_members=rights.can_promote_members,
            can_change_info=rights.can_change_info,
            can_invite_users=rights.can_invite_users,
            can_post_messages=rights.can_post_messages,
            can_edit_messages=rights.can_edit_messages,
            can_pin_messages=rights.can_pin_messages,
            can_post_stories=rights.can_post_stories,
            can_edit_stories=rights.can_edit_stories,
            can_delete_stories=rights.can_delete_stories,
        )
