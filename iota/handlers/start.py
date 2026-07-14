"""Iota Bot - /start with Iota-style text + Menu button"""
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, MenuButtonCommands
from telegram.ext import ContextTypes
from utils.mongo_db import ensure_user, get_user
from utils.helpers import mention, fmt, xp_level, rank_title
from utils.fonts import sc, bold_sc
from utils.safe_html import safe_html
from utils.dm_redirect import require_dm
import logging

logger = logging.getLogger(__name__)

# ── Iota-style start text (unicode smallcaps) ─────────────────────────────────
def _start_text(name_mention: str) -> str:
    return (
        f"💕 ʜɪᴇᴇᴇᴇᴇ {name_mention}\n"
        f"ʏᴏᴜ ᴀʀᴇ ᴛᴀʟᴋɪɴɢ ᴛᴏ ɪᴏᴛᴀ, ᴀ ɢᴀᴍɪɴɢ ᴀɴᴅ ᴄʜᴀᴛᴛɪɴɢ ɢɪʀʟ ʜᴀᴠɪɴɢ ʟᴏᴛs ᴏꜰ ꜰᴇᴀᴛᴜʀᴇs.\n\n"
        f"👇 ᴄʜᴏᴏsᴇ ᴀɴ ᴏᴘᴛɪᴏɴ ʙᴇʟᴏᴡ :"
    )

def _start_kb(bot_username: str) -> InlineKeyboardMarkup:
    from config import UPDATE_CHANNEL_USERNAME
    # 🆕 "Friends" is now the update channel link, per request — a direct
    # URL button straight to the channel instead of a menu screen, since
    # there's nothing to configure once you're there. Falls back to the
    # old menu-callback version if the channel hasn't been set up yet in
    # config.py, so nothing ever points to a broken/missing link.
    if UPDATE_CHANNEL_USERNAME:
        friends_btn = InlineKeyboardButton(
            "📢 ᴜᴘᴅᴀᴛᴇ ᴄʜᴀɴɴᴇʟ", url=f"https://t.me/{UPDATE_CHANNEL_USERNAME}"
        )
    else:
        friends_btn = InlineKeyboardButton("🧸 ꜰʀɪᴇɴᴅs", callback_data="menu_friends")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 ɢʀᴏᴜᴘs",    callback_data="menu_groups"),
         InlineKeyboardButton("🤝 ᴘʀᴏᴍᴏᴛᴇʀ",  callback_data="menu_promoter")],
        [friends_btn,
         InlineKeyboardButton("🎮 ɢᴀᴍᴇs",     callback_data="menu_games")],
        [InlineKeyboardButton("➕ ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ 👥",
                              url=f"https://t.me/{bot_username}?startgroup=start")],
    ])

# A known-good, always-reachable fallback image (Telegram's own CDN-cached
# Unsplash proxy). The previous castle image (i.imgur.com/wJuQEbG.jpeg)
# is unreliable — Imgur links routinely get rate-limited / geo-blocked
# ("Content not viewable in your region"), which is exactly the broken
# image seen in testing. We now try a short list of mirrors and fall
# back to text-only instantly if all of them fail, instead of ever
# showing a broken/undeliverable image to the user.
_CASTLE_IMAGES = [
    "https://images.unsplash.com/photo-1533154683836-84ea7a0bc310?w=1200&q=80",  # castle
    "https://images.unsplash.com/photo-1520637836862-4d197d17c93a?w=1200&q=80",  # fantasy castle
    "https://i.imgur.com/wJuQEbG.jpeg",  # legacy fallback (kept last)
]


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)

    # 🆕 Deep-link routing: t.me/BOT?start=commands|pay|panel opens the
    # matching feature directly once the user reaches the bot's DM.
    if context.args:
        payload = context.args[0].split("_")[0]
        if payload == "commands":
            from handlers.commands_list import commands_cmd
            return await commands_cmd(update, context)
        if payload == "pay":
            from handlers.premium import pay_cmd
            return await pay_cmd(update, context)
        if payload == "panel":
            from handlers.owner_panel import owner_panel_cmd
            return await owner_panel_cmd(update, context)

    me = await context.bot.get_me()

    village_caption = (
        f"🏰 ʜᴇʟʟᴏ {mention(u)}! ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ ɪᴏᴛᴀ!\n\n"
        f"ʙᴜɪʟᴅ ʏᴏᴜʀ ᴏᴡɴ ᴠɪʟʟᴀɢᴇ ᴀɴᴅ ʙᴇᴄᴏᴍᴇ ᴛʜᴇ sᴛʀᴏɴɢᴇsᴛ ᴇᴍᴘᴇʀᴏʀ.\n"
        f"ᴜᴘɢʀᴀᴅᴇ ʙᴜɪʟᴅɪɴɢs ᴛᴏ ᴄᴏʟʟᴇᴄᴛ ᴡᴏᴏᴅ, sᴛᴏɴᴇ & ɪʀᴏɴ ᴇᴠᴇʀʏ ᴅᴀʏ.\n"
        f"ᴛʀᴀɪɴ ᴀʀᴍʏ • ᴀᴛᴛᴀᴄᴋ ᴘʟᴀʏᴇʀs • ᴄʟɪᴍʙ ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ 👑\n\n"
        f"👇 ᴄʜᴏᴏsᴇ ᴀɴ ᴏᴘᴛɪᴏɴ ʙᴇʟᴏᴡ :"
    )

    kb = _start_kb(me.username)

    # Send EXACTLY ONE message: try each image mirror in order; the first
    # one that succeeds becomes the single /start reply. If every image
    # fails to send (network issue, dead link, region block, etc.) we
    # fall back to one clean text-only message — never both.
    for img in _CASTLE_IMAGES:
        try:
            await update.message.reply_photo(
                photo=img, caption=village_caption,
                parse_mode="HTML", reply_markup=kb
            )
            return
        except Exception as e:
            logger.debug(f"/start: image mirror failed ({img}): {e}")
            continue

    # All image mirrors failed — send a single text-only fallback so the
    # user still gets ONE clear welcome message instead of nothing.
    await update.message.reply_html(village_caption, reply_markup=kb)


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    d = q.data
    back = [[InlineKeyboardButton("« ʙᴀᴄᴋ", callback_data="menu_back")]]

    content = {
        "menu_groups": (
            "👥 <b>ɢʀᴏᴜᴘs</b>\n\n"
            "Add Iota to your group and get:\n"
            "• Auto welcome messages\n"
            "• Group economy (/gbal /gkill /grob)\n"
            "• Admin tools (.warn .ban .mute)\n"
            "• Group protection (anti-spam, flood)\n"
            "• Card tournaments & games\n\n"
            "➕ Add Iota → t.me/IotaBot?startgroup=start\n"
            "/claim — Claim group reward ($10k+)"
        ),
        "menu_promoter": (
            "🤝 <b>ᴘʀᴏᴍᴏᴛᴇʀ ꜱʏꜱᴛᴇᴍ</b>\n\n"
            "Earn coins by referring friends!\n\n"
            "💰 Per referral: +500 coins\n"
            "💎 Premium referral: +2000 coins\n\n"
            "Commands:\n"
            "/promoter — Get your referral link\n"
            "/ref_stats — Check your earnings\n"
            "/refer_top — Top promoters"
        ),
        "menu_friends": (
            "🧸 <b>ꜰʀɪᴇɴᴅꜱ & ꜱᴏᴄɪᴀʟ</b>\n\n"
            "/slap /punch /kiss /hug /bite\n"
            "/murder /couples /crush /love /ship\n"
            "/compliment /roast /truth /dare\n"
            "/puzzle /shayari /meme\n\n"
            "🆕 /fall /throw /kick /highfive /poke\n"
            "/tickle /facepalm /pie /trip /freeze\n"
            "/zap /dancewith\n\n"
            "/valentine — Valentine event\n"
            "/last_seen &lt;user_id&gt; — Check last active"
        ),
        "menu_games": (
            "🎮 <b>ɢᴀᴍᴇꜱ</b>\n\n"
            "🃏 /card — Card Game\n"
            "💣 /bomb — Bomb Passing\n"
            "🎭 /bluff — Bluff Game\n"
            "🐺 /werewolf — Social Deduction (5-10 players)\n"
            "💻 /hack — Password Hacking\n"
            "📝 /wordgame — Word Guess\n"
            "🧩 /hangman — Hangman\n"
            "❓ /quiz — AI Quiz\n"
            "⭕ /tictactoe — Tic Tac Toe\n"
            "✊ /rps — Rock Paper Scissors\n"
            "🎲 /ludo — Ludo Game\n"
            "🏆 /leaders — Leaderboard"
        ),
        "menu_economy": (
            "💰 <b>ᴇᴄᴏɴᴏᴍʏ</b>\n\n"
            "/daily — Claim daily coins\n"
            "/bal — Check your balance\n"
            "/rob — Rob another user\n"
            "/kill — Kill another user\n"
            "/revive — Revive yourself/others\n"
            "/protect — Buy protection\n"
            "/give — Give coins to someone\n"
            "/wallet — Deposit/withdraw\n"
            "/toprich — Richest players\n"
            "/shop — Spend your coins\n\n"
            "🏦 <b>ʙᴀɴᴋɪɴɢ</b>\n"
            "/bank — Wallet + bank + savings overview\n"
            "/deposit /withdraw — Move coins to/from bank (rob-proof)\n"
            "/transfer &lt;@user&gt; &lt;amt&gt; — Send coins to a friend\n"
            "/savings deposit|withdraw — Earn 2%/day interest\n"
            "/loan /repay — Borrow & repay coins\n"
            "/networth — Your total wealth\n"
            "/lottery — Buy a ticket, win the jackpot\n\n"
            "🛒 <b>ᴍᴀʀᴋᴇᴛ</b>\n"
            "/bazaar — Buy & sell items for coins"
        ),
        "menu_village": (
            "🏰 <b>ᴠɪʟʟᴀɢᴇ ᴡᴀʀ</b>\n\n"
            "🏘️ /village — Full empire dashboard\n"
            "⛏️ /collect — Harvest resources\n"
            "📦 /storage /vault — Check resources\n"
            "🏗️ /build — Upgrade buildings\n"
            "🧱 /walls /defense — Fortify\n"
            "⚔️ /train /troops — Build your army\n"
            "🔍 /kingdom /spy — Scout targets\n"
            "💥 /attack — Raid other players\n"
            "💱 /settle /convert — Manage currency\n"
            "👑 /emperors — Leaderboard\n"
            "📖 /guide — Full walkthrough\n\n"
            "🆕 /donate — Send resources to a friend\n"
            "/repair — Restore wall/defense HP\n"
            "/raidlog — Your last 5 attack results\n"
            "/recruit — Recruit a hero"
        ),
        "menu_admin": (
            "🛡️ <b>ᴀᴅᴍɪɴ ᴄᴏᴍᴍᴀɴᴅꜱ</b> (. or ! prefix)\n\n"
            "<code>.warn .unwarn .warns</code>\n"
            "<code>.mute .imute .dmute .unmute</code>\n"
            "<code>.ban .dban .unban .kick</code>\n\n"
            "<b>Promote (3 levels):</b>\n"
            "<code>.promote</code> [user] 1/2/3\n"
            "  1=Junior 2=Admin 3=Full\n"
            "<code>.demote .unpromote .demote_all</code>\n"
            "<code>.add .remove .title</code>\n\n"
            "<b>🆕 New:</b>\n"
            "<code>.adminlist</code> — List all admins\n"
            "<code>.report</code> — Ping admins about a message\n"
            "<code>.clearwarn .warnlimit</code>\n"
            "<code>.tmute .tban</code> — Timed mute/ban\n"
            "<code>.note .notes .delnote .clearnotes</code>\n"
            "<code>.pin .unpin .d</code>\n\n"
            "💡 Bot owner can run any of these in ANY group Iota is in, "
            "even without being an admin there — as long as Iota herself "
            "has admin rights."
        ),
        "menu_toys": (
            "🧰 <b>ᴛᴇxᴛ ᴛᴏʏꜱ & ᴛʀɪᴠɪᴀ</b>\n\n"
            "/8ball /joke /fact /riddle /wyr\n"
            "/reverse /mock /binary /morse\n"
            "/hash /password\n\n"
            "🎁 /avatar — See someone's profile photo\n"
            "📌 /pin /unpin /purge — Message tools (admin)\n\n"
            "📝 /nickname /birthday /todo /countdown\n"
            "🎉 /giveaway — Run a group giveaway (admin)"
        ),
        "menu_ai": (
            "🤖 <b>ᴀɪ ᴄʜᴀᴛ</b>\n\n"
            "/ai &lt;message&gt; — Chat with Iota's AI\n"
            "/ask &lt;message&gt; — Same as /ai\n"
            "/clearmemory — Reset your AI chat memory\n\n"
            "🆕 <b>Fresh AI content every time:</b>\n"
            "/aijoke — An original joke, never repeated\n"
            "/advice &lt;topic&gt; — Iota's advice on anything\n"
            "/roastme — A playful AI roast of you\n"
            "/aistory &lt;topic&gt; — A short story on any topic\n\n"
            "💬 In DMs, just message me directly!\n"
            "💬 In groups, @mention me or reply to my messages"
        ),
        "menu_back": None
    }

    if d == "menu_back":
        me = await context.bot.get_me()
        # 🔴 FIX: /start now always sends a PHOTO message (village image +
        # caption) as a single clean message (see handlers/start.py
        # start_cmd). Calling edit_message_text() on a photo message
        # fails with "There is no text in the message to edit" — this is
        # exactly why tapping "« Back" (and the menu buttons below) could
        # silently do nothing. edit_message_caption() is required for
        # messages that carry media; fall back to edit_message_text only
        # if the message really has no photo (defensive, in case /start
        # ever falls back to text-only when all image mirrors fail).
        try:
            if q.message.photo:
                await q.edit_message_caption(
                    caption=_start_text(""), parse_mode="HTML",
                    reply_markup=_start_kb(me.username)
                )
            else:
                await q.edit_message_text(
                    _start_text(""), parse_mode="HTML",
                    reply_markup=_start_kb(me.username)
                )
        except Exception as e:
            logger.debug(f"menu_back edit failed: {e}")
        return

    if d in content and content[d]:
        try:
            if q.message.photo:
                await q.edit_message_caption(
                    caption=content[d], parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(back)
                )
            else:
                await q.edit_message_text(
                    content[d], parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(back)
                )
        except Exception as e:
            logger.debug(f"menu_callback edit failed for {d}: {e}")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 🆕 Long help is shown in DM; in groups, redirect with a clickable button.
    if not await require_dm(update, context, "/help", "commands"):
        return
    u = update.effective_user
    me = await context.bot.get_me()
    await update.message.reply_html(
        f"✨ <b>ʜᴇʟʟᴏ {mention(u)}! ɪ'ᴍ ɪᴏᴛᴀ!</b> ✨\n"
        f"ɪ'ᴍ ʏᴏᴜʀ ᴘᴇʀsᴏɴᴀʟ ᴀɪ ʙᴇsᴛɪᴇ, ᴍᴏᴅᴇʀᴀᴛᴏʀ & ᴇᴄᴏɴᴏᴍʏ ʙᴏᴛ!\n\n"
        "🌟 <b>Most Used:</b>\n"
        "/daily /bal /rob /kill /profile\n"
        "/collect /attack /ai /shop\n\n"
        "🛡️ <b>ᴀᴅᴍɪɴ (. or ! prefix):</b>\n"
        "<code>.warn .unwarn .warns</code>\n"
        "<code>.mute .imute .dmute .unmute</code>\n"
        "<code>.ban .dban .unban .kick</code>\n"
        "<code>.promote 1/2/3 .demote .unpromote</code>\n"
        "<code>.add .remove .title .pin .unpin .d</code>\n"
        "<code>.adminlist .report .note .tmute .tban</code>\n\n"
        "👇 Tap below for all commands:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Economy",  callback_data="menu_economy"),
             InlineKeyboardButton("🎮 Games",    callback_data="menu_games")],
            [InlineKeyboardButton("🏰 Village",  callback_data="menu_village"),
             InlineKeyboardButton("😂 Fun",      callback_data="menu_friends")],
            [InlineKeyboardButton("🤖 AI Chat",  callback_data="menu_ai"),
             InlineKeyboardButton("🛡️ Admin",   callback_data="menu_admin")],
            [InlineKeyboardButton("🧰 Toys",     callback_data="menu_toys"),
             InlineKeyboardButton("🤝 Promoter", callback_data="menu_promoter")],
        ])
    )
