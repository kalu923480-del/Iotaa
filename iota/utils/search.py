"""
Iota web search — DISABLED.

DuckDuckGo + Wikipedia multi-endpoint search was burning provider tokens and
hitting rate limits (HTML/Lite/Instant + wiki enrich on many AI turns).

All public APIs are no-ops that return empty results so any leftover imports
cannot trigger network calls or extra AI retries.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Hard kill-switch: never perform outbound search.
SEARCH_ENABLED = False


async def web_search(query: str, max_results: int = 5) -> list:
    """Disabled — always empty (no network)."""
    logger.debug("web_search disabled; query ignored: %r", (query or "")[:80])
    return []


async def search_summary(query: str, max_results: int = 4) -> str:
    """Disabled — always empty string (no network)."""
    logger.debug("search_summary disabled; query ignored: %r", (query or "")[:80])
    return ""


def needs_search(text: str) -> bool:
    """Always False — search stack removed."""
    return False
