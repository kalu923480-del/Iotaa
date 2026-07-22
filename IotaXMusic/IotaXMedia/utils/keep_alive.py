# Authored By Iota Coders © 2025
"""Minimal HTTP health server + Render free-tier keep-alive self-ping.

Render free Web Services sleep after ~15 minutes with no inbound HTTP.
This module:
  1. Serves GET / and GET /health on $PORT (so Render health checks pass)
  2. Self-pings that URL every 5 minutes so the service never idles out
"""
from __future__ import annotations

import asyncio
import os
from aiohttp import web, ClientSession, ClientTimeout

from IotaXMedia.logging import LOGGER

log = LOGGER(__name__)


async def _health(_request: web.Request) -> web.Response:
    return web.json_response(
        {"ok": True, "service": "iota-music", "status": "alive"}
    )


async def start_health_server(port: int | None = None) -> web.AppRunner | None:
    port = int(port or os.environ.get("PORT") or os.environ.get("HEALTH_PORT") or 8080)
    app = web.Application()
    app.router.add_get("/", _health)
    app.router.add_get("/health", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    try:
        await site.start()
        log.info(f"✅ Health server listening on 0.0.0.0:{port}")
        return runner
    except OSError as e:
        log.warning(f"⚠️ Health server failed to bind port {port}: {e}")
        await runner.cleanup()
        return None


async def render_keepalive_job(interval: int = 300) -> None:
    """Ping our own /health so Render free tier never spins down."""
    url = (
        os.environ.get("RENDER_EXTERNAL_URL")
        or os.environ.get("KEEPALIVE_URL")
        or ""
    ).strip()
    if not url:
        log.info("ℹ️ Keep-alive disabled (no RENDER_EXTERNAL_URL / KEEPALIVE_URL).")
        return
    url = url.rstrip("/") + "/health"
    log.info(f"🔁 Keep-alive self-ping → {url} every {interval}s")
    while True:
        try:
            await asyncio.sleep(interval)
            async with ClientSession() as session:
                async with session.get(url, timeout=ClientTimeout(total=15)) as resp:
                    log.debug(f"🔁 keep-alive → HTTP {resp.status}")
        except Exception as e:
            log.debug(f"keep-alive ping failed: {e}")
