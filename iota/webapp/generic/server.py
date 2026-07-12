"""
Iota Mini App — Generic skeleton server

Reference implementation of utils.miniapp.make_app(). Serves the shared
`webapp/generic/index.html` shell plus one trivial example endpoint so
future Mini Apps have a copy-paste starting point. The Ludo server
(webapp/ludo_server.py) predates this helper and can be refactored onto it
later; new apps SHOULD use it directly.
"""
import os
import logging

from aiohttp import web
from utils.miniapp import authed_user, make_app, run_server

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
_STATIC = os.path.join(_DIR, "static")
_INDEX = os.path.join(_DIR, "index.html")


async def example_api(request):
    user = authed_user(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response({"hello": user.get("first_name", "Player")})


def build_app() -> web.Application:
    return make_app(
        static_dir=_STATIC,
        index_html=_INDEX,
        routes=[("POST", "/api/example", example_api)],
    )


async def run_generic_server(host="0.0.0.0", port=8090):
    return await run_server(build_app(), host=host, port=port)
