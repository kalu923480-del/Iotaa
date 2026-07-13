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
import logging
from typing import Awaitable, Callable, Optional

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
