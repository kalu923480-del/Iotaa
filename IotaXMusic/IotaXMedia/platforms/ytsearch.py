"""
Async YouTube search shim.

Uses yt-dlp flat playlist search (reliable) instead of the broken
youtube-search-python package (incompatible with current httpx/requests).
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Optional


def _format_duration(seconds: Optional[float]) -> Optional[str]:
    if seconds is None:
        return None
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return None
    if total < 0:
        return None
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _entry_to_result(entry: Dict[str, Any]) -> Dict[str, Any]:
    vid = entry.get("id") or ""
    url = entry.get("url") or entry.get("webpage_url") or (
        f"https://www.youtube.com/watch?v={vid}" if vid else ""
    )
    if url and not str(url).startswith("http") and vid:
        url = f"https://www.youtube.com/watch?v={vid}"
    title = entry.get("title") or "Unknown"
    duration = entry.get("duration")
    channel = entry.get("channel") or entry.get("uploader") or "Unknown"
    channel_url = entry.get("channel_url") or entry.get("uploader_url") or "https://youtube.com"
    thumb = entry.get("thumbnail")
    if not thumb:
        thumbs = entry.get("thumbnails") or []
        if thumbs:
            thumb = thumbs[-1].get("url")
    if not thumb and vid:
        thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
    views = entry.get("view_count")
    return {
        "type": "video",
        "id": vid,
        "title": title,
        "publishedTime": entry.get("upload_date") or entry.get("release_timestamp") or "Unknown",
        "duration": _format_duration(duration) if not isinstance(duration, str) else duration,
        "viewCount": {"text": str(views) if views is not None else "Unknown", "short": str(views) if views is not None else "Unknown"},
        "thumbnails": [{"url": thumb}] if thumb else [],
        "thumbnail": thumb or "",
        "richThumbnail": None,
        "descriptionSnippet": [{"text": entry.get("description") or ""}],
        "channel": {
            "name": channel,
            "id": entry.get("channel_id") or "",
            "thumbnails": [],
            "link": channel_url,
        },
        "accessibility": {"title": title, "duration": _format_duration(duration) or ""},
        "link": url,
        "shelfTitle": None,
    }


def _search_sync(query: str, limit: int = 1) -> Dict[str, Any]:
    import subprocess

    q = (query or "").strip()
    if not q:
        return {"result": []}

    # Direct watch URL / video id
    if re.fullmatch(r"[a-zA-Z0-9_-]{11}", q):
        target = f"https://www.youtube.com/watch?v={q}"
        cmd = [
            "yt-dlp",
            "--dump-json",
            "--no-warnings",
            "--no-update",
            "--skip-download",
            target,
        ]
    elif q.startswith("http://") or q.startswith("https://"):
        cmd = [
            "yt-dlp",
            "--dump-json",
            "--no-warnings",
            "--no-update",
            "--skip-download",
            q,
        ]
    else:
        cmd = [
            "yt-dlp",
            "--flat-playlist",
            "--dump-json",
            "--no-warnings",
            "--no-update",
            f"ytsearch{max(1, int(limit))}:{q}",
        ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    except Exception:
        return {"result": []}

    out = (proc.stdout or "").strip()
    if not out:
        return {"result": []}

    results: List[Dict[str, Any]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("_type") == "playlist":
            continue
        results.append(_entry_to_result(entry))
        if len(results) >= limit:
            break
    return {"result": results}


class VideosSearch:
    def __init__(self, query, limit: int = 1, region=None):
        self._query = query
        self._limit = limit

    async def next(self):
        return await asyncio.to_thread(_search_sync, self._query, self._limit)


class Playlist:
    """Raise so caller's existing yt-dlp fallback is used."""

    @classmethod
    async def get(cls, link: str):
        raise RuntimeError("Playlist class unavailable; use yt-dlp fallback")
