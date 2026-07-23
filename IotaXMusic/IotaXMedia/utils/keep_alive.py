# Authored By Iota Coders © 2025
"""HTTP health server + Render free-tier 24/7 keep-alive (same idea as iota/).

Render free Web Services sleep after ~15 minutes with no inbound HTTP.
This module:
  1. Serves GET / and GET /health on $PORT (Render health checks pass)
  2. Self-pings that URL every 5 minutes so the service never idles out
  3. Also accepts external monitors (UptimeRobot) hitting /health
"""
from __future__ import annotations

import asyncio
import os
import time
from aiohttp import web, ClientSession, ClientTimeout

from IotaXMedia.logging import LOGGER

log = LOGGER(__name__)
_boot = time.time()
_runner: web.AppRunner | None = None


async def _health(_request: web.Request) -> web.Response:
    return web.json_response(
        {
            "ok": True,
            "service": "iota-music",
            "status": "alive",
            "uptime_sec": int(time.time() - _boot),
        }
    )


async def start_health_server(port: int | None = None) -> web.AppRunner | None:
    global _runner
    port = int(
        port
        or os.environ.get("PORT")
        or os.environ.get("HEALTH_PORT")
        or 8080
    )
    app = web.Application()
    app.router.add_get("/", _health)
    app.router.add_get("/health", _health)
    app.router.add_get("/ping", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    try:
        await site.start()
        _runner = runner
        log.info(f"✅ Health server listening on 0.0.0.0:{port}")
        return runner
    except OSError as e:
        log.warning(f"⚠️ Health server failed to bind port {port}: {e}")
        await runner.cleanup()
        return None


async def stop_health_server() -> None:
    global _runner
    if _runner is not None:
        try:
            await _runner.cleanup()
        except Exception:
            pass
        _runner = None


def _keepalive_base_url() -> str:
    """Public URL for self-ping (Render injects RENDER_EXTERNAL_URL)."""
    for key in (
        "RENDER_EXTERNAL_URL",
        "KEEPALIVE_URL",
        "WEBAPP_BASE_URL",
        "PUBLIC_URL",
    ):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    # Optional explicit host without scheme
    host = (os.environ.get("RENDER_EXTERNAL_HOSTNAME") or "").strip()
    if host:
        return f"https://{host}"
    return ""


async def render_keepalive_job(interval: int = 300) -> None:
    """Ping our own /health so Render free tier never spins down.

    Same behaviour as iota/bot.py `_render_keepalive_job`.
    """
    # Wait for Render to inject URL / service to be fully up
    await asyncio.sleep(20)
    url = _keepalive_base_url()
    if not url:
        log.info(
            "ℹ️ Keep-alive disabled (no RENDER_EXTERNAL_URL / KEEPALIVE_URL). "
            "Set KEEPALIVE_URL=https://YOUR-service.onrender.com after first deploy."
        )
        # Retry a few times in case env appears late
        for _ in range(6):
            await asyncio.sleep(30)
            url = _keepalive_base_url()
            if url:
                break
        if not url:
            return

    url = url.rstrip("/") + "/health"
    log.info(f"🔁 Keep-alive self-ping → {url} every {interval}s")
    consecutive_fail = 0
    while True:
        try:
            await asyncio.sleep(interval)
            timeout = ClientTimeout(total=20, connect=10)
            async with ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status < 500:
                        consecutive_fail = 0
                        log.debug(f"🔁 keep-alive → HTTP {resp.status}")
                    else:
                        consecutive_fail += 1
                        log.warning(f"keep-alive HTTP {resp.status}")
        except Exception as e:
            consecutive_fail += 1
            if consecutive_fail <= 3 or consecutive_fail % 10 == 0:
                log.warning(f"keep-alive ping failed ({consecutive_fail}): {e}")
