"""
Iota — Resilient Telegram network helpers.

A single, shared place that wraps the *network* half of every Telegram call
(reply / edit / delete / send) so a transient blip (a slow Telegram gateway, a
`TimedOut`, a `RetryAfter` flood-control, a `NetworkError`) can NEVER surface to
the owner as a hard "X crashed!" report.

Why this exists
──────────────
Previously a command like ``/previewtts`` would do
``await update.message.reply_html("Generating…")`` and, if Telegram took >10s to
answer, raise ``telegram.error.TimedOut``. That bubbled all the way up to the
owner-panel crash reporter and looked like a fatal bug when it was really just a
flaky network moment. These helpers retry transient errors once and otherwise
return ``None`` (or swallow the failure after logging) so the *command* keeps
working and only the cosmetic status message is lost.

Usage
─────
    from utils.telegram_safe import safe_call

    msg = await safe_call(lambda: update.message.reply_html("hi"))
    if msg is None:
        # network was down — send fresh / bail gracefully
    await safe_call(lambda: msg.edit_text("done"), label="previewtts.edit")

``safe_call`` takes a zero-arg callable that *returns a fresh coroutine* on each
call (a ``lambda``), never an already-awaited coroutine object, so retries are
always valid.
"""
import asyncio
import contextlib
import logging
from typing import Awaitable, Callable, Optional

from telegram.constants import ChatAction
from telegram.error import (TimedOut, RetryAfter, NetworkError,
                            BadRequest, Forbidden)

logger = logging.getLogger(__name__)

# Errors that are *transient* and worth retrying once.
_TRANSIENT = (TimedOut, NetworkError)

# Errors that are *permanent* for this exact call — retrying is pointless and
# would just spam Telegram, so we surface them once (as None) and move on.
_PERMANENT = (BadRequest, Forbidden)


async def safe_call(
    fn: Callable[[], Awaitable],
    *,
    retries: int = 1,
    label: str = "telegram",
    sleep_base: float = 1.0,
) -> Optional[object]:
    """
    Run ``fn()`` (which must return a fresh coroutine each call) and return its
    result. Retries ``retries`` times on transient errors (TimedOut /
    NetworkError / RetryAfter). Permanent errors (BadRequest / Forbidden) and
    exhausted retries return ``None`` after logging. NEVER raises.
    """
    if fn is None:
        return None
    last = None
    for attempt in range(retries + 1):
        try:
            return await fn()
        except _PERMANENT as e:
            # NOTE: check permanent BEFORE transient — in python-telegram-bot
            # BadRequest/Forbidden are subclasses of NetworkError, so a
            # permanent error would otherwise be mistaken for a transient one
            # and pointless retries fired.
            logger.warning(
                f"[safe_call:{label}] permanent error (not retrying): "
                f"{type(e).__name__}: {e}"
            )
            return None
        except RetryAfter as e:
            last = e
            if attempt < retries:
                await asyncio.sleep(min(getattr(e, "retry_after", 5) or 5, 30))
                continue
            logger.warning(f"[safe_call:{label}] rate-limited, giving up: {e}")
            return None
        except _TRANSIENT as e:
            last = e
            if attempt < retries:
                await asyncio.sleep(sleep_base * (attempt + 1))
                continue
            logger.warning(
                f"[safe_call:{label}] transient error after "
                f"{retries + 1} attempts: {type(e).__name__}: {e}"
            )
            return None
        except Exception as e:  # noqa: BLE001 — last-resort guard
            logger.warning(
                f"[safe_call:{label}] unexpected error (not retrying): "
                f"{type(e).__name__}: {e}"
            )
            return None
    return None


# ── Chat-action ("typing…", "choosing sticker…") helpers ──────────────────
#
# Telegram chat actions (the "Iota is typing…" / "Iota is choosing a
# sticker…" hint shown under the chat title) auto-expire after ~5 seconds.
# For anything that takes longer (an AI call + web search, a quote render
# hitting an external API) a single send_chat_action call would flicker off
# mid-work. `chat_action()` is an async context manager that RE-SENDS the
# action every few seconds until the work is done, so the indicator stays
# visible for the whole duration. It NEVER raises — a failed action must
# never break the actual reply.

# Common actions, re-exported so callers don't need to import ChatAction.
ACTION_TYPING = ChatAction.TYPING
ACTION_CHOOSE_STICKER = ChatAction.CHOOSE_STICKER
ACTION_UPLOAD_PHOTO = ChatAction.UPLOAD_PHOTO
ACTION_UPLOAD_VOICE = ChatAction.UPLOAD_VOICE
ACTION_RECORD_VOICE = ChatAction.RECORD_VOICE


async def send_action(bot, chat_id, action=ACTION_TYPING,
                      message_thread_id=None) -> None:
    """Fire a single chat action. Swallows every error (a cosmetic hint must
    never break a command)."""
    try:
        kwargs = {"chat_id": chat_id, "action": action}
        if message_thread_id is not None:
            kwargs["message_thread_id"] = message_thread_id
        await bot.send_chat_action(**kwargs)
    except Exception as e:
        logger.debug(f"send_action failed ({action}): {e}")


@contextlib.asynccontextmanager
async def chat_action(bot, chat_id, action=ACTION_TYPING, *,
                      interval: float = 4.0, message_thread_id=None):
    """
    Async context manager that keeps a chat action alive for the whole
    duration of the wrapped work by re-sending it every `interval` seconds
    (Telegram expires actions after ~5s). Works in DMs AND groups.

    Usage:
        async with chat_action(context.bot, chat_id, ACTION_TYPING):
            reply = await do_slow_work()
        await msg.reply_html(reply)

    Never raises: if the background refresher hits an error it just stops
    quietly, leaving the real work untouched.
    """
    stop = asyncio.Event()

    async def _pump():
        # Send once immediately, then refresh until told to stop.
        while not stop.is_set():
            await send_action(bot, chat_id, action,
                              message_thread_id=message_thread_id)
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue

    task = asyncio.create_task(_pump())
    try:
        yield
    finally:
        stop.set()
        with contextlib.suppress(Exception):
            await task
