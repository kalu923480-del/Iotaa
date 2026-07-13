"""
Single-instance guard for Iota.

Telegram allows only ONE long-poll connection per bot token. If two
processes poll at once (a Render re-deploy that leaves the old process
alive, a stray local run, or two scaled instances) Telegram aborts both
with:

    telegram.error.Conflict: Terminated by other getUpdates request

That used to hard-crash the bot and spam the owner with a "crashed on a
command!" DM (with no command → "📛 ?").

This module enforces a distributed lock in MongoDB so that, at any moment,
exactly ONE Iota process drives `run_polling`:

  • On startup each process claims the lock (identified by a random id) and
    writes a heartbeat every few seconds.
  • A process only becomes the primary (and is allowed to poll) if no other
    *healthy* process holds the lock.
  • A secondary process does NOT call getUpdates — it waits in a short retry
    loop, so it can never trigger a Conflict.
  • If the primary dies, its heartbeat goes stale; within `STALE_SECS` the
    next secondary acquires the lock and takes over (self-healing).

The lock auto-expires via the heartbeat, so a killed/crashed primary can
never permanently block a restart.
"""
import asyncio
import logging
import time
import uuid

logger = logging.getLogger(__name__)

LOCK_ID = "iota"
HEARTBEAT_INTERVAL = 10      # seconds between heartbeat writes
STALE_SECS = 45              # a lock older than this is considered dead

_HOST = ""
try:
    import socket
    _HOST = socket.gethostname()
except Exception:
    pass


def _coll():
    from utils.mongo_db import get_db
    return get_db().bot_instance_lock


async def _try_acquire(my_id: str) -> bool:
    """Atomically claim the lock if free/stale/ours. Returns True if WE own it."""
    from pymongo import ReturnDocument
    coll = _coll()
    now = time.time()
    # Match: no doc yet (upsert creates it), OR it's ours, OR its heartbeat
    # is stale (previous primary died). A fresh lock held by another process
    # does NOT match → update is a no-op → we don't own it.
    filt = {
        "_id": LOCK_ID,
        "$or": [
            {"pid": my_id},
            {"last_heartbeat": {"$lt": now - STALE_SECS}},
        ],
    }
    try:
        doc = await coll.find_one_and_update(
            filt,
            {"$set": {
                "pid": my_id,
                "started_at": now,
                "last_heartbeat": now,
                "host": _HOST,
            }},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except Exception as e:
        # If we can't reach Mongo, don't block startup forever on a transient
        # DB blip — assume we're the primary and let the rest of the bot
        # surface any DB problem normally.
        logger.warning(f"⚠️ Instance-lock acquire failed ({e}); proceeding as primary.")
        return True
    return bool(doc) and doc.get("pid") == my_id


async def _heartbeat(my_id: str):
    """Keep our lock fresh while we are the primary."""
    coll = _coll()
    while True:
        try:
            await coll.update_one(
                {"_id": LOCK_ID, "pid": my_id},
                {"$set": {"last_heartbeat": time.time()}},
            )
        except Exception:
            pass
        await asyncio.sleep(HEARTBEAT_INTERVAL)


async def _release(my_id: str):
    """Best-effort release on graceful shutdown so a restart is instant."""
    try:
        await _coll().delete_one({"_id": LOCK_ID, "pid": my_id})
    except Exception:
        pass


async def ensure_single_instance(application) -> None:
    """
    Block (in post_init, before polling starts) until THIS process is the
    primary instance. Only the primary ever reaches run_polling, so a
    Conflict can never occur. Secondaries wait and take over if the primary
    dies — so the bot stays up with zero manual intervention.
    """
    my_id = uuid.uuid4().hex
    while True:
        if await _try_acquire(my_id):
            asyncio.create_task(_heartbeat(my_id))
            # Release the lock on shutdown so the next start is immediate.
            try:
                application.post_shutdown = _make_release(application, my_id)
            except Exception:
                pass
            logger.info("🔒 Single-instance lock acquired — this process is PRIMARY.")
            return
        logger.warning(
            "⚠️ Another Iota instance is already PRIMARY — waiting to take "
            "over if it stops. (No getUpdates started here, so no Conflict.)"
        )
        await asyncio.sleep(5)


def _make_release(application, my_id: str):
    """Wrap any existing post_shutdown to also release our lock."""
    original = getattr(application, "post_shutdown", None)

    async def _release_then_chain(app):
        await _release(my_id)
        if callable(original):
            try:
                await original(app)
            except Exception:
                pass

    return _release_then_chain
