# Authored By Iota Coders © 2025
import asyncio
import contextlib
import json
import os
import re
import time
from typing import Dict, List, Optional, Tuple, Union

import yt_dlp
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message
from IotaXMedia.platforms.ytsearch import VideosSearch, Playlist

from IotaXMedia.utils.cookie_handler import resolve_cookie_path
from IotaXMedia.utils.database import is_on_off
from IotaXMedia.utils.downloader import yt_dlp_download
from IotaXMedia.utils.errors import capture_internal_err
from IotaXMedia.utils.formatters import time_to_seconds
from IotaXMedia.utils.tuning import YTDLP_TIMEOUT, YOUTUBE_META_MAX, YOUTUBE_META_TTL


# === Caches ===
_cache: Dict[str, Tuple[float, List[Dict]]] = {}
_cache_lock = asyncio.Lock()
_formats_cache: Dict[str, Tuple[float, List[Dict], str]] = {}
_formats_lock = asyncio.Lock()


# === Constants ===
YOUTUBE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")


# === Helpers ===
def _cookiefile_path() -> Optional[str]:
    """Temp copy of cookies so CLI yt-dlp cannot rewrite the master file."""
    try:
        import shutil
        import tempfile

        src = resolve_cookie_path()
        if not src:
            return None
        fd, tmp = tempfile.mkstemp(prefix="yt_cookies_cli_", suffix=".txt")
        os.close(fd)
        shutil.copy2(src, tmp)
        return tmp
    except Exception:
        pass
    return None


def _cookies_args() -> List[str]:
    path = _cookiefile_path()
    return ["--cookies", path] if path else []


def _js_runtime_args() -> List[str]:
    import shutil

    home = os.path.expanduser("~/.deno/bin/deno")
    deno = home if os.path.isfile(home) else shutil.which("deno")
    if deno:
        return ["--js-runtimes", f"deno:{deno}"]
    return []


async def _exec_proc(*args: str) -> Tuple[bytes, bytes]:
    # Prefer python -m yt_dlp so PATH/deno issues don't break metadata
    argv = list(args)
    if argv and argv[0] == "yt-dlp":
        argv = ["python3", "-m", "yt_dlp", *argv[1:]]
    env = os.environ.copy()
    deno_home = os.path.expanduser("~/.deno/bin")
    if os.path.isdir(deno_home):
        env["PATH"] = deno_home + os.pathsep + env.get("PATH", "")
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        return await asyncio.wait_for(proc.communicate(), timeout=YTDLP_TIMEOUT)
    except asyncio.TimeoutError:
        with contextlib.suppress(Exception):
            proc.kill()
        return b"", b"timeout"


async def _ytdlp_dump_json(target: str, *, flat: bool = False) -> Optional[Dict]:
    """Reliable metadata via yt-dlp (cookies + deno)."""
    args = [
        "yt-dlp",
        *(_cookies_args()),
        *(_js_runtime_args()),
        "--dump-json",
        "--no-warnings",
        "--no-update",
        "--skip-download",
        "--socket-timeout",
        "25",
    ]
    if flat:
        args.append("--flat-playlist")
    args.append(target)
    stdout, stderr = await _exec_proc(*args)
    if not stdout:
        return None
    try:
        first = stdout.decode(errors="ignore").strip().splitlines()[0]
        return json.loads(first)
    except Exception:
        return None


@capture_internal_err
async def cached_youtube_search(query: str) -> List[Dict]:
    key = f"q:{query}"
    now = time.time()

    async with _cache_lock:
        if key in _cache:
            ts, val = _cache[key]
            if now - ts < YOUTUBE_META_TTL:
                return val
            _cache.pop(key, None)
        if len(_cache) > YOUTUBE_META_MAX:
            _cache.clear()

    result: List[Dict] = []
    try:
        data = await VideosSearch(query, limit=1).next()
        result = data.get("result", []) or []
    except Exception:
        result = []

    # Fallback: yt-dlp search if shim empty
    if not result:
        info = await _ytdlp_dump_json(f"ytsearch1:{query}", flat=True)
        if info:
            result = [info]

    if result:
        async with _cache_lock:
            _cache[key] = (now, result)

    return result


# === Main Class ===
class YouTubeAPI:
    def __init__(self) -> None:
        self.base_url = "https://www.youtube.com/watch?v="
        self.playlist_url = "https://youtube.com/playlist?list="
        self._url_pattern = re.compile(r"(?:youtube\.com|youtu\.be)")

    def _prepare_link(self, link: str, videoid: Union[str, bool, None] = None) -> str:
        if isinstance(videoid, str) and videoid.strip():
            link = self.base_url + videoid.strip()

        link = (link or "").strip()

        # bare 11-char video id
        if YOUTUBE_ID_RE.fullmatch(link):
            return self.base_url + link

        if "youtu.be" in link:
            link = self.base_url + link.split("/")[-1].split("?")[0]
        elif "youtube.com/shorts/" in link or "youtube.com/live/" in link:
            link = self.base_url + link.split("/")[-1].split("?")[0]
        elif "youtube.com/watch" in link and "v=" in link:
            vid = link.split("v=")[-1].split("&")[0].split("#")[0]
            if YOUTUBE_ID_RE.fullmatch(vid):
                return self.base_url + vid

        return link.split("&")[0]

    # === URL Handling ===
    @capture_internal_err
    async def exists(self, link: str, videoid: Union[str, bool, None] = None) -> bool:
        return bool(self._url_pattern.search(self._prepare_link(link, videoid)))

    @capture_internal_err
    async def url(self, message: Message) -> Optional[str]:
        msgs = [message] + ([message.reply_to_message] if message.reply_to_message else [])
        for msg in msgs:
            text = msg.text or msg.caption or ""
            entities = (msg.entities or []) + (msg.caption_entities or [])
            for ent in entities:
                if ent.type == MessageEntityType.URL:
                    return text[ent.offset: ent.offset + ent.length].split("&si")[0]
                if ent.type == MessageEntityType.TEXT_LINK:
                    return ent.url.split("&si")[0]
        return None

    async def _ensure_watch_url(self, maybe_query_or_url: str) -> Optional[str]:
        prepared = self._prepare_link(maybe_query_or_url)
        if prepared.startswith("http") and self._url_pattern.search(prepared):
            return prepared
        if prepared.startswith("http"):
            return prepared
        data = await cached_youtube_search(prepared)
        if not data:
            return None
        vid = data[0].get("id")
        return self.base_url + vid if vid else None

    # === Metadata Fetching ===
    @capture_internal_err
    async def _fetch_video_info(self, query: str, *, use_cache: bool = True) -> Optional[Dict]:
        q = self._prepare_link(query)
        # Direct watch URL / id → yt-dlp first (reliable with cookies)
        if q.startswith("http") or YOUTUBE_ID_RE.fullmatch(q.replace(self.base_url, "")):
            target = q if q.startswith("http") else self.base_url + q
            info = await _ytdlp_dump_json(target)
            if info:
                return info
            # last resort search shim
            try:
                data = await VideosSearch(target, limit=1).next()
                res = data.get("result") or []
                return res[0] if res else None
            except Exception:
                return None

        # Text query
        if use_cache:
            res = await cached_youtube_search(q)
            return res[0] if res else None
        data = await VideosSearch(q, limit=1).next()
        result = data.get("result", [])
        return result[0] if result else None

    @capture_internal_err
    async def is_live(self, link: str) -> bool:
        prepared = self._prepare_link(link)
        info = await _ytdlp_dump_json(prepared)
        return bool(info and info.get("is_live"))

    @capture_internal_err
    async def details(
        self, link: str, videoid: Union[str, bool, None] = None
    ) -> Tuple[str, Optional[str], int, str, str]:
        prepared_link = self._prepare_link(link, videoid)
        info = await self._fetch_video_info(prepared_link)
        if not info:
            raise ValueError("Video not found")

        dt = info.get("duration")
        if isinstance(dt, (int, float)):
            total = int(dt)
            dt_str = f"{total // 60}:{total % 60:02d}"
            ds = total
        else:
            dt_str = dt if isinstance(dt, str) else None
            ds = int(time_to_seconds(dt_str)) if dt_str else 0
        thumbs = info.get("thumbnails") or [{}]
        thumb = (
            info.get("thumbnail")
            or (thumbs[-1].get("url") if thumbs else "")
            or ""
        )
        thumb = str(thumb).split("?")[0]
        vid = info.get("id", "")
        if not thumb and vid:
            from config import youtube_thumb
            thumb = youtube_thumb(str(vid))

        return info.get("title", ""), dt_str, ds, thumb, vid

    @capture_internal_err
    async def title(self, link: str, videoid: Union[str, bool, None] = None) -> str:
        info = await self._fetch_video_info(self._prepare_link(link, videoid))
        return info.get("title", "") if info else ""

    @capture_internal_err
    async def duration(self, link: str, videoid: Union[str, bool, None] = None) -> Optional[str]:
        info = await self._fetch_video_info(self._prepare_link(link, videoid))
        return info.get("duration") if info else None

    @capture_internal_err
    async def thumbnail(self, link: str, videoid: Union[str, bool, None] = None) -> str:
        from config import youtube_thumb

        prepared = self._prepare_link(link, videoid)
        info = await self._fetch_video_info(prepared)
        thumb = ""
        if info:
            thumbs = info.get("thumbnails") or [{}]
            thumb = (
                info.get("thumbnail")
                or (thumbs[-1].get("url") if thumbs else "")
                or ""
            )
            thumb = str(thumb).split("?")[0]
            if thumb and "telegra.ph/file/2c6d1a6f78eba6199933a" not in thumb:
                return thumb
            vid = info.get("id") or ""
            if len(str(vid)) == 11:
                return youtube_thumb(str(vid))
        # videoid direct / extract from prepared link
        if isinstance(videoid, str) and len(videoid.strip()) == 11:
            return youtube_thumb(videoid.strip())
        m = YOUTUBE_ID_RE.search(prepared.replace("https://www.youtube.com/watch?v=", ""))
        if m:
            return youtube_thumb(m.group(0))
        if prepared.startswith(self.base_url):
            vid = prepared.replace(self.base_url, "")[:11]
            if len(vid) == 11:
                return youtube_thumb(vid)
        return ""

    @capture_internal_err
    async def track(self, link: str, videoid: Union[str, bool, None] = None) -> Tuple[Dict, str]:
        prepared_link = self._prepare_link(link, videoid)

        info = await self._fetch_video_info(prepared_link)
        if not info:
            # Explicit yt-dlp fallback for text queries / edge cases
            target = (
                prepared_link
                if prepared_link.startswith("http")
                else f"ytsearch1:{prepared_link}"
            )
            info = await _ytdlp_dump_json(
                target, flat=not prepared_link.startswith("http")
            )
        if not info:
            raise ValueError(f"Could not resolve track: {prepared_link}")

        thumbs = info.get("thumbnails") or [{}]
        thumb_raw = info.get("thumbnail") or (thumbs[-1].get("url") if thumbs else "") or ""
        thumb = str(thumb_raw).split("?")[0]
        vidid = info.get("id", "") or info.get("vidid", "")
        link_out = (
            info.get("webpage_url")
            or info.get("link")
            or info.get("url")
            or (self.base_url + vidid if vidid else prepared_link)
        )
        if link_out and not str(link_out).startswith("http") and vidid:
            link_out = self.base_url + vidid
        if not thumb and vidid:
            from config import youtube_thumb
            thumb = youtube_thumb(str(vidid))
        dur = info.get("duration")
        if isinstance(dur, (int, float)):
            total = int(dur)
            duration_min = f"{total // 60}:{total % 60:02d}"
        else:
            duration_min = dur if isinstance(dur, str) else None

        details = {
            "title": info.get("title", ""),
            "link": link_out,
            "vidid": vidid,
            "duration_min": duration_min,
            "thumb": thumb,
        }
        return details, vidid

    # === Media & Formats ===
    @capture_internal_err
    async def video(self, link: str, videoid: Union[str, bool, None] = None) -> Tuple[int, str]:
        link = self._prepare_link(link, videoid)
        # Prefer audio-capable progressive / bestaudio for VC
        for fmt in (
            "bestaudio[ext=m4a]/bestaudio/best",
            "best[height<=?720][width<=?1280]/best",
        ):
            stdout, stderr = await _exec_proc(
                "yt-dlp",
                *(_cookies_args()),
                *(_js_runtime_args()),
                "-g",
                "-f",
                fmt,
                "--no-warnings",
                "--no-update",
                link,
            )
            if stdout:
                url = stdout.decode().strip().split("\n")[0].strip()
                if url.startswith("http"):
                    return 1, url
        return 0, (stderr.decode() if stderr else "no stream url")

    @capture_internal_err
    async def playlist(
        self, link: str, limit: int, user_id, videoid: Union[str, bool, None] = None
    ) -> List[str]:
        if videoid:
            link = self.playlist_url + str(videoid)
        link = self._prepare_link(link).split("&")[0]

        try:
            plist = await Playlist.get(link)
            items = [video.get("id") for video in plist.get("videos", [])[:limit] if video.get("id")]
            if items:
                return items
        except Exception:
            pass

        stdout, _ = await _exec_proc(
            "yt-dlp",
            *(_cookies_args()), *(_js_runtime_args()),
            "-i",
            "--get-id",
            "--flat-playlist",
            "--playlist-end",
            str(limit),
            "--skip-download",
            link,
        )
        items = stdout.decode().strip().split("\n") if stdout else []
        return [i for i in items if i]

    @capture_internal_err
    async def formats(
        self, link: str, videoid: Union[str, bool, None] = None
    ) -> Tuple[List[Dict], str]:
        link = self._prepare_link(link, videoid)
        key = f"f:{link}"
        now = time.time()

        async with _formats_lock:
            cached = _formats_cache.get(key)
            if cached and now - cached[0] < YOUTUBE_META_TTL:
                return cached[1], cached[2]

        opts = {"quiet": True}
        if cf := _cookiefile_path():
            opts["cookiefile"] = cf

        out: List[Dict] = []
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(link, download=False)
                for fmt in info.get("formats", []):
                    if "dash" in str(fmt.get("format", "")).lower():
                        continue
                    if not any(k in fmt for k in ("filesize", "filesize_approx")):
                        continue
                    if not all(k in fmt for k in ("format", "format_id", "ext", "format_note")):
                        continue
                    size = fmt.get("filesize") or fmt.get("filesize_approx")
                    if not size:
                        continue
                    out.append(
                        {
                            "format": fmt["format"],
                            "filesize": size,
                            "format_id": fmt["format_id"],
                            "ext": fmt["ext"],
                            "format_note": fmt["format_note"],
                            "yturl": link,
                        }
                    )
        except Exception:
            pass

        async with _formats_lock:
            if len(_formats_cache) > YOUTUBE_META_MAX:
                _formats_cache.clear()
            _formats_cache[key] = (now, out, link)

        return out, link

    @capture_internal_err
    async def slider(
        self, link: str, query_type: int, videoid: Union[str, bool, None] = None
    ) -> Tuple[str, Optional[str], str, str]:
        data = await VideosSearch(self._prepare_link(link, videoid), limit=10).next()
        results = data.get("result", [])
        if not results or query_type >= len(results):
            raise IndexError(
                f"Query type index {query_type} out of range (found {len(results)} results)"
            )
        r = results[query_type]
        return (
            r.get("title", ""),
            r.get("duration"),
            r.get("thumbnails", [{}])[-1].get("url", "").split("?")[0],
            r.get("id", ""),
        )

    @capture_internal_err
    async def download(
        self,
        link: str,
        mystic,
        *,
        video: Union[bool, str, None] = None,
        videoid: Union[str, bool, None] = None,
    ) -> Union[Tuple[str, Optional[bool]], Tuple[None, None]]:
        link = self._prepare_link(link, videoid)

        if video:
            if await self.is_live(link):
                status, stream_url = await self.video(link)
                if status == 1:
                    return stream_url, None
                return None, None

            if await is_on_off(1):
                p = await yt_dlp_download(link, type="video", title=await self.title(link))
                return (p, True) if p else (None, None)

            stdout, _ = await _exec_proc(
                "yt-dlp",
                *(_cookies_args()), *(_js_runtime_args()),
                "-g",
                "-f",
                "best[height<=?720][width<=?1280]",
                link,
            )
            if stdout:
                return stdout.decode().split("\n")[0], None
            return None, None

        p = await yt_dlp_download(link, type="audio", title=await self.title(link))
        if p:
            return p, True
        # Prefer direct stream URL when file download is blocked (cookies)
        status, stream_url = await self.video(link)
        if status == 1 and stream_url:
            return stream_url, None
        return None, None
