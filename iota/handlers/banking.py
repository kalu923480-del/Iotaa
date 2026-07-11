"""
Iota Bot — Banking system (canonical handler module)

Commands:
  /bank        — full overview (wallet, bank, savings, loan, net worth)
  /deposit     — move coins wallet -> bank (safe from /rob)
  /withdraw    — move coins bank -> wallet
  /loan        — borrow coins (flat interest, due in 24h)
  /repay       — repay an outstanding loan
  /transfer    — send coins to another user (reply or @username)
  /savings     — interest-bearing deposit (deposit / withdraw / check)
  /networth    — total wealth of you (or a replied user)

All commands are gated by @economy_gate (so /close economy disables them in
groups) and every money move goes through the atomic helpers in
utils.banking_store / utils.mongo_db, so the accounts can never desync.
"""
import asyncio
import time

from telegram import Update
from telegram.ext import ContextTypes

from utils.mongo_db import (
    ensure_user, get_user, get_bank, deposit_to_bank, withdraw_from_bank,
    get_loan, take_loan, repay_loan, get_db, get_user_by_username,
)
from utils.banking_store import (
    transfer_coins, get_savings, savings_deposit, savings_withdraw,
    accrue_savings_interest, apply_loan_overdue,
    SAVINGS_DAILY_RATE, LOAN_OVERDUE_PENALTY_PCT,
)
from utils.helpers import mention, fmt
from utils.fonts import sc
from utils.system_gate import economy_gate

logger = __import__("logging").getLogger(__name__)

LOAN_MAX = 5000
LOAN_INTEREST_PCT = 10
LOAN_DURATION_HOURS = 24


def _parse_amount(raw: str, available: int):
    """Parse '500' or 'all'/'max' against `available`. None = invalid."""
    if not raw:
        return None
    raw = str(raw).lower().strip()
    if raw in ("all", "max"):
        return available
    try:
        amt = int(raw)
    except ValueError:
        return None
    return amt if amt > 0 else None


# ═══════════════════════════════════════════════════════════════════════════
@economy_gate
async def bank_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)
    bank = await get_bank(u.id)
    savings = await get_savings(u.id)
    loan = await get_loan(u.id)
    bal = d.get("balance", 0)
    total = bal + bank + savings - loan["amount"]
    text = (
        f"🏦 <b>{sc('Bank')} — {mention(u)}</b>\n\n"
        f"💼 {sc('Wallet')}: {fmt(bal)}\n"
        f"🏦 {sc('Bank (safe from rob)')}: {fmt(bank)}\n"
        f"🐖 {sc('Savings')} ({int(SAVINGS_DAILY_RATE*100)}%/day): {fmt(savings)}\n"
    )
    if loan["amount"] > 0:
        hrs = max(0, (loan["due_ts"] - time.time()) / 3600)
        text += f"💳 {sc('Loan owed')}: {fmt(loan['amount'])} ({hrs:.1f}h left)\n"
    text += (
        f"\n💰 <b>{sc('Net Worth')}: {fmt(total)}</b>\n\n"
        f"{sc('Deposit')}: /deposit &lt;amt|all&gt;\n"
        f"{sc('Withdraw')}: /withdraw &lt;amt|all&gt;\n"
        f"{sc('Send coins')}: /transfer &lt;@user&gt; &lt;amt&gt;\n"
        f"{sc('Savings')}: /savings deposit &lt;amt|all&gt;"
    )
    await msg.reply_html(text)


@economy_gate
async def deposit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)
    if not context.args:
        await msg.reply_html("🏦 " + sc("Usage: /deposit <amount|all>"))
        return
    amt = _parse_amount(context.args[0], d.get("balance", 0))
    if amt is None:
        await msg.reply_html("❌ " + sc("Invalid amount."))
        return
    if amt > d.get("balance", 0):
        await msg.reply_html(f"❌ {sc('You only have')} {fmt(d.get('balance',0))} {sc('in your wallet.')}")
        return
    await deposit_to_bank(u.id, amt)
    await msg.reply_html(f"🏦 {sc('Deposited')} {fmt(amt)} {sc('to your bank.')} {sc('Safe from /rob now!')}")


@economy_gate
async def withdraw_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    bank = await get_bank(u.id)
    if not context.args:
        await msg.reply_html("🏦 " + sc("Usage: /withdraw <amount|all>"))
        return
    amt = _parse_amount(context.args[0], bank)
    if amt is None:
        await msg.reply_html("❌ " + sc("Invalid amount."))
        return
    if amt > bank:
        await msg.reply_html(f"❌ {sc('You only have')} {fmt(bank)} {sc('in your bank.')}")
        return
    await withdraw_from_bank(u.id, amt)
    await msg.reply_html(f"💼 {sc('Withdrew')} {fmt(amt)} {sc('to your wallet.')}")


@economy_gate
async def loan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    try:
        current = await get_loan(u.id)
        if not context.args:
            if current["amount"] > 0:
                hrs = max(0, (current["due_ts"] - time.time()) / 3600)
                await msg.reply_html(
                    f"💳 {sc('You owe')} <b>{fmt(current['amount'])}</b>\n"
                    f"⏳ {sc('Due in')} {hrs:.1f}h\n"
                    f"{sc('Repay')}: /repay &lt;amount|all&gt;"
                )
            else:
                await msg.reply_html(
                    f"🏦 " + sc(f"Usage: /loan <amount> (max {LOAN_MAX})") +
                    f"\n{sc('Interest')}: {LOAN_INTEREST_PCT}% — {sc('due in')} {LOAN_DURATION_HOURS}h"
                )
            return

        if current["amount"] > 0:
            await msg.reply_html(
                f"❌ {sc('You already have an outstanding loan of')} {fmt(current['amount'])}.\n"
                f"{sc('Repay it first')}: /repay &lt;amount|all&gt;"
            )
            return

        try:
            principal = int(context.args[0])
        except ValueError:
            await msg.reply_html("❌ " + sc("Amount must be a number."))
            return
        if principal <= 0 or principal > LOAN_MAX:
            await msg.reply_html(f"❌ " + sc(f"Loan amount must be between 1 and {LOAN_MAX}."))
            return

        owed = int(principal * (1 + LOAN_INTEREST_PCT / 100))
        due_ts = time.time() + LOAN_DURATION_HOURS * 3600
        await take_loan(u.id, principal, due_ts)
        await get_db().users.update_one(
            {"_id": u.id},
            {"$set": {"loan_amount": owed, "loan_overdue": False}},
        )
        await msg.reply_html(
            f"💰 {sc('Loan approved!')} +{fmt(principal)} {sc('to your wallet.')}\n"
            f"💳 {sc('You owe')}: <b>{fmt(owed)}</b> ({LOAN_INTEREST_PCT}% {sc('interest')})\n"
            f"⏳ {sc('Due in')} {LOAN_DURATION_HOURS}h — {sc('repay with')} /repay"
        )
    except Exception as e:
        logger.exception("loan_cmd failed: %s", e)
        await msg.reply_html("⚠️ " + sc("Loan process mein kuch gadbad ho gayi. Try again."))


@economy_gate
async def repay_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)
    loan = await get_loan(u.id)
    if loan["amount"] <= 0:
        await msg.reply_html("✅ " + sc("You have no outstanding loan!"))
        return
    if not context.args:
        await msg.reply_html(f"💳 {sc('You owe')} {fmt(loan['amount'])}. " + sc("Usage: /repay <amount|all>"))
        return
    amt = _parse_amount(context.args[0], min(loan["amount"], d.get("balance", 0)))
    if amt is None:
        await msg.reply_html("❌ " + sc("Invalid amount."))
        return
    if amt > d.get("balance", 0):
        await msg.reply_html(f"❌ {sc('You only have')} {fmt(d.get('balance',0))} {sc('in your wallet.')}")
        return
    paid = await repay_loan(u.id, amt)
    remaining = loan["amount"] - paid
    if remaining <= 0:
        await msg.reply_html(f"✅ {sc('Loan fully repaid!')} {sc('Paid')}: {fmt(paid)} 🎉")
    else:
        await msg.reply_html(f"💳 {sc('Paid')} {fmt(paid)}. {sc('Remaining')}: {fmt(remaining)}")


@economy_gate
async def transfer_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    if len(context.args) < 2:
        await msg.reply_html("💸 " + sc("Usage: /transfer <@user|reply> <amount|all>"))
        return
    # ── resolve target ──────────────────────────────────────────────────
    target = None
    token = context.args[0]
    if msg.reply_to_message and msg.reply_to_message.from_user:
        target = msg.reply_to_message.from_user
    elif token.startswith("@"):
        rec = await get_user_by_username(token.lstrip("@"))
        if rec:
            target = type("T", (), {"id": rec["_id"], "full_name": rec.get("full_name", "User")})()
    elif token.lstrip("-").isdigit():
        target = type("T", (), {"id": int(token), "full_name": f"User {token}"})()
    if target is None or target.id == u.id:
        await msg.reply_html("❌ " + sc("Reply to a user or use @username to transfer."))
        return
    d = await get_user(u.id)
    amt = _parse_amount(context.args[1], d.get("balance", 0))
    if amt is None:
        await msg.reply_html("❌ " + sc("Invalid amount."))
        return
    if amt > d.get("balance", 0):
        await msg.reply_html(f"❌ {sc('You only have')} {fmt(d.get('balance',0))} {sc('coins.')}")
        return
    ok = await transfer_coins(u.id, target.id, amt)
    if not ok:
        await msg.reply_html("❌ " + sc("Transfer failed (check balance / user)."))
        return
    await msg.reply_html(
        f"💸 {mention(u)} → {mention(target)}: <b>{fmt(amt)}</b> {sc('coins sent!')}"
    )


@economy_gate
async def savings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    d = await get_user(u.id)
    savings = await get_savings(u.id)
    if not context.args:
        await msg.reply_html(
            f"🐖 <b>{sc('Savings')}</b>\n\n"
            f"{sc('Balance')}: {fmt(savings)}\n"
            f"📈 {sc('Interest')}: {int(SAVINGS_DAILY_RATE*100)}%/day (compounding)\n\n"
            f"{sc('Deposit')}: /savings deposit &lt;amt|all&gt;\n"
            f"{sc('Withdraw')}: /savings withdraw &lt;amt|all&gt;"
        )
        return
    sub = context.args[0].lower()
    if sub not in ("deposit", "withdraw"):
        await msg.reply_html("🐖 " + sc("Usage: /savings <deposit|withdraw> <amount|all>"))
        return
    if len(context.args) < 2:
        await msg.reply_html("🐖 " + sc("Usage: /savings <deposit|withdraw> <amount|all>"))
        return
    if sub == "deposit":
        amt = _parse_amount(context.args[1], d.get("balance", 0))
        if amt is None or amt <= 0:
            await msg.reply_html("❌ " + sc("Invalid amount."))
            return
        if amt > d.get("balance", 0):
            await msg.reply_html(f"❌ {sc('You only have')} {fmt(d.get('balance',0))} {sc('coins.')}")
            return
        if await savings_deposit(u.id, amt):
            await msg.reply_html(f"🐖 {sc('Deposited')} {fmt(amt)} {sc('to savings.')} {sc('Earning')} {int(SAVINGS_DAILY_RATE*100)}%/day!")
        else:
            await msg.reply_html("❌ " + sc("Deposit failed."))
    else:
        amt = _parse_amount(context.args[1], savings)
        if amt is None or amt <= 0:
            await msg.reply_html("❌ " + sc("Invalid amount."))
            return
        if amt > savings:
            await msg.reply_html(f"❌ {sc('You only have')} {fmt(savings)} {sc('in savings.')}")
            return
        if await savings_withdraw(u.id, amt):
            await msg.reply_html(f"💼 {sc('Withdrew')} {fmt(amt)} {sc('from savings to wallet.')}")
        else:
            await msg.reply_html("❌ " + sc("Withdrawal failed."))


@economy_gate
async def networth_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    target = msg.reply_to_message.from_user if msg.reply_to_message else update.effective_user
    await ensure_user(target.id, target.username or "", target.full_name)
    d = await get_user(target.id)
    bank = await get_bank(target.id)
    savings = await get_savings(target.id)
    loan = await get_loan(target.id)
    try:
        from config import GEMS_PRICE_COINS
        gems_value = d.get("gems", 0) * GEMS_PRICE_COINS
    except ImportError:
        gems_value = 0
    total = d.get("balance", 0) + bank + savings + gems_value - loan["amount"]
    await msg.reply_html(
        f"📊 <b>{sc('Net Worth')} — {mention(target)}</b>\n\n"
        f"💼 {sc('Wallet')}: {fmt(d.get('balance',0))}\n"
        f"🏦 {sc('Bank')}: {fmt(bank)}\n"
        f"🐖 {sc('Savings')}: {fmt(savings)}\n"
        f"💎 {sc('Gems value')}: {fmt(gems_value)}\n"
        f"💳 {sc('Loan owed')}: -{fmt(loan['amount'])}\n\n"
        f"💰 <b>{sc('Total')}: {fmt(total)}</b>"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Background maintenance (launched once from bot.py post_init)
# ═══════════════════════════════════════════════════════════════════════════
async def banking_maintenance_loop(bot):
    """Runs forever: every 24h it credits savings interest and applies the
    one-time overdue penalty to loans that have passed their due time.
    Mirrors the repo's other background loops (e.g. _premium_expiry_job)."""
    while True:
        try:
            await asyncio.sleep(86400)
            earned = await accrue_savings_interest()
            penalised = await apply_loan_overdue()
            logger.info(
                f"🏦 banking maintenance: interest→{earned} users, "
                f"loan overdue penalty→{penalised} users"
            )
        except Exception:
            logger.exception("banking_maintenance_loop error")
