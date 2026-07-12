"""
Iota Mini App — Roulette

A live-spinning wheel Mini App built on the generic skeleton
(utils.miniapp). It is OPTIONAL: the in-chat /roulette command remains the
canonical way to play. The Mini App only enhances the experience with a
real animated wheel; the authoritative result is computed server-side here
and reported back to the chat via Telegram.WebApp.sendData (the chat
version decides payouts). If WEBAPP_BASE_URL / ROULETTE_MINIAPP isn't set,
bot.py simply never starts this server, so nothing breaks.

Auth: every request must carry valid Telegram initData (verified in
utils.miniapp.authed_user) before we trust a claimed identity.
"""
import os
import logging
import random

from aiohttp import web
from utils.miniapp import authed_user, make_app, run_server

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
_INDEX = os.path.join(_DIR, "index.html")

_RED = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
_GREEN = {0}


def _color(pocket: int) -> str:
    if pocket in _GREEN:
        return "Green"
    return "Red" if pocket in _RED else "Black"


async def spin_api(request):
    user = authed_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    pocket = random.randint(0, 36)
    return web.json_response({"pocket": pocket, "color": _color(pocket)})


def build_app() -> web.Application:
    return make_app(
        static_dir=None,
        index_html=_INDEX,
        routes=[("POST", "/api/roulette/spin", spin_api)],
    )


async def run_roulette_server(host="0.0.0.0", port=8091):
    return await run_server(build_app(), host=host, port=port)
