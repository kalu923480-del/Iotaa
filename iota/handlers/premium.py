"""
Iota Premium Handler
- 3 months duration
- 1-day protection: 400 coins (all users) — see handlers/economy.py protect_cmd
- 2-day protection: premium only
- /check shows timing privately (DM forward)

HOW STARS PAYMENTS REACH THE OWNER
────────────────────────────────────
This is a factual note for whoever maintains this bot — nothing below is
shown to users, and there is nothing further to configure here.

Telegram Stars (currency="XTR") sent via send_invoice() ALWAYS accumulate
on the balance of the bot that issued the invoice — not on any per-payment
"recipient" field, because the Bot API has no such field. Telegram
attributes every bot's Stars balance to whichever Telegram account
registered that bot with @BotFather (i.e. the bot's owner). Only that
owner account can later withdraw the accumulated Stars (converted to TON)
via Fragment (https://fragment.com) — this happens completely outside
the bot's code, directly between the owner's Telegram account and
Fragment.

So: as long as config.OWNER_ID's Telegram account is the one that
actually created/owns this bot via BotFather, every Star spent through
/pay or /gems already lands with the owner automatically — there is no
redirection logic to add, and no other Telegram account can intercept or
redirect that balance. log_stars_payment() below simply records each
transaction for the owner's own /starsstats audit trail (owner-only,
see handlers/owner_panel.py) — it does not move money itself.
"""
import time
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice
from telegram.ext import ContextTypes
from utils.mongo_db import ensure_user, get_user, update_user, log_stars_payment
from utils.helpers import mention, fmt, ts
from utils.fonts import sc
from utils.dm_redirect import require_dm
from config import (PREMIUM_PRICE_COINS, PREMIUM_PRICE_STARS, PREMIUM_DURATION_DAYS,
                    GEMS_PRICE_STARS, GEMS_PRICE_COINS, OWNER_ID, PROTECT_1D_COST, PROTECT_2D_COST)

async def pay_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 🆕 Premium purchase is a private/DM-only action (payment, invoices).
    if not await require_dm(update, context, "/pay (Premium purchase)", "pay"):
        return
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)
    status = f"💓 {sc('Premium')}" if d.get("is_premium") else f"⚠️ {sc('Normal')}"

    # Check if already premium — show expiry
    now = ts()
    expiry_line = ""
    if d.get("is_premium") and d.get("premium_until",0) > now:
        rem = d["premium_until"] - now
        expiry_line = f"\n⏳ {sc('Expires in')}: <b>{rem//86400}d {(rem%86400)//3600}h</b>"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Buy with Coins", callback_data="buy_premium_coins")],
        [InlineKeyboardButton("⭐ Buy with Stars (/fpay)", callback_data="buy_premium_stars")],
    ])
    await update.message.reply_html(
        f"💓 <b>{sc('Iota Premium Access')}</b>\n\n"
        f"<b>{sc('Important Note')}</b>: {sc('If you cannot buy premium from coins then you can buy Iota premium using Telegram Stars')}: /fpay\n\n"
        f"💰 {sc('Price')}: <b>{fmt(PREMIUM_PRICE_COINS)}</b> {sc('coins')}\n"
        f"⭐ {sc('Stars')}: <b>{PREMIUM_PRICE_STARS}</b> Telegram Stars\n"
        f"📅 {sc('Duration')}: <b>3 {sc('Months')} (90 {sc('days')})</b>\n\n"
        f"✅ <b>{sc('Benefits')}:</b>\n"
        f"• 2.5x {sc('Daily')} ({fmt(1250)})\n"
        f"• {sc('Rob limit')}: {fmt(100_000)}\n"
        f"• {sc('Tax')}: 5% {sc('only')}\n"
        f"• {sc('Wallet access')}\n"
        f"• {sc('Custom emoji')} (/setemoji)\n"
        f"• 2-{sc('day protection')}\n"
        f"• {sc('Higher kill rewards (10-20 XP)')}\n"
        f"• 400 {sc('kills')} / 300 {sc('robs per day')}\n"
        f"• 🏆 {sc('Premium badge on leaderboard')}\n"
        f"• /check {sc('— See your protection time (private)')}\n"
        f"• {sc('Priority AI responses')}\n\n"
        f"👑 {sc('Your status')}: {status}{expiry_line}\n"
        f"👉 {sc('Check your ID')}: /id",
        reply_markup=kb
    )

async def pay_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); u = q.from_user
    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)

    if q.data == "buy_premium_coins":
        if d.get("is_premium"):
            await q.answer(f"Already Premium! 💓", show_alert=True); return
        if d["balance"] < PREMIUM_PRICE_COINS:
            await q.answer(
                f"Not enough coins! Need {fmt(PREMIUM_PRICE_COINS)}, have {fmt(d['balance'])}",
                show_alert=True
            ); return
        now = ts()
        await update_user(u.id, balance=d["balance"]-PREMIUM_PRICE_COINS,
                          is_premium=True, premium_until=now+(PREMIUM_DURATION_DAYS*86400))
        await q.edit_message_text(
            f"💓 <b>{sc('Welcome to Premium!')} 🎉</b>\n\n"
            f"{mention(u)} {sc('is now Premium for 3 months!')}\n"
            f"📅 {sc('Valid until')}: <b>{__import__('time').strftime('%d %b %Y', __import__('time').localtime(now+PREMIUM_DURATION_DAYS*86400))}</b>\n\n"
            f"{sc('All premium features are now active!')}",
            parse_mode="HTML"
        )
    elif q.data == "buy_premium_stars":
        try:
            await context.bot.send_invoice(
                chat_id=u.id,
                title="Iota Premium (3 Months)",
                description="Full premium access for 90 days! Games, economy, custom emoji & more!",
                payload="premium_stars_3m",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice("Iota Premium 3 Months", PREMIUM_PRICE_STARS)],
            )
            await q.answer("Invoice sent to your DM! ⭐", show_alert=True)
        except Exception as e:
            await q.answer(f"Use /fpay command!", show_alert=True)

async def fpay_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Buy premium via Telegram Stars — stars go to owner."""
    if not await require_dm(update, context, "/fpay (Stars purchase)", "pay"):
        return
    u = update.effective_user
    # Stars payments go to the bot owner's Telegram account automatically
    try:
        await context.bot.send_invoice(
            chat_id=u.id,
            title="Iota Premium (3 Months)",
            description="Full premium access for 90 days! Economy, games, AI, custom emoji & more!",
            payload="premium_stars_3m",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice("Iota Premium (90 days)", PREMIUM_PRICE_STARS)],
        )
    except Exception as e:
        await update.message.reply_html(
            f"⭐ {sc('Buy Premium with Telegram Stars')}\n\n"
            f"Stars: <b>{PREMIUM_PRICE_STARS}</b>\n"
            f"Duration: <b>3 {sc('months')}</b>\n\n"
            f"<i>Open this in Telegram app to pay with Stars.</i>"
        )

async def fgems_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)
    if not d.get("is_premium"):
        await update.message.reply_html(
            f"💎 {sc('Gems are only for Premium users!')}\nBuy premium: /pay"
        ); return
    try:
        await context.bot.send_invoice(
            chat_id=u.id,
            title="Iota Gems (1 Gem)",
            description=f"1 Gem = {fmt(GEMS_PRICE_COINS)} in-game coins!",
            payload="gems_stars_1",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice("1 Iota Gem", GEMS_PRICE_STARS)],
        )
    except Exception:
        await update.message.reply_html(f"⭐ {GEMS_PRICE_STARS} Stars = 1 💎 Gem\nUse /fgems in DM!")

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    payment = update.message.successful_payment
    payload = payment.invoice_payload
    stars   = payment.total_amount

    # Log payment
    await log_stars_payment(u.id, payload, stars, u.full_name)

    if payload == "premium_stars_3m":
        now = ts()
        d = await get_user(u.id)
        # Extend if already premium
        current_until = max(d.get("premium_until", now), now)
        new_until = current_until + (PREMIUM_DURATION_DAYS * 86400)
        await update_user(u.id, is_premium=True, premium_until=new_until)
        exp_date = __import__('time').strftime('%d %b %Y',
                               __import__('time').localtime(new_until))
        await update.message.reply_html(
            f"💓 <b>{sc('Payment Successful!')} ⭐</b>\n\n"
            f"{mention(u)} {sc('now has Premium!')}\n"
            f"📅 {sc('Valid until')}: <b>{exp_date}</b>\n"
            f"⭐ Stars paid: <b>{stars}</b>\n\n"
            f"{sc('All premium features unlocked!')} 🎉"
        )
        # Notify owner
        try:
            await context.bot.send_message(
                OWNER_ID,
                f"⭐ <b>Stars Payment!</b>\n\n"
                f"User: {mention(u)} (<code>{u.id}</code>)\n"
                f"Stars: <b>{stars}</b>\n"
                f"Payload: {payload}",
                parse_mode="HTML"
            )
        except Exception:
            pass

    elif payload == "gems_stars_1":
        d = await get_user(u.id)
        await update_user(u.id, gems=d.get("gems",0)+1)
        await update.message.reply_html(
            f"💎 <b>{sc('Gem Purchased!')} ⭐</b>\n\n"
            f"{mention(u)} {sc('received 1 Gem!')}\n"
            f"Total gems: <b>{d.get('gems',0)+1}</b>"
        )

async def setemoji_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the emoji shown before your name on /bal, /profile, leaderboards.

    • Setting requires active Premium (/pay).
    • Once set, the emoji stays forever — even after premium expires.
    • Clear with: /setemoji clear
    """
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)
    current = (d.get("premium_emoji") or "").strip() or "none"

    if not context.args:
        await update.message.reply_html(
            f"Current name emoji: <b>{current}</b>\n"
            f"Usage: /setemoji 😎\n"
            f"Clear: /setemoji clear\n\n"
            f"<i>Setting requires Premium. After set, emoji stays even if premium ends.</i>"
        )
        return

    raw = " ".join(context.args).strip()
    if raw.lower() in ("clear", "reset", "none", "off", "remove"):
        # Clearing also requires premium (so people can't grief by clearing others — own only)
        if not d.get("is_premium") and not current:
            await update.message.reply_html(
                f"💓 {sc('Only Premium Users Can Manage Custom Emoji.')}\n👉 /pay"
            )
            return
        if not d.get("is_premium"):
            # Allow viewing; clear only if premium OR keep permanent once set
            # User asked: emoji stays after premium ends — so clear should also
            # require premium to avoid accidental permanent lock without control.
            await update.message.reply_html(
                f"💓 Premium chahiye emoji clear karne ke liye.\n👉 /pay"
            )
            return
        await update_user(u.id, premium_emoji="")
        await update.message.reply_html("✅ Name emoji cleared. Default 👤 / 💓 wapas.")
        return

    if not d.get("is_premium"):
        await update.message.reply_html(
            f"💓 {sc('Only Premium Users Can Set Custom Emoji.')}\n"
            f"Current (locked): <b>{current}</b>\n👉 /pay"
        )
        return

    # Single emoji / short custom mark (max ~8 chars to block spam titles)
    emoji = raw.split()[0]
    if len(emoji) > 16:
        await update.message.reply_html("❌ Emoji too long. Use a short emoji like 👑 or 😎")
        return
    if emoji.startswith("[") and emoji.endswith("]"):
        await update.message.reply_html("❌ Use a real emoji, not a title. Titles: /shop")
        return

    await update_user(u.id, premium_emoji=emoji)
    await update.message.reply_html(
        f"✅ Name emoji set to <b>{emoji}</b>!\n"
        f"Ye /bal, /profile aur leaderboards pe dikhega — "
        f"premium khatam hone ke baad bhi rahega."
    )

async def check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Premium users: shows protection time privately (sent to DM).
    Normal users: cannot check others' protection — privacy maintained.
    """
    msg = update.effective_message; u = update.effective_user; now = ts()

    # Check if replying to someone else
    if msg.reply_to_message and msg.reply_to_message.from_user:
        target_u = msg.reply_to_message.from_user
        requester = await get_user(u.id)

        # Only premium users can check others
        if not requester.get("is_premium") and target_u.id != u.id:
            await msg.reply_html(
                f"❌ {sc('Only Premium Users Can Check Others Protection Time!')}\n/pay"
            ); return

        if target_u.id == u.id:
            pass  # checking own — proceed below
        else:
            # Premium user checking another — send to their DM for privacy
            await ensure_user(target_u.id, target_u.username or "", target_u.full_name)
            td = await get_user(target_u.id)
            if td["protected_until"] > now:
                rem = td["protected_until"] - now
                result = (
                    f"🔍 <b>{sc('Protection Info')} — {mention(target_u)}</b>\n\n"
                    f"🛡️ {sc('Protected')}: <b>{rem//86400}d {(rem%86400)//3600}h {(rem%3600)//60}m</b> {sc('left')}"
                )
            else:
                result = f"❌ {mention(target_u)} {sc('has no protection')}"

            try:
                await context.bot.send_message(u.id, result, parse_mode="HTML")
                if update.effective_chat.type != "private":
                    await msg.reply_html(
                        f"🔒 {sc('Protection info sent to your DM for privacy!')} 📩"
                    )
            except Exception:
                await msg.reply_html(
                    f"❌ {sc('Start bot DM first')}: @{(await context.bot.get_me()).username}"
                )
            return

    # Own check
    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)
    d_prem = d.get("is_premium", False)

    lines = [f"🔍 <b>{sc('Your Status')}</b>\n"]

    if d["protected_until"] > now:
        rem = d["protected_until"] - now
        lines.append(f"🛡️ {sc('Protected')}: <b>{rem//86400}d {(rem%86400)//3600}h {(rem%3600)//60}m</b> {sc('left')}")
    else:
        lines.append(f"🛡️ {sc('Protection')}: <b>{sc('None')} — Use /protect 1d</b>")

    if d["dead_until"] > now:
        rem2 = d["dead_until"] - now
        lines.append(f"💀 {sc('Dead for')}: <b>{rem2//3600}h {(rem2%3600)//60}m</b>")
    else:
        lines.append(f"💚 {sc('Status')}: <b>{sc('Alive')}</b>")

    if d_prem and d.get("premium_until", 0) > now:
        rem3 = d["premium_until"] - now
        lines.append(f"💓 {sc('Premium')}: <b>{rem3//86400}d {(rem3%86400)//3600}h</b> {sc('left')}")
    else:
        lines.append(f"💓 {sc('Premium')}: <b>{'Active' if d_prem else 'No'}</b>")

    result = "\n".join(lines)

    # Send to DM for privacy if in group
    if update.effective_chat.type != "private":
        try:
            await context.bot.send_message(u.id, result, parse_mode="HTML")
            await msg.reply_html(f"🔒 {sc('Sent to your DM for privacy!')} 📩")
        except Exception:
            await msg.reply_html(
                f"❌ {sc('Start bot DM first')}: @{(await context.bot.get_me()).username}"
            )
    else:
        await msg.reply_html(result)
