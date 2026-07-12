"""
Iota Bot — Generic Mini App toolkit

Extracts the reusable bits of webapp/ludo_server.py so every future Mini
App (Roulette, Card table, …) shares ONE auth scheme, ONE app factory, and
ONE runner — instead of copy-pasting the Telegram initData verification
and aiohttp boilerplate each time.

Security: every request carries Telegram's `initData` (the web app attaches
it automatically). We verify its HMAC-SHA256 signature against the bot token
before trusting any claimed user id, so a player can't impersonate another
or tamper with their identity.
"""
import hashlib
import hmac
import json
import logging
import os
import time
from urllib.parse import parse_qsl

from aiohttp import web

from config import BOT_TOKEN

logger = logging.getLogger(__name__)


def verify_init_data(init_data: str) -> dict | None:
    """Validate Telegram Mini App initData. Returns parsed user dict or None."""
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
        received_hash = pairs.pop("hash", None)
        if not received_hash:
            return None
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(computed_hash, received_hash):
            logger.warning("Mini App: initData signature mismatch (forged request?)")
            return None
        auth_date = int(pairs.get("auth_date", 0))
        if time.time() - auth_date > 86400:
            logger.warning("Mini App: initData expired")
            return None
        user_json = pairs.get("user")
        if not user_json:
            return None
        return json.loads(user_json)
    except Exception as e:
        logger.warning(f"Mini App: initData validation error: {e}")
        return None


def authed_user(request) -> dict | None:
    return verify_init_data(request.headers.get("X-Telegram-Init-Data", ""))


def make_app(static_dir: str, index_html: str,
             routes: list = None, ws_routes: dict = None) -> web.Application:
    """Generic Mini App factory.

    static_dir : folder served at /static
    index_html : path to the app's index.html (served at / and /app)
    routes     : list of (method, path, handler) tuples
    ws_routes  : dict of path -> handler (optional websockets)
    """
    app = web.Application()
    app.router.add_get("/", lambda r: web.FileResponse(index_html))
    app.router.add_get("/app", lambda r: web.FileResponse(index_html))
    app.router.add_get("/health", lambda r: web.Response(text="OK", status=200))
    if static_dir and os.path.isdir(static_dir):
        app.router.add_static("/static", path=static_dir, name="static")
    for method, path, handler in (routes or []):
        app.router.add_route(method, path, handler)
    for path, handler in (ws_routes or {}).items():
        app.router.add_get(path, handler)
    return app


async def run_server(app: web.Application, host="0.0.0.0", port=8080):
    """Start a Mini App server as a background asyncio task (in-process)."""
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info(f"🎮 Mini App server running on {host}:{port}")
    return runner
