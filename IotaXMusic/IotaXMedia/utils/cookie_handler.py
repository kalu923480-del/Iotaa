# Authored By Iota Coders © 2025
"""YouTube cookies: COOKIE_URL, local assets, or Render Secret Files."""
from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlsplit

import requests

from config import COOKIE_URL
from IotaXMedia.utils.errors import capture_internal_err

# Writable path when cookies are downloaded via COOKIE_URL
COOKIE_PATH = Path("IotaXMedia/assets/cookies.txt")


def _candidates() -> List[Path]:
    """Resolve order: COOKIE_FILE env → Render secrets → local assets."""
    paths: List[Path] = []
    env_path = (os.environ.get("COOKIE_FILE") or "").strip()
    if env_path:
        paths.append(Path(env_path))
    # Render Secret Files are mounted at /etc/secrets/<filename>
    paths.append(Path("/etc/secrets/cookies.txt"))
    paths.append(Path("/etc/secrets/cookies"))
    paths.append(COOKIE_PATH)
    seen = set()
    out: List[Path] = []
    for p in paths:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _is_valid_netscape(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size < 50:
            return False
        head = path.read_text(encoding="utf-8", errors="ignore")[:200]
        if head.lstrip().startswith("# Netscape"):
            return True
        if "youtube.com" in head:
            return True
        return False
    except Exception:
        return False


def resolve_cookie_path() -> Optional[Path]:
    """First valid Netscape cookies file."""
    for p in _candidates():
        if _is_valid_netscape(p):
            return p
    return None


def cookie_path_for_write() -> Path:
    return COOKIE_PATH


def _extract_paste_id(url: str) -> str:
    path = urlsplit(url).path.rstrip("/")
    parts = [p for p in path.split("/") if p]
    return parts[-1] if parts else ""


def resolve_raw_cookie_url(url: str) -> str:
    url = (url or "").strip()
    low = url.lower()

    if "pastebin.com/" in low and "/raw/" not in low:
        paste_id = _extract_paste_id(url)
        return f"https://pastebin.com/raw/{paste_id}" if paste_id else url

    if "batbin.me/" in low:
        if "/api/v2/paste/" in low:
            return url
        paste_id = _extract_paste_id(url)
        return f"https://batbin.me/api/v2/paste/{paste_id}" if paste_id else url

    return url


@capture_internal_err
async def fetch_and_store_cookies():
    if not COOKIE_URL:
        raise EnvironmentError("COOKIE_URL not set in env.")

    raw_url = resolve_raw_cookie_url(COOKIE_URL)

    try:
        response = await asyncio.to_thread(
            requests.get,
            raw_url,
            timeout=15,
            headers={"User-Agent": "iota-cookie-fetcher/1.0"},
        )
        response.raise_for_status()
    except Exception as e:
        raise ConnectionError(f"Can't fetch cookies:\n{e}")

    cookies = (response.text or "").strip()

    if not cookies.startswith("# Netscape"):
        raise ValueError("Invalid cookie format. Needs Netscape format.")

    if len(cookies) < 100:
        raise ValueError("Cookie content too short. Possibly invalid.")

    dest = cookie_path_for_write()
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.write_text(cookies, encoding="utf-8")
    except Exception as e:
        raise IOError(f"Failed to save cookies: {e}")


def sync_secret_cookies_to_assets() -> Optional[Path]:
    """Optional mirror of secret-file cookies into assets (best-effort)."""
    src = resolve_cookie_path()
    if not src:
        return None
    try:
        if src.resolve() == COOKIE_PATH.resolve():
            return src
    except Exception:
        pass
    try:
        COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not COOKIE_PATH.exists() or COOKIE_PATH.stat().st_size < 50:
            shutil.copy2(src, COOKIE_PATH)
    except Exception:
        pass
    return src
