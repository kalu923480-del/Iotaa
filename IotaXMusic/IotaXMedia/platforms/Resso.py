# Authored By Iota Coders © 2025
import re
from typing import Optional, Union

import aiohttp
from bs4 import BeautifulSoup
from IotaXMedia.platforms.ytsearch import VideosSearch


class RessoAPI:
    def __init__(self):
        self.regex = r"^(https:\/\/m.resso.com\/)(.*)$"
        self.base = "https://m.resso.com/"

    async def valid(self, link: str):
        return bool(re.search(self.regex, link or ""))

    async def track(self, url, playid: Union[bool, str] = None):
        if playid:
            url = self.base + url
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return False
                html = await response.text()

        soup = BeautifulSoup(html, "html.parser")
        title: Optional[str] = None
        des: Optional[str] = None
        for tag in soup.find_all("meta"):
            prop = tag.get("property")
            if prop == "og:title":
                title = tag.get("content")
            if prop == "og:description":
                des = tag.get("content") or ""
                try:
                    des = des.split("·")[0]
                except Exception:
                    pass

        query = (title or des or "").strip()
        if not query:
            return False

        data = await VideosSearch(query, limit=1).next()
        results = data.get("result") or []
        if not results:
            return False

        result = results[0]
        track_details = {
            "title": result.get("title", query),
            "link": result.get("link", ""),
            "vidid": result.get("id", ""),
            "duration_min": result.get("duration"),
            "thumb": (result.get("thumbnails") or [{}])[0].get("url", "").split("?")[0],
        }
        return track_details, track_details["vidid"]