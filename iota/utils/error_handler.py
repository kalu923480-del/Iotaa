"""
╔══════════════════════════════════════════════════════════════════╗
║  IOTA BOT — Global Error Handler                                 ║
║                                                                    ║
║  Catches unhandled exceptions in ANY command/handler so a single  ║
║  bug never silently breaks a command — the user always sees a     ║
║  friendly message instead of the bot just not responding.         ║
║                                                                    ║
║  🔴 DIAGNOSABILITY FIX: previously only a *database* failure was   ║
║  reported to the owner, so a regular code bug (NameError,         ║
║  AttributeError, BadRequest from the Telegram API, etc.) would    ║
║  surface to users ONLY as a cold "Kuch gadbad ho gayi!" while the  ║
║  owner got no signal at all about what/when/where broke. Now the   ║
║  FULL traceback (command name + exception) is DM'd to the owner    ║
║  on EVERY unhandled error, so "all my commands fail" becomes      ║
║  "here is exactly which line threw for /chess".                   ║
╚══════════════════════════════════════════════════════════════════╝
"""
import logging
import traceback
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import Conflict

# `TerminatedByOtherGetUpdates` is the specific subclass raised for the
# "Terminated by other getUpdates request" Conflict, but it only exists in
# newer python-telegram-bot releases. On older pinned versions (e.g. 21.3)
# only the base `Conflict` is exported, so alias it to avoid an ImportError
# that would hard-crash the whole bot at import time.
try:
    from telegram.error import TerminatedByOtherGetUpdates
except ImportError:  # pragma: no cover - depends on PTB version
    TerminatedByOtherGetUpdates = Conflict

logger = logging.getLogger(__name__)


def _db_issue(err) -> bool:
    """True if `err` looks like a MongoDB / network connectivity failure."""
    try:
        import pymongo.errors as _pe
        if isinstance(err, (_pe.PyMongoError,)):
            return True
    except Exception:
        pass
    err_str = str(err).lower()
    return any(k in err_str for k in [
        "serverselectiontimeout", "server selection", "connection refused",
        "connection error", "connection timed out", "timed out", "timeout",
        "econnrefused", "connrefused", "authentication failed",
        "not authorized", "operationfailure", "no replica set members",
        "network is unreachable", "ssl handshake", "certificate",
        "getaddrinfo", "name or service not known", "no route to host",
        "dns", "srv", "pymongo", "motor", "missing dependency",
        # Telegram often wraps a dead DB in a generic network/gateway error
        "timed out waiting for a reply", "gateway", "502", "503", "504",
    ])


def _command_name(update: object) -> str:
    """Best-effort: which command (if any) triggered this update."""
    try:
        msg = getattr(update, "effective_message", None)
        if msg and msg.text and msg.text.startswith("/"):
            return msg.text.split()[0]
        q = getattr(update, "callback_query", None)
        if q and q.data:
            return f"callback:{q.data[:40]}"
    except Exception:
        pass
    return "?"


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Registered via app.add_error_handler() in bot.py"""
    err = context.error

    # ── Single-instance Conflict guard ────────────────────────────────────
    # A "Conflict: Terminated by other getUpdates request" means a SECOND
    # process is polling the same token. The instance-lock in bot.py should
    # already prevent this, but if one slips through (e.g. a split-second
    # during a re-deploy) we must NOT spam the owner with a fake "crash" DM.
    # Log it once and move on — the lock will sort it out.
    if isinstance(err, (Conflict, TerminatedByOtherGetUpdates)):
        logger.warning(
            "⚠️ Telegram Conflict (another getUpdates instance). The "
            "single-instance lock normally prevents this; if it persists, "
            "ensure only one Iota process is running."
        )
        return

    logger.error(f"⚠️ Unhandled exception: {err}", exc_info=err)

    db_issue = _db_issue(err)

    # ── Always tell the OWNER the real traceback so failures are
    # diagnosable. (Previously this only happened for DB errors, leaving
    # every other crash invisible to the person who can fix it.)
    try:
        from config import OWNER_ID
        if OWNER_ID:
            tb = "".join(traceback.format_exception(
                type(err), err, err.__traceback__))[-3500:]
            tag = _command_name(update)
            head = (
                "🔥 Iota crashed on a command!\n\n"
                f"📛 {tag}\n"
                f"❌ {type(err).__name__}: {str(err)[:300]}\n\n"
                f"{tb}"
            )
            # Plain text (no parse_mode): a raw traceback contains '<' /
            # '&' that would otherwise make an HTML message fail to send.
            # Truncate to stay under Telegram's 4096-char message limit.
            await context.bot.send_message(OWNER_ID, head[-4000:])
    except Exception:
        pass

    # ── Notify the owner once on a confirmed DB outage (so they fix
    # MONGO_URI / MONGO_PASS instead of thinking every command is broken).
    if db_issue:
        try:
            from config import OWNER_ID
            if OWNER_ID:
                await context.bot.send_message(
                    OWNER_ID,
                    "🔌 <b>Iota DB Connection Issue!</b>\n\n"
                    f"<code>{str(err)[:500]}</code>\n\n"
                    "Check MONGO_URI / MONGO_PASS. DB-backed commands "
                    "(/bal, /daily, /rob, /pay, /ludo …) will fail until fixed.",
                    parse_mode="HTML",
                )
        except Exception:
            pass

    if not isinstance(update, Update):
        return

    try:
        if update.callback_query:
            await update.callback_query.answer(
                "⚠️ Kuch gadbad ho gayi! Thodi der mein try karo." if not db_issue
                else "⚠️ Database connect nahi ho pa raha. Owner ko batao!",
                show_alert=True
            )
            return
        if update.effective_message:
            if db_issue:
                await update.effective_message.reply_html(
                    "🔌 <b>Database Connection Issue!</b>\n\n"
                    "Iota abhi database se connect nahi ho pa rahi.\n"
                    "Owner ko bot ki MongoDB settings check karne ko bolo. 🙏"
                )
            else:
                await update.effective_message.reply_html(
                    "⚠️ Kuch gadbad ho gayi! Thodi der mein try karo 🙄"
                )
    except Exception:
        pass
