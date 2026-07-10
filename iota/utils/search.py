"""
Iota Bot — Free Real-Time Web Search Engine

Resilient, multi-source search that works even when DuckDuckGo blocks the
bot's IP (common on datacenter / hosting providers like Render). It tries
providers in order and falls back automatically:

  1. DuckDuckGo HTML  (primary, no API key)
  2. DuckDuckGo Lite  (lighter HTML endpoint)
  3. Wikipedia        (factual fallback — extremely reliable, works from
                       datacenter IPs as long as we send a real User-Agent)

Every provider is wrapped so a single failure (timeout, block page, parse
error) is swallowed and the next source is tried. The AI only ever sees a
clean `search_summary()` string or "" — never a raw exception — so the
chat path stays at 0 errors.
"""
import logging
import aiohttp
import re
import urllib.parse
from html import unescape

logger = logging.getLogger(__name__)

DDG_HTML_URL = "https://html.duckduckgo.com/html/"
DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
WIKI_API_URL = "https://en.wikipedia.org/w/api.php"

# A small pool of realistic browser UAs. DDG blocks datacenter/empty UAs
# aggressively, so we rotate through these and send a real one each time.
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
    "Gecko/20100101 Firefox/125.0",
]
import itertools
_ua_cycle = itertools.cycle(_USER_AGENTS)


def _next_ua() -> str:
    return next(_ua_cycle)


_TAG_RE = re.compile(r"<.*?>")
_ANOMALY_RE = re.compile(r"unusual traffic|anomaly|are you a robot|bot check",
                         re.I)

# ── DuckDuckGo HTML result parser ────────────────────────────────────────
_RESULT_RE = re.compile(
    r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>(.*?)</a>.*?'
    r'class="result__snippet"[^>]*>(.*?)</a>',
    re.S
)
# ── DuckDuckGo Lite result parser ────────────────────────────────────────
_LITE_RE = re.compile(
    r'class="result-link"[^>]*><a href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?class="result-snippet"[^>]*>(.*?)</td>',
    re.S
)


def _clean(text: str) -> str:
    return unescape(_TAG_RE.sub("", text)).strip()


def _real_url(raw: str) -> str:
    """
    DuckDuckGo wraps result links in a redirector:
        //duckduckgo.com/l/?uddg=ENCODED_URL&...
    Extract the real destination so the AI (and any logging) sees a clean
    link instead of a DDG bounce URL.
    """
    if not raw:
        return raw
    m = re.search(r"uddg=([^&]+)", raw)
    if m:
        try:
            return urllib.parse.unquote(m.group(1))
        except Exception:
            return raw
    if raw.startswith("//"):
        return "https:" + raw
    return raw


async def _fetch(session: aiohttp.ClientSession, method: str, url: str,
                **kw) -> str:
    """GET/POST helper with a real UA and a sane timeout."""
    headers = kw.pop("headers", {})
    headers.setdefault("User-Agent", _next_ua())
    timeout = aiohttp.ClientTimeout(total=10)
    if method == "POST":
        async with session.post(url, headers=headers, timeout=timeout,
                                **kw) as r:
            if r.status != 200:
                return ""
            return await r.text()
    else:
        async with session.get(url, headers=headers, timeout=timeout,
                               **kw) as r:
            if r.status != 200:
                return ""
            return await r.text()


async def _ddg_html(query: str, max_results: int) -> list:
    async with aiohttp.ClientSession() as s:
        html = await _fetch(s, "POST", DDG_HTML_URL,
                            data={"q": query, "kl": "us-en"})
    if not html or _ANOMALY_RE.search(html):
        return []
    out = []
    for m in _RESULT_RE.finditer(html):
        url, title, snippet = m.groups()
        out.append({
            "title":   _clean(title),
            "url":     _real_url(url),
            "snippet": _clean(snippet),
        })
        if len(out) >= max_results:
            break
    return out


async def _ddg_lite(query: str, max_results: int) -> list:
    async with aiohttp.ClientSession() as s:
        html = await _fetch(s, "POST", DDG_LITE_URL,
                            data={"q": query, "kl": "us-en"},
                            headers={"Content-Type":
                                      "application/x-www-form-urlencoded",
                                      "Origin": "https://lite.duckduckgo.com"})
    if not html or _ANOMALY_RE.search(html):
        return []
    out = []
    for m in _LITE_RE.finditer(html):
        url, title, snippet = m.groups()
        out.append({
            "title":   _clean(title),
            "url":     _real_url(url),
            "snippet": _clean(snippet),
        })
        if len(out) >= max_results:
            break
    return out


async def _wikipedia(query: str, max_results: int) -> list:
    """Factual fallback. Wikipedia almost never blocks datacenter IPs as
    long as we identify the bot with a real User-Agent."""
    params = {
        "action": "query", "list": "search",
        "srsearch": query, "format": "json",
        "srlimit": max_results, "srprop": "snippet",
    }
    try:
        async with aiohttp.ClientSession() as s:
            txt = await _fetch(s, "GET", WIKI_API_URL, params=params,
                               headers={"User-Agent":
                                        "IotaBot/1.0 (https://t.me/Its_iotabot)"})
        if not txt:
            return []
        data = _safe_json(txt)
        hits = (data.get("query") or {}).get("search") or []
    except Exception as e:
        logger.debug(f"wikipedia search failed: {e}")
        return []
    out = []
    for h in hits:
        title = h.get("title", "")
        if not title:
            continue
        out.append({
            "title":   title,
            "url":     "https://en.wikipedia.org/wiki/" +
                      urllib.parse.quote(title.replace(" ", "_")),
            "snippet": _clean(h.get("snippet", "")),
        })
        if len(out) >= max_results:
            break
    return out


def _safe_json(txt: str):
    try:
        import json
        return json.loads(txt)
    except Exception:
        return {}


async def web_search(query: str, max_results: int = 5) -> list:
    """
    Free web search. Returns list of dicts:
        [{"title", "url", "snippet"}, ...]
    Tries DuckDuckGo HTML → Lite → Wikipedia, returning the first source
    that yields results. Always returns a (possibly empty) list — never
    raises — so callers stay at 0 errors.
    """
    if not query or not query.strip():
        return []
    query = query.strip()
    for provider in (_ddg_html, _ddg_lite, _wikipedia):
        try:
            results = await provider(query, max_results)
            if results:
                logger.debug(
                    f"🔎 search('{query}') -> {len(results)} results "
                    f"via {provider.__name__}"
                )
                return results
        except Exception as e:
            logger.debug(f"search provider {provider.__name__} failed: {e}")
    logger.debug(f"🔎 search('{query}') -> no results from any provider")
    return []


async def search_summary(query: str, max_results: int = 4) -> str:
    """
    Returns a compact text block of search results, ready to inject
    into an AI context prompt for grounded, up-to-date answers.
    Returns "" when no provider could find anything (so the AI falls back
    to its own graceful 'can't check right now' line).
    """
    results = await web_search(query, max_results)
    if not results:
        return ""
    lines = [f"🔍 Real-time info for '{query}':"]
    for i, r in enumerate(results, 1):
        snip = (r.get("snippet") or "")[:240]
        lines.append(f"{i}. {r.get('title', '')} — {snip}")
    return "\n".join(lines)


# Kept for backwards compatibility with any handlers that still import it
def needs_search(text: str) -> bool:
    """
    DEPRECATED — ai_chat.py now uses its own _should_attempt_search()
    with broader, trigger-free logic. This function is kept only so older
    imports (fun.py truth/dare) don't break.
    Returns True for almost everything non-trivial.
    """
    t = text.lower().strip()
    if len(t.split()) < 2:
        return False
    skip = ["hi", "hello", "hii", "bye", "ok", "okay", "lol", "haha"]
    if t in skip:
        return False
    return True
