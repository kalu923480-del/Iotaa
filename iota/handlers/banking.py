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

All commands are gated by @premium_gate (Premium-only) stacked above
@economy_gate (so /close economy disables them in
groups) and every money move goes through the atomic helpers in
utils.banking_store / utils.mongo_db, so the accounts can never desync.
"""
import asyncio
import functools
import time

from telegram import Update
from telegram.ext import ContextTypes

from utils.mongo_db import (
    ensure_user, get_user, get_bank, deposit_to_bank, withdraw_from_bank,
    get_loan, take_loan, repay_loan, get_db, get_user_by_username, update_user,
    add_bank_txn, get_bank_txns, create_fd, get_fd, list_fds, fd_payout,
    settle_fd, process_fd_maturities, create_rd, get_rd, list_rds,
    process_rd_installments, create_bank, get_bank_info,
    list_banks, bank_deposit, bank_withdraw, set_bank_profile, close_bank,
    accrue_bank_customer_interest, accrue_bank_interest,
)
from utils.banking_store import (
    transfer_coins, get_savings, savings_deposit, savings_withdraw,
    accrue_savings_interest, apply_loan_overdue,
    SAVINGS_DAILY_RATE, LOAN_OVERDUE_PENALTY_PCT,
)
from utils.helpers import mention, fmt
from utils.fonts import sc
from utils.safe_html import safe_html
from utils.system_gate import economy_gate
from config import (
    BANK_DAILY_RATE, PREMIUM_BANKING_CAP, FD_TENURES, FD_BREAK_PENALTY,
    RD_MIN_INSTALLMENT, RD_MAX_MONTHS, RD_MONTHLY_RATE, RD_BREAK_PENALTY,
    BANK_OPEN_MIN_BALANCE, BANK_CUSTOMER_DAILY_RATE, BANK_RATE_MIN,
    BANK_RATE_MAX,
)

logger = __import__("logging").getLogger(__name__)

LOAN_MAX = 5000
LOAN_INTEREST_PCT = 10
LOAN_DURATION_HOURS = 24


def premium_gate(func):
    """Gate a banking command behind Premium. Stacks above @economy_gate:
    premium is checked first, then the group economy system-gate."""
    @functools.wraps(func)
    async def wrapper(update, context, *a, **kw):
        u = update.effective_user
        if not u:
            return
        await ensure_user(u.id, u.username or "", u.full_name)
        d = await get_user(u.id)
        if not d.get("is_premium"):
            try:
                await update.effective_message.reply_html(
                    "💓 <b>Iota Banking is Premium-only!</b>\n\n"
                    "Buy Premium to unlock the full banking system "
                    "(bank account, savings, FD, RD, passbook & your own "
                    "Bank/Branch):\n/pay or /fpay"
                )
            except Exception:
                pass
            return
        return await func(update, context, *a, **kw)
    return wrapper


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
@premium_gate
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


@premium_gate
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


@premium_gate
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


@premium_gate
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


@premium_gate
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


@premium_gate
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


@premium_gate
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


@premium_gate
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
# Real-life banking products: Fixed Deposit (FD), Recurring Deposit (RD),
# passbook/statement, and user-owned Banks/Branches. All premium-only.
# ═══════════════════════════════════════════════════════════════════════════
def _fd_lines(fd) -> str:
    principal = fd["principal"]
    rate = int(fd.get("rate", 0) * 100)
    proj = principal + int(principal * fd.get("rate", 0))
    rem = max(0, int((fd["maturity_ts"] - time.time()) / 86400))
    return (
        f"📜 <b>FD #{fd['_id']}</b>\n"
        f"💰 Principal: {fmt(principal)}\n"
        f"📈 Rate: {rate}% (over {fd['tenure_days']}d)\n"
        f"💎 Projected payout: {fmt(proj)}\n"
        f"⏳ Matures in: {rem}d"
    )


@premium_gate
@economy_gate
async def fd_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    sub = (context.args[0].lower() if context.args else "")
    # ── list ──
    if sub in ("", "list"):
        fds = await list_fds(u.id)
        if not fds:
            await msg.reply_html(
                f"📜 {sc('No active Fixed Deposits.')}\n\n"
                f"{sc('Create')}: /fd create &lt;amount&gt; &lt;tenure&gt;\n"
                f"{sc('Tenures')}: 30 / 90 / 180 / 365 days"
            )
            return
        text = f"📜 <b>{sc('Your Fixed Deposits')}</b>\n\n"
        for fd in fds:
            text += _fd_lines(fd) + "\n\n"
        await msg.reply_html(text)
        return
    # ── create ──
    if sub == "create":
        if len(context.args) < 3:
            await msg.reply_html(
                f"📜 {sc('Usage')}: /fd create &lt;amount&gt; &lt;tenure&gt;\n"
                f"{sc('Tenures')}: 30 / 90 / 180 / 365 days"
            )
            return
        try:
            principal = int(context.args[1])
        except ValueError:
            await msg.reply_html("❌ " + sc("Amount must be a number."))
            return
        try:
            tenure = int(context.args[2])
        except ValueError:
            await msg.reply_html("❌ " + sc("Tenure must be a number of days."))
            return
        if principal <= 0:
            await msg.reply_html("❌ " + sc("Amount must be positive."))
            return
        if tenure not in FD_TENURES:
            await msg.reply_html(
                f"❌ {sc('Invalid tenure. Choose from')}: 30 / 90 / 180 / 365 days"
            )
            return
        d = await get_user(u.id)
        if d.get("balance", 0) < principal:
            await msg.reply_html(
                f"❌ {sc('Need')} {fmt(principal)} {sc('coins. You have')} {fmt(d.get('balance',0))}"
            )
            return
        # create_fd locks the principal atomically (gated on balance).
        fd = await create_fd(u.id, principal, tenure, FD_TENURES[tenure])
        if not fd:
            await msg.reply_html(
                f"❌ {sc('Could not open the FD (insufficient balance).')}"
            )
            return
        await add_bank_txn(u.id, "fd_create", -principal, f"FD #{fd['_id']}")
        proj = principal + int(principal * FD_TENURES[tenure])
        await msg.reply_html(
            f"📜 {sc('Fixed Deposit opened!')}\n\n"
            f"💰 {sc('Principal')}: {fmt(principal)}\n"
            f"📈 {sc('Rate')}: {int(FD_TENURES[tenure]*100)}% ({tenure}d)\n"
            f"💎 {sc('Payout at maturity')}: {fmt(proj)}\n"
            f"⏳ {sc('Matures in')} {tenure} days — {sc('auto-credited')} 🤖"
        )
        return
    # ── break ──
    if sub == "break":
        if len(context.args) < 2:
            await msg.reply_html("📜 " + sc("Usage: /fd break <fd_id>"))
            return
        from bson import ObjectId
        try:
            fd = await get_fd(ObjectId(context.args[1]))
        except Exception:
            fd = None
        if not fd or fd.get("uid") != u.id or fd.get("status") != "active":
            await msg.reply_html("❌ " + sc("FD not found or not yours."))
            return
        payout = fd_payout(fd)
        await update_user(u.id, balance=(await get_user(u.id)).get("balance", 0) + payout)
        await settle_fd(fd["_id"], payout, "broken")
        await add_bank_txn(u.id, "fd_break", payout, f"FD #{fd['_id']} broken (penalty)")
        await msg.reply_html(
            f"📜 {sc('FD broken early.')}\n💰 {sc('You received')}: {fmt(payout)}\n"
            f"⚠️ {sc('Early-withdrawal penalty applied.')}"
        )
        return
    # ── info ──
    if sub == "info":
        if len(context.args) < 2:
            await msg.reply_html("📜 " + sc("Usage: /fd info <fd_id>"))
            return
        from bson import ObjectId
        try:
            fd = await get_fd(ObjectId(context.args[1]))
        except Exception:
            fd = None
        if not fd or fd.get("uid") != u.id:
            await msg.reply_html("❌ " + sc("FD not found."))
            return
        await msg.reply_html(_fd_lines(fd))
        return
    await msg.reply_html("📜 " + sc("Usage: /fd <create|list|info|break> ..."))


@premium_gate
@economy_gate
async def rd_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    sub = (context.args[0].lower() if context.args else "")
    if sub in ("", "list"):
        rds = await list_rds(u.id)
        if not rds:
            await msg.reply_html(
                f"🔁 {sc('No active Recurring Deposits.')}\n\n"
                f"{sc('Start')}: /rd start &lt;installment&gt; &lt;months&gt;\n"
                f"{sc('Min installment')}: {fmt(RD_MIN_INSTALLMENT)} | "
                f"{sc('Max months')}: {RD_MAX_MONTHS}"
            )
            return
        text = f"🔁 <b>{sc('Your Recurring Deposits')}</b>\n\n"
        for rd in rds:
            paid = rd["paid"]; months = rd["months"]
            proj = rd["total"] + int(rd["total"] * RD_MONTHLY_RATE * months)
            rem = max(0, int((rd["maturity_ts"] - time.time()) / 86400))
            text += (
                f"🔁 <b>RD #{rd['_id']}</b>\n"
                f"💰 Installment: {fmt(rd['installment'])} | {sc('Paid')}: {paid}/{months}\n"
                f"🏦 Saved: {fmt(rd['total'])} | 💎 {sc('Projected')}: {fmt(proj)}\n"
                f"⏳ {rem}d left\n\n"
            )
        await msg.reply_html(text)
        return
    if sub == "start":
        if len(context.args) < 3:
            await msg.reply_html(
                f"🔁 {sc('Usage')}: /rd start &lt;installment&gt; &lt;months&gt;"
            )
            return
        try:
            inst = int(context.args[1])
        except ValueError:
            await msg.reply_html("❌ " + sc("Installment must be a number."))
            return
        try:
            months = int(context.args[2])
        except ValueError:
            await msg.reply_html("❌ " + sc("Months must be a number."))
            return
        if inst < RD_MIN_INSTALLMENT:
            await msg.reply_html(f"❌ {sc('Min installment')}: {fmt(RD_MIN_INSTALLMENT)}")
            return
        if months < 1 or months > RD_MAX_MONTHS:
            await msg.reply_html(f"❌ {sc('Months')}: 1..{RD_MAX_MONTHS}")
            return
        d = await get_user(u.id)
        if d.get("balance", 0) < inst:
            await msg.reply_html(
                f"❌ {sc('Need')} {fmt(inst)} {sc('for the first installment.')}"
            )
            return
        # create_rd locks the first installment atomically (gated on balance).
        rd = await create_rd(u.id, inst, months, RD_MONTHLY_RATE)
        if not rd:
            await msg.reply_html(
                f"❌ {sc('Could not start the RD (insufficient balance).')}"
            )
            return
        await add_bank_txn(u.id, "rd_start", -inst, f"RD #{rd['_id']} started")
        proj = inst + int(inst * RD_MONTHLY_RATE * months)  # first month only shown
        await msg.reply_html(
            f"🔁 {sc('Recurring Deposit started!')}\n\n"
            f"💰 {sc('Installment')}: {fmt(inst)} / month\n"
            f"📆 {sc('Tenure')}: {months} months\n"
            f"🤖 {sc('Installments auto-deducted monthly & matured at the end.')}"
        )
        return
    if sub == "break":
        if len(context.args) < 2:
            await msg.reply_html("🔁 " + sc("Usage: /rd break <rd_id>"))
            return
        from bson import ObjectId
        try:
            rd = await get_rd(ObjectId(context.args[1]))
        except Exception:
            rd = None
        if not rd or rd.get("uid") != u.id or rd.get("status") != "active":
            await msg.reply_html("❌ " + sc("RD not found or not yours."))
            return
        # Early closure: return principal minus the break penalty (never less
        # than the contributed principal).
        payout = max(rd["total"] - int(rd["total"] * RD_BREAK_PENALTY), rd["total"])
        await update_user(u.id, balance=(await get_user(u.id)).get("balance", 0) + payout)
        await get_db().recurring_deposits.update_one(
            {"_id": rd["_id"]}, {"$set": {"status": "broken"}}
        )
        await add_bank_txn(u.id, "rd_break", payout, f"RD #{rd['_id']} broken")
        await msg.reply_html(
            f"🔁 {sc('RD broken.')} 💰 {sc('You received')}: {fmt(payout)}\n"
            f"⚠️ {sc('Early-closure penalty applied.')}"
        )
        return
    await msg.reply_html("🔁 " + sc("Usage: /rd <start|list|break> ..."))


@premium_gate
@economy_gate
async def statement_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    try:
        n = int(context.args[0]) if context.args else 10
    except ValueError:
        n = 10
    n = max(1, min(n, 30))
    txns = await get_bank_txns(u.id, n)
    if not txns:
        await msg.reply_html(f"📒 {sc('Your passbook is empty.')}")
        return
    lines = [f"📒 <b>{sc('Passbook')} — {mention(u)}</b> (last {len(txns)})\n"]
    for t in txns:
        sign = "+" if t["amount"] >= 0 else "-"
        amt = fmt(abs(t["amount"]))
        lines.append(f"• {t['type']}: {sign}{amt} — {safe_html(t.get('note',''))}")
    await msg.reply_html("\n".join(lines))


# ── Bank / Branch resolution helpers ────────────────────────────────────────
async def _resolve_bank(arg: str):
    arg = (arg or "").strip()
    if not arg:
        return None
    if arg.startswith("@"):
        u = await get_user_by_username(arg.lstrip("@"))
        if not u:
            return None
        return await get_db().banks.find_one({"owner_id": u["_id"], "active": True})
    from bson import ObjectId
    try:
        return await get_bank_info(ObjectId(arg))
    except Exception:
        return None


def _bank_deposit_total(bank: dict) -> int:
    return sum(d["principal"] + d["acc_int"] for d in (bank.get("deposits") or {}).values())


@premium_gate
@economy_gate
async def openbank_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    existing = await get_db().banks.find_one({"owner_id": u.id, "active": True})
    if existing:
        await msg.reply_html(
            f"🏦 {sc('You already own a bank')}: {safe_html(existing['name'])}\n"
            f"{sc('Close it first')}: /closebank"
        )
        return
    name = " ".join(context.args).strip()
    if not name:
        await msg.reply_html(
            f"🏦 {sc('Usage')}: /openbank &lt;bank name&gt;\n"
            f"{sc('Requires')} {fmt(BANK_OPEN_MIN_BALANCE)} {sc('coins & Premium.')}"
        )
        return
    d = await get_user(u.id)
    if d.get("balance", 0) < BANK_OPEN_MIN_BALANCE:
        await msg.reply_html(
            f"🏦 {sc('Need')} {fmt(BANK_OPEN_MIN_BALANCE)} {sc('coins to open a bank.')}\n"
            f"{sc('You have')}: {fmt(d.get('balance',0))}"
        )
        return
    # create_bank locks the reserve atomically (gated on balance).
    reserve = BANK_OPEN_MIN_BALANCE
    bank = await create_bank(u.id, name, reserve)
    if not bank:
        await msg.reply_html(
            f"❌ {sc('Could not open the bank (insufficient balance).')}"
        )
        return
    await add_bank_txn(u.id, "bank_open", -reserve, f"Opened bank '{name}'")
    await msg.reply_html(
        f"🏦🎉 <b>{sc('Bank Opened!')}</b>\n\n"
        f"🏛️ {sc('Name')}: {safe_html(name)}\n"
        f"💰 {sc('Reserve capital')}: {fmt(reserve)}\n"
        f"📈 {sc('Customer rate')}: {int(BANK_CUSTOMER_DAILY_RATE*100)}%/day\n\n"
        f"{sc('Customers deposit with')}: /bankdeposit {bank['_id']} &lt;amt&gt;\n"
        f"{sc('Manage')}: /mybank • {sc('Set rate')}: /setbankrate &lt;%&gt;"
    )


@premium_gate
@economy_gate
async def mybank_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    bank = await get_db().banks.find_one({"owner_id": u.id, "active": True})
    if not bank:
        await msg.reply_html(
            f"🏦 {sc('You do not own a bank yet.')}\n"
            f"{sc('Open one with')} {fmt(BANK_OPEN_MIN_BALANCE)} {sc('coins')}: /openbank &lt;name&gt;"
        )
        return
    deposits = _bank_deposit_total(bank)
    customers = len(bank.get("deposits") or {})
    await msg.reply_html(
        f"🏦 <b>{safe_html(bank['name'])}</b> — {sc('Your Bank')}\n\n"
        f"💰 {sc('Reserve')}: {fmt(bank['reserve'])}\n"
        f"👥 {sc('Customers')}: {customers}\n"
        f"🏦 {sc('Customer deposits')}: {fmt(deposits)}\n"
        f"📈 {sc('Customer rate')}: {int(bank.get('rate',0)*100)}%/day\n"
        f"💵 {sc('Fees earned')}: {fmt(bank.get('total_fees',0))}\n\n"
        f"/setbankname • /setbankrate • /bankinfo {bank['_id']}\n"
        f"/closebank"
    )


@premium_gate
@economy_gate
async def bankinfo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    if not context.args:
        await msg.reply_html("🏦 " + sc("Usage: /bankinfo <@owner|bank_id>"))
        return
    bank = await _resolve_bank(context.args[0])
    if not bank or not bank.get("active"):
        await msg.reply_html("❌ " + sc("Bank not found."))
        return
    owner = await get_user(bank["owner_id"])
    deposits = _bank_deposit_total(bank)
    customers = len(bank.get("deposits") or {})
    text = (
        f"🏦 <b>{safe_html(bank['name'])}</b>\n\n"
        f"👑 {sc('Owner')}: {mention(owner) if owner else '?'}\n"
        f"📈 {sc('Interest paid to customers')}: {int(bank.get('rate',0)*100)}%/day\n"
        f"👥 {sc('Customers')}: {customers}\n"
        f"🏦 {sc('Total deposits')}: {fmt(deposits)}\n"
    )
    if bank.get("msg"):
        text += f"\n📝 {safe_html(bank['msg'])}\n"
    text += f"\n💸 {sc('Deposit')}: /bankdeposit {bank['_id']} &lt;amount&gt;"
    await msg.reply_html(text)


@premium_gate
@economy_gate
async def banks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    banks = await list_banks(10)
    if not banks:
        await msg.reply_html(f"🏦 {sc('No banks open yet. Be the first!')} /openbank")
        return
    lines = [f"🏦 <b>{sc('Iota Banks')}</b>\n"]
    for b in banks:
        owner = await get_user(b["owner_id"])
        deposits = _bank_deposit_total(b)
        lines.append(
            f"🏛️ {safe_html(b['name'])} — {mention(owner) if owner else '?'}\n"
            f"   📈 {int(b.get('rate',0)*100)}%/day | 👥 {len(b.get('deposits') or {})} | 🏦 {fmt(deposits)}"
        )
    lines.append(f"\n🏦 {sc('Open yours')}: /openbank &lt;name&gt; (need {fmt(BANK_OPEN_MIN_BALANCE)})")
    await msg.reply_html("\n".join(lines))


@premium_gate
@economy_gate
async def bankdeposit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    if len(context.args) < 2:
        await msg.reply_html("🏦 " + sc("Usage: /bankdeposit <@owner|bank_id> <amount|all>"))
        return
    bank = await _resolve_bank(context.args[0])
    if not bank or not bank.get("active"):
        await msg.reply_html("❌ " + sc("Bank not found."))
        return
    d = await get_user(u.id)
    if bank["owner_id"] == u.id:
        await msg.reply_html("❌ " + sc("You can't deposit into your own bank."))
        return
    amt = _parse_amount(context.args[1], d.get("balance", 0))
    if amt is None or amt <= 0:
        await msg.reply_html("❌ " + sc("Invalid amount."))
        return
    if amt > d.get("balance", 0):
        await msg.reply_html(f"❌ {sc('You only have')} {fmt(d.get('balance',0))}")
        return
    ok, fee = await bank_deposit(bank["_id"], u.id, amt)
    if not ok:
        await msg.reply_html("❌ " + sc("Deposit failed (check balance)."))
        return
    avail = (bank["deposits"].get(str(u.id), {}).get("principal", 0)
             + bank["deposits"].get(str(u.id), {}).get("acc_int", 0))
    await msg.reply_html(
        f"🏦 {sc('Deposited')} {fmt(amt)} {sc('into')} {safe_html(bank['name'])}!\n"
        f"📈 {sc('Earning')} {int(bank.get('rate',0)*100)}%/day\n"
        f"💰 {sc('Your balance here')}: {fmt(avail)}"
    )


@premium_gate
@economy_gate
async def bankwithdraw_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    if len(context.args) < 2:
        await msg.reply_html("🏦 " + sc("Usage: /bankwithdraw <@owner|bank_id> <amount|all>"))
        return
    bank = await _resolve_bank(context.args[0])
    if not bank or not bank.get("active"):
        await msg.reply_html("❌ " + sc("Bank not found."))
        return
    dep = bank["deposits"].get(str(u.id))
    if not dep:
        await msg.reply_html("❌ " + sc("You have no deposit in this bank."))
        return
    available = dep["principal"] + dep["acc_int"]
    token = context.args[1].lower()
    amt = available if token in ("all", "max") else _parse_amount(context.args[1], available)
    if amt is None or amt <= 0:
        await msg.reply_html("❌ " + sc("Invalid amount."))
        return
    ok, payout = await bank_withdraw(bank["_id"], u.id, amt)
    if not ok:
        await msg.reply_html("❌ " + sc("Withdrawal failed."))
        return
    await msg.reply_html(f"🏦 {sc('Withdrew')} {fmt(payout)} {sc('from')} {safe_html(bank['name'])}!")


@premium_gate
@economy_gate
async def setbankname_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    bank = await get_db().banks.find_one({"owner_id": u.id, "active": True})
    if not bank:
        await msg.reply_html("❌ " + sc("You don't own a bank."))
        return
    name = " ".join(context.args).strip()
    if not name:
        await msg.reply_html("🏦 " + sc("Usage: /setbankname <new name>"))
        return
    if not await set_bank_profile(bank["_id"], u.id, name=name):
        await msg.reply_html("❌ " + sc("Could not rename."))
        return
    await msg.reply_html(f"✅ {sc('Bank renamed to')} {safe_html(name)}")


@premium_gate
@economy_gate
async def setbankrate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    bank = await get_db().banks.find_one({"owner_id": u.id, "active": True})
    if not bank:
        await msg.reply_html("❌ " + sc("You don't own a bank."))
        return
    if not context.args:
        await msg.reply_html(
            f"🏦 {sc('Usage')}: /setbankrate &lt;%&gt;\n"
            f"{sc('Range')}: {int(BANK_RATE_MIN*100)}% – {int(BANK_RATE_MAX*100)}% /day"
        )
        return
    try:
        pct = float(context.args[0])
    except ValueError:
        await msg.reply_html("❌ " + sc("Enter a percentage, e.g. 1.0"))
        return
    rate = pct / 100.0
    if await set_bank_profile(bank["_id"], u.id, rate=rate):
        await msg.reply_html(f"✅ {sc('Customer rate set to')} {pct}% /day")
    else:
        await msg.reply_html(
            f"❌ {sc('Rate must be')} {int(BANK_RATE_MIN*100)}%–{int(BANK_RATE_MAX*100)}%"
        )


@premium_gate
@economy_gate
async def closebank_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    bank = await get_db().banks.find_one({"owner_id": u.id, "active": True})
    if not bank:
        await msg.reply_html("❌ " + sc("You don't own a bank."))
        return
    res = await close_bank(bank["_id"])
    if not res.get("ok"):
        await msg.reply_html("❌ " + sc("Could not close bank."))
        return
    await msg.reply_html(
        f"🏦 {sc('Bank closed.')}\n"
        f"💰 {sc('Reserve returned')}: {fmt(res.get('reserve',0))}\n"
        f"👥 {sc('Customer deposits returned')}: {fmt(res.get('returned',0))}\n"
        f"💵 {sc('Fees you earned')}: {fmt(res.get('fees',0))}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Background maintenance (launched once from bot.py post_init)
# ═══════════════════════════════════════════════════════════════════════════
async def banking_maintenance_loop(bot):
    """Runs forever. Every 24h it:
      • credits savings interest,
      • applies the one-time loan overdue penalty,
      • credits demand-deposit (bank) interest (capped at the premium cap),
      • matures Fixed Deposits,
      • collects Recurring Deposit installments & matures completed RDs,
      • credits user-owned bank customer interest.
    Mirrors the repo's other background loops (e.g. _premium_expiry_job)."""
    while True:
        try:
            await asyncio.sleep(86400)
            savings = await accrue_savings_interest()
            penalised = await apply_loan_overdue()
            bank_int = await accrue_bank_interest()
            fds = await process_fd_maturities()
            rds = await process_rd_installments()
            bank_cust = await accrue_bank_customer_interest()
            logger.info(
                f"🏦 banking maintenance: savings→{savings}, loan_penalty→{penalised}, "
                f"bank_int→{bank_int}, fd_matured→{fds}, rd→{rds}, "
                f"bank_customers→{bank_cust}"
            )
        except Exception:
            logger.exception("banking_maintenance_loop error")
