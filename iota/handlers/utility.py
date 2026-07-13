"""Iota Utility - tr, voice, id, admins, own, detail (with history), last_seen, promoter"""
import aiohttp, time
from telegram import Update
from telegram.ext import ContextTypes
from utils.mongo_db import (ensure_user, get_user, get_user_rank,
                             set_top_group, get_top_groups,
                             get_sticker_pack, set_sticker_pack,
                             get_last_seen, update_last_seen,
                             add_promoter, get_promoter, get_promoter_by_code,
                             add_referral)
from utils.helpers import mention, mention_id, fmt, ts
from utils.fonts import sc
from utils.safe_html import safe_html
from utils.sarvam import translate
from utils.tts_engine import (text_to_speech, is_valid_voice,
                             get_tts_config, voice_display, get_last_tts_error,
                             send_tts_voice)
from config import OWNER_ID, OWNER_USERNAME

LANG_MAP = {
    "hi":"hi-IN","en":"en-IN","bn":"bn-IN","te":"te-IN",
    "mr":"mr-IN","ta":"ta-IN","gu":"gu-IN","kn":"kn-IN",
    "ml":"ml-IN","pa":"pa-IN","or":"or-IN","ur":"ur-IN",
}
# A sensible default voice per language (Bulbul v3 recommended picks from the
# Sarvam docs). If the owner set a global default via /ttssettings it is used
# instead unless the user overrides with an explicit voice id.
LANG_DEFAULT_VOICE = {
    "hi-IN":"shubh","en-IN":"ratan","bn-IN":"rehan","te-IN":"shubh",
    "mr-IN":"ratan","ta-IN":"ratan","gu-IN":"ratan","kn-IN":"shubh",
    "ml-IN":"shubh","pa-IN":"mani","od-IN":"shubh","ur-IN":"shubh",
}

# ── /tr ───────────────────────────────────────────────────────────────────────

async def translate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; args = context.args or []
    if msg.reply_to_message and msg.reply_to_message.text:
        text = msg.reply_to_message.text
        lang_key = args[0].lower() if args else "en"
    elif args and len(args) >= 2:
        lang_key = args[0].lower(); text = " ".join(args[1:])
    else:
        await msg.reply_html(
            f"🥀 {sc('Usage')}: /tr <{sc('Language Code')}> <{sc('Reply/Text')}>\n\n"
            f"{sc('Codes')}: hi en bn te mr ta gu kn ml pa\n"
            f"{sc('Example')}: /tr hi Hello how are you?"
        ); return
    target_lang = LANG_MAP.get(lang_key, "en-IN")
    thinking = await msg.reply_html(f"🌐 {sc('Translating')}...")
    try:
        result = await translate(text, target_lang)
        await thinking.edit_text(
            f"🌐 <b>{sc('Translation')} ({lang_key})</b>\n\n"
            f"<b>{sc('Original')}:</b> {text}\n"
            f"<b>{sc('Result')}:</b> {result}",
            parse_mode="HTML"
        )
    except Exception as e:
        await thinking.edit_text(f"❌ {sc('Translation failed!')}")

# ── /voice ────────────────────────────────────────────────────────────────────

async def voice_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; args = context.args or []
    if not args and not (msg.reply_to_message and msg.reply_to_message.text):
        await msg.reply_html(
            f"🎙️ {sc('Usage')}: /voice <{sc('text')}>\n"
            f"{sc('Or')}: /voice hi <{sc('Hindi text')}>\n"
            f"{sc('Or')}: /voice <{sc('voice')}> <{sc('text')}>  (e.g. /voice priya hello)\n"
            f"{sc('Langs')}: hi en bn te mr ta gu kn ml pa\n"
            f"{sc('Browse voices')}: /ttsvoices"
        ); return
    if msg.reply_to_message and msg.reply_to_message.text and not args:
        text = msg.reply_to_message.text; lang_key = "en"; speaker = None
    elif args and args[0].lower() in LANG_MAP and len(args) > 1:
        lang_key = args[0].lower(); text = " ".join(args[1:]); speaker = None
    elif args and is_valid_voice(args[0]):
        # Explicit voice override: /voice priya hello world
        speaker = args[0].lower(); text = " ".join(args[1:]); lang_key = "en"
    else:
        lang_key = "en"; text = " ".join(args); speaker = None
    if not text.strip(): await msg.reply_html("❌ No text!"); return
    lang_code = LANG_MAP.get(lang_key, "en-IN")
    if speaker is None:
        # Use the owner-configured default unless we have a per-language pick.
        cfg = get_tts_config()
        speaker = LANG_DEFAULT_VOICE.get(lang_code, cfg["speaker"])
    thinking  = await msg.reply_html(f"🎙️ {sc('Generating voice')}...")
    try:
        audio_bytes = await text_to_speech(text[:2500], lang_code, speaker)
        if audio_bytes:
            await thinking.delete()
            ok, err = await send_tts_voice(
                context.bot, msg.chat_id, audio_bytes,
                caption=f"🔊 {voice_display(speaker)} — {text[:80]}",
            )
            if not ok:
                await msg.reply_html(
                    f"❌ Voice generated but couldn't be sent: {safe_html(err)}"
                )
        else:
            reason = get_last_tts_error()
            msg_txt = (
                f"❌ {sc('TTS failed!')} Sarvam API returned no audio."
            )
            if reason:
                msg_txt += f"\n\n<i>{safe_html(reason)}</i>"
            else:
                msg_txt += " Check the bot's logs for details."
            await thinking.edit_text(msg_txt, parse_mode="HTML")
    except Exception as e:
        try:
            await thinking.edit_text(f"❌ {sc('Voice error')}: {safe_html(e)}", parse_mode="HTML")
        except Exception:
            await thinking.edit_text(f"❌ {sc('Voice generation failed. Please try again.')}")

# ── /id ───────────────────────────────────────────────────────────────────────

async def id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; chat = update.effective_chat
    if msg.reply_to_message and msg.reply_to_message.from_user:
        u = msg.reply_to_message.from_user
        await msg.reply_html(
            f"👤 <b>{sc('User Info')}</b>\n"
            f"{sc('Name')}: {u.full_name}\n"
            f"{sc('Username')}: @{u.username or 'none'}\n"
            f"{sc('ID')}: <code>{u.id}</code>"
        )
    else:
        u = update.effective_user
        await msg.reply_html(
            f"👤 <b>{sc('Your Info')}</b>\n"
            f"{sc('Name')}: {u.full_name}\n"
            f"{sc('Username')}: @{u.username or 'none'}\n"
            f"{sc('ID')}: <code>{u.id}</code>\n\n"
            f"💬 {sc('Chat ID')}: <code>{chat.id}</code>"
        )

# ── /detail (with name/username history Iota style) ────────────────────────────

async def detail_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)

    # Determine target
    if msg.reply_to_message and msg.reply_to_message.from_user:
        tu = msg.reply_to_message.from_user
    elif context.args:
        arg = context.args[0].lstrip("@")
        try:
            if arg.isdigit():
                uid = int(arg)
                m = await context.bot.get_chat_member(update.effective_chat.id, uid)
                tu = m.user
            else:
                try:
                    ch = await context.bot.get_chat(f"@{arg}")
                    # Fake user object from chat
                    class _FU:
                        def __init__(s):
                            s.id=ch.id; s.full_name=getattr(ch,'first_name','?')
                            s.username=getattr(ch,'username',None); s.is_bot=False
                    tu = _FU()
                except Exception:
                    # 🔴 FIX: get_chat("@username") fails often even for
                    # real, known users (uncached username, privacy
                    # settings, API hiccups). Fall back to our own DB
                    # before giving up — same fix as handlers/admin.py.
                    from utils.mongo_db import get_user_by_username
                    du = await get_user_by_username(arg)
                    if not du:
                        raise
                    class _FU:
                        def __init__(s):
                            s.id=du["_id"]; s.full_name=du.get("full_name") or "?"
                            s.username=du.get("username"); s.is_bot=False
                    tu = _FU()
        except Exception:
            await msg.reply_html(f"⚠️ {sc('Usage')}: /detail <{sc('reply/id')}>"); return
    else:
        tu = u

    await ensure_user(tu.id, getattr(tu,'username','') or "", tu.full_name)
    d = await get_user(tu.id)
    from utils.helpers import xp_level, rank_title, fmt

    # Name & username history (Iota style). Now that ensure_user() properly
    # seeds and accumulates history (see utils/mongo_db.py), this correctly
    # shows every past name/username, not just the current one.
    name_hist = list(d.get("name_history", []))
    user_hist = list(d.get("username_history", []))

    # Always show the CURRENT name/username first, even if it hasn't
    # changed since history was last recorded (matches how the reference
    # bot always lists the live value at the top of the list).
    current_name = d.get("full_name") or tu.full_name
    current_user = d.get("username") or (getattr(tu, "username", None) or "")
    if current_name and current_name not in name_hist:
        name_hist = [current_name] + name_hist
    if current_user and current_user not in user_hist:
        user_hist = [current_user] + user_hist

    lv = xp_level(d.get("xp",0))
    rank = await get_user_rank(tu.id)

    hist_section = f"\n{mention(tu)}'s History\n" + '_'*24 + "\n\n"
    if name_hist:
        hist_section += f"{sc('Full Names')}:\n"
        for n in name_hist[:10]: hist_section += f"• {n}\n"
    else:
        hist_section += f"{sc('Full Names')}:\n• {tu.full_name}\n"
    hist_section += "\n-----------\n\n"
    if user_hist:
        hist_section += f"{sc('Usernames')}:\n"
        for un in user_hist[:10]: hist_section += f"• @{un}\n"
    else:
        hist_section += f"{sc('Usernames')}:\n• @{getattr(tu,'username',None) or 'None'}\n"

    await msg.reply_html(
        hist_section + "\n"
        f"🆔 {sc('ID')}: <code>{tu.id}</code>\n"
        f"💓 {sc('Premium')}: {'Yes' if d.get('is_premium') else 'No'}\n"
        f"⚡ XP: {d.get('xp',0)}  |  {sc('Level')}: {lv}\n"
        f"💀 {sc('Kills')}: {d.get('kills',0)}  |  🔫 {sc('Robs')}: {d.get('robs',0)}\n"
        f"💰 {sc('Balance')}: {fmt(d.get('balance',0))}\n"
        f"🌍 {sc('Rank')}: #{rank}"
    )

# ── /last_seen ─────────────────────────────────────────────────────────────────

async def last_seen_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; now = ts()

    # Accept user_id as argument or reply
    uid = None
    if msg.reply_to_message and msg.reply_to_message.from_user:
        uid = msg.reply_to_message.from_user.id
    elif context.args:
        try: uid = int(context.args[0])
        except Exception: await msg.reply_html("❌ Usage: /last_seen &lt;user_id&gt;"); return
    else:
        await msg.reply_html(
            f"👀 {sc('Usage')}: /last_seen &lt;user_id&gt;\n"
            f"{sc('Or reply to a user message')}"
        ); return

    ls = await get_last_seen(uid)
    if not ls:
        await msg.reply_html(
            f"👀 {sc('Last Active Before')}: {sc('Never seen by Iota Bot')} ⚡"
        ); return

    last = ls.get("last_seen", 0)
    diff = now - last
    d = diff // 86400; h = (diff%86400)//3600; m = (diff%3600)//60

    name = ls.get("full_name","User")
    await msg.reply_html(
        f"👀 {sc('Last Active Before')}: {d}D:{h:02d}H:{m:02d}M ⚡"
    )

# ── /owner ────────────────────────────────────────────────────────────────────

async def owner_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_html(f"🚫 {sc('Group Only!')}"); return
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
        creator = next((a for a in admins if a.status=="creator"), None)
        if creator:
            await update.message.reply_html(
                f"👑 {sc('Group Owner')}:\n╰ 𓃠 {mention(creator.user)}"
            )
        else:
            await update.message.reply_html(f"❓ {sc('Owner not found!')}")
    except Exception as e:
        await update.message.reply_html(f"❌ {e}")

# ── /admins ───────────────────────────────────────────────────────────────────

async def admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_html(f"🚫 {sc('Group Only!')}"); return
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
        creator = next((a for a in admins if a.status=="creator"), None)
        others  = [a for a in admins if a.status!="creator"]

        text = f"👑 {sc('Group Owner')}: "
        if creator:
            text += mention(creator.user) + "\n\n"
        text += f"\n👥 {sc('Total Admins')}: {len(admins)}"

        if others:
            text += "\n\n" + "\n".join(
                f"🛡️ {mention(a.user)}" + (f" <i>({a.custom_title})</i>" if getattr(a,"custom_title",None) else "")
                for a in others[:20]
            )
        await update.message.reply_html(text)
    except Exception as e:
        await update.message.reply_html(f"❌ {e}")

# ── /own (sticker pack) ───────────────────────────────────────────────────────

async def own_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    if msg.reply_to_message and msg.reply_to_message.sticker:
        pack = await get_sticker_pack(u.id)
        if pack:
            await msg.reply_html(
                f"📦 {sc('Your Pack')}: <b>{pack['pack_title']}</b>\n"
                f"t.me/addstickers/{pack['pack_name']}"
            )
        else:
            await msg.reply_html(
                f"⚠️ {sc('Reply To A Sticker To Add In Your Pack.')}\n\n"
                f"{sc('Setup')}: /own set &lt;pack_name&gt;"
            )
        return
    if context.args and context.args[0].lower()=="set" and len(context.args)>=2:
        pack_name = context.args[1]
        await set_sticker_pack(u.id, pack_name, f"{u.first_name}'s Pack")
        await msg.reply_html(f"✅ Pack set!\nt.me/addstickers/{pack_name}"); return
    await msg.reply_html(
        f"⚠️ {sc('Reply To A Sticker To Add In Your Pack.')}\n\n"
        f"{sc('Or')}: /own set &lt;pack_name&gt;"
    )

# ── /setgroup & /topgroups ────────────────────────────────────────────────────

async def setgroup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    rank = await get_user_rank(u.id)
    if rank > 5:
        await update.message.reply_html(
            f"❌ {sc('Only Top 5 Richest Can Set A Group!')}\n"
            f"{sc('Your Rank')}: #{rank}\n/toprich"
        ); return
    args = context.args
    if not args or len(args)<2:
        await update.message.reply_html("Usage: /setgroup &lt;name&gt; &lt;link&gt;"); return
    await set_top_group(min(rank,5), u.id, args[0], args[1])
    await update.message.reply_html(f"✅ Group set at rank #{min(rank,5)}!")

async def topgroups_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await get_top_groups()
    if not rows: await update.message.reply_html(f"📋 {sc('No top groups set!')}"); return
    medals = ["🥇","🥈","🥉","4️⃣","5️⃣"]
    text = f"🌍 <b>{sc('Top Groups')}</b>\n\n"
    for i, r in enumerate(rows[:5]):
        text += f"{medals[i]} <a href='{r['group_link']}'>{r['group_name']}</a>\n"
    await update.message.reply_html(text, disable_web_page_preview=True)

# ── /promoter system ──────────────────────────────────────────────────────────

import uuid

async def promoter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    p = await get_promoter(u.id)
    if not p:
        # Create new promoter profile
        code = f"iota_{u.id}_{str(uuid.uuid4())[:4]}"
        await add_promoter(u.id, code)
        p = await get_promoter(u.id)
    me = await context.bot.get_me()
    ref_link = f"https://t.me/{me.username}?start=ref_{p['ref_code']}"
    referred = len(p.get("referred", []))
    earnings = p.get("earnings", 0)
    await update.message.reply_html(
        f"🤝 <b>{sc('Your Promoter Profile')}</b>\n\n"
        f"🔗 {sc('Referral Link')}:\n{ref_link}\n\n"
        f"📊 {sc('Stats')}:\n"
        f"👥 {sc('Referred')}: <b>{referred}</b>\n"
        f"💰 {sc('Earnings')}: <b>{fmt(earnings)}</b>\n\n"
        f"💰 {sc('Per Referral')}: +500 coins\n"
        f"💓 {sc('Premium Referral')}: +2000 coins\n\n"
        f"{sc('Share your link and earn when someone starts the bot!')}"
    )

async def ref_stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    p = await get_promoter(u.id)
    if not p:
        await update.message.reply_html(
            f"❌ {sc('No promoter profile!')}\n/promoter to create one"
        ); return
    referred = len(p.get("referred",[]))
    earnings = p.get("earnings",0)
    await update.message.reply_html(
        f"📊 <b>{sc('Referral Stats')} — {mention(u)}</b>\n\n"
        f"👥 {sc('Total Referred')}: <b>{referred}</b>\n"
        f"💰 {sc('Total Earned')}: <b>{fmt(earnings)}</b>\n"
        f"🔑 {sc('Code')}: <code>{p['ref_code']}</code>"
    )

async def handle_referral(uid: int, ref_code: str, bot):
    """Called when someone starts with ?start=ref_<code>"""
    promoter = await get_promoter_by_code(ref_code)
    if not promoter or promoter["_id"] == uid:
        return
    already = uid in promoter.get("referred", [])
    if already: return
    # Check if referred user is premium
    d = await get_user(uid)
    reward = 2000 if d.get("is_premium") else 500
    await add_referral(promoter["_id"], uid, reward)
    from utils.mongo_db import add_balance
    await add_balance(promoter["_id"], reward)
    try:
        await bot.send_message(
            promoter["_id"],
            f"🎉 Someone joined via your referral link!\n💰 +{fmt(reward)} coins!",
            parse_mode="HTML"
        )
    except Exception: pass
