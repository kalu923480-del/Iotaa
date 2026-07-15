# Authored By Iota Coders © 2025
import httpx
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode
from IotaXMedia import app

commands = {
    "punch": {"emoji": "💥", "text": "punched"},
    "slap": {"emoji": "😒", "text": "slapped"},
    "hug": {"emoji": "🤗", "text": "hugged"},
    "bite": {"emoji": "😈", "text": "bit"},
    "kiss": {"emoji": "😘", "text": "kissed"},
    "highfive": {"emoji": "🙌", "text": "high-fived"},
    "shoot": {"emoji": "🔫", "text": "shot"},
    "dance": {"emoji": "💃", "text": "danced"},
    "happy": {"emoji": "😊", "text": "was happy"},
    "baka": {"emoji": "😡", "text": "called you a baka"},
    "pat": {"emoji": "👋", "text": "patted"},
    "nod": {"emoji": "👍", "text": "nodded"},
    "nope": {"emoji": "👎", "text": "said nope"},
    "cuddle": {"emoji": "🤗", "text": "cuddled"},
    "feed": {"emoji": "🍴", "text": "fed"},
    "bored": {"emoji": "😴", "text": "was bored"},
    "nom": {"emoji": "😋", "text": "nommed"},
    "yawn": {"emoji": "😪", "text": "yawned"},
    "facepalm": {"emoji": "🤦", "text": "facepalmed"},
    "tickle": {"emoji": "😆", "text": "tickled"},
    "yeet": {"emoji": "💨", "text": "yeeted"},
    "think": {"emoji": "🤔", "text": "thought"},
    "blush": {"emoji": "😊", "text": "blushed"},
    "smug": {"emoji": "😏", "text": "was smug"},
    "wink": {"emoji": "😉", "text": "winked"},
    "peck": {"emoji": "😘", "text": "pecked"},
    "smile": {"emoji": "😄", "text": "smiled"},
    "wave": {"emoji": "👋", "text": "waved"},
    "poke": {"emoji": "👉", "text": "poked"},
    "stare": {"emoji": "👀", "text": "stared"},
    "shrug": {"emoji": "🤷", "text": "shrugged"},
    "sleep": {"emoji": "😴", "text": "slept"},
    "lurk": {"emoji": "👤", "text": "is lurking"}
}

# nekos.life is permanently offline, so map every action to the closest
# waifu.pics SFW tags (first matching tag wins). Unsupported actions fall
# back to general SFW tags that always resolve.
ACTION_TAGS = {
    "punch": ["bonk", "slap", "yeet"],
    "slap": ["slap"],
    "hug": ["hug"],
    "bite": ["bite"],
    "kiss": ["kiss", "lick"],
    "highfive": ["highfive"],
    "shoot": ["yeet", "bonk", "kick"],
    "dance": ["dance"],
    "happy": ["happy"],
    "baka": ["smug", "cringe"],
    "pat": ["pat"],
    "nod": ["wave", "handhold"],
    "nope": ["cry", "cringe"],
    "cuddle": ["cuddle", "glomp"],
    "feed": ["feed", "nom"],
    "bored": ["cringe", "cry"],
    "nom": ["nom"],
    "yawn": ["cry", "cringe"],
    "facepalm": ["cringe"],
    "tickle": ["glomp", "cuddle"],
    "yeet": ["yeet"],
    "think": ["smug", "wave"],
    "blush": ["blush"],
    "smug": ["smug"],
    "wink": ["wink"],
    "peck": ["kiss", "blush"],
    "smile": ["smile"],
    "wave": ["wave"],
    "poke": ["poke"],
    "stare": ["glomp", "smug"],
    "shrug": ["cry", "cringe"],
    "sleep": ["cry", "cuddle"],
    "lurk": ["awoo", "neko"],
}

GENERAL_TAGS = ["waifu", "neko", "shinobu", "megumin"]

# purrbot.site is reachable from anywhere (incl. restricted networks) and
# serves real reaction GIFs. Every action is mapped to a supported type so
# the command never errors out even when waifu.pics / nekos.best are blocked.
PURRBOT_MAP = {
    "punch": "slap",
    "slap": "slap",
    "hug": "hug",
    "bite": "bite",
    "kiss": "kiss",
    "highfive": "pat",
    "shoot": "slap",
    "dance": "dance",
    "happy": "smile",
    "baka": "cry",
    "pat": "pat",
    "nod": "poke",
    "nope": "poke",
    "cuddle": "cuddle",
    "feed": "feed",
    "bored": "cry",
    "nom": "bite",
    "yawn": "cry",
    "facepalm": "cry",
    "tickle": "tickle",
    "yeet": "slap",
    "think": "blush",
    "blush": "blush",
    "smug": "smile",
    "wink": "smile",
    "peck": "kiss",
    "smile": "smile",
    "wave": "pat",
    "poke": "poke",
    "stare": "blush",
    "shrug": "cry",
    "sleep": "cry",
    "lurk": "poke",
}


def md_escape(text: str) -> str:
    return text.replace('[', '\\[').replace(']', '\\]')


def _dedupe(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


async def _try_waifu(tag: str):
    """Return a GIF/photo URL for a waifu.pics SFW tag, or None on failure."""
    url = f"https://api.waifu.pics/sfw/{tag}"
    try:
        async with httpx.AsyncClient(
            verify=False, timeout=10, follow_redirects=True
        ) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("url")
    except Exception:
        pass
    return None


async def _try_nekosbest(tag: str):
    """Return a GIF URL from nekos.best (modern nekos.life successor), or None."""
    url = f"https://nekos.best/api/v2/sfw/{tag}"
    try:
        async with httpx.AsyncClient(
            verify=False, timeout=10, follow_redirects=True,
            headers={"User-Agent": "IotaMusicBot/1.0"},
        ) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results") or []
                if results and results[0].get("url"):
                    return results[0]["url"]
                if data.get("url"):
                    return data["url"]
    except Exception:
        pass
    return None


async def _try_purrbot(ptype: str):
    """Return a real reaction GIF URL from purrbot.site, or None on failure."""
    url = f"https://api.purrbot.site/v2/img/sfw/{ptype}/gif"
    try:
        async with httpx.AsyncClient(
            verify=False, timeout=10, follow_redirects=True
        ) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if not data.get("error"):
                    return data.get("link")
    except Exception:
        pass
    return None


async def get_animation(action: str):
    """Return a reaction GIF/photo URL for an action.

    Order of sources (best quality first, guaranteed-last):
      1. waifu.pics      – real, accurate GIFs (works on normal servers)
      2. nekos.best      – real GIFs (works on normal servers)
      3. purrbot.site    – real GIFs, reachable from ANY network
    Returns the URL or None only if every source is unreachable.
    """
    tags = ACTION_TAGS.get(action, [])
    ordered = _dedupe(list(tags) + list(GENERAL_TAGS))

    for tag in ordered:
        gif_url = await _try_waifu(tag)
        if gif_url:
            return gif_url
    for tag in ordered:
        gif_url = await _try_nekosbest(tag)
        if gif_url:
            return gif_url

    ptype = PURRBOT_MAP.get(action)
    if ptype:
        gif_url = await _try_purrbot(ptype)
        if gif_url:
            return gif_url
    return None


@app.on_message(filters.command(list(commands.keys())) & ~filters.forwarded & ~filters.via_bot)
async def animation_command(client: Client, message: Message):
    command = message.command[0].lower()

    if command not in commands:
        return await message.reply_text("⚠️ That command is not supported.")

    gif_url = await get_animation(command)
    if not gif_url:
        return await message.reply_text("❌ Couldn't fetch the animation. Please try again later.")

    sender_name = md_escape(message.from_user.first_name)
    sender = f"[{sender_name}](tg://user?id={message.from_user.id})"

    if message.reply_to_message and message.reply_to_message.from_user:
        target_user = message.reply_to_message.from_user
        target_name = md_escape(target_user.first_name)
        target = f"[{target_name}](tg://user?id={target_user.id})"
    else:
        target = sender

    action_text = commands[command]['text']
    emoji = commands[command]['emoji']

    caption = f"**{sender} {action_text} {target}!** {emoji}"

    lower = gif_url.lower()
    if lower.endswith((".gif", ".mp4", ".webm", ".mov")):
        await message.reply_animation(
            animation=gif_url,
            caption=caption,
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await message.reply_photo(
            photo=gif_url,
            caption=caption,
            parse_mode=ParseMode.MARKDOWN
        )
