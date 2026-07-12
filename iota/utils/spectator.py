"""
Iota Bot — Spectator system (shared by multiplayer games)

Keeps a `watchers` set on every live game dict (connect4 / uno / …). When a
game moves, the handler calls notify_watchers() so each watcher gets a DM
with the new board state — they follow the action without cluttering the
group. Pure helpers here so they're trivial to reuse and unit-test.
"""
import logging

logger = logging.getLogger(__name__)


def add_watcher(game: dict, uid: int) -> bool:
    game.setdefault("watchers", set())
    if uid in game["watchers"]:
        return False
    game["watchers"].add(uid)
    return True


def remove_watcher(game: dict, uid: int) -> bool:
    ws = game.setdefault("watchers", set())
    present = uid in ws
    ws.discard(uid)
    return present


def is_watching(game: dict, uid: int) -> bool:
    return uid in game.get("watchers", set())


async def notify_watchers(bot, game: dict, text: str,
                          reply_markup=None, exclude: int = None):
    """DM every watcher of `game` with `text`. Never raises."""
    if bot is None:
        return
    for uid in list(game.get("watchers", set())):
        if uid == exclude:
            continue
        try:
            await bot.send_message(uid, text, parse_mode="HTML",
                                   reply_markup=reply_markup)
        except Exception as e:
            logger.debug(f"spectator DM to {uid} failed: {e}")
