# Authored By Iota Coders © 2025
import asyncio
import re
from typing import Any, List, Tuple

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from IotaXMedia.platforms.ytsearch import VideosSearch

import config


class SpotifyAPI:
    def __init__(self):
        self.regex = r"^https:\/\/open\.spotify\.com\/.+"
        self.client_id = config.SPOTIFY_CLIENT_ID
        self.client_secret = config.SPOTIFY_CLIENT_SECRET
        if self.client_id and self.client_secret:
            self.client_credentials_manager = SpotifyClientCredentials(
                self.client_id, self.client_secret
            )
            self.spotify = spotipy.Spotify(
                client_credentials_manager=self.client_credentials_manager
            )
        else:
            self.spotify = None

    async def valid(self, link: str) -> bool:
        return bool(re.search(self.regex, link or ""))

    async def _run(self, fn, *args, **kwargs) -> Any:
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def track(self, link: str):
        if not self.spotify:
            raise RuntimeError("Spotify credentials not configured")
        track = await self._run(self.spotify.track, link)
        info = track["name"]
        for artist in track["artists"]:
            fetched = f' {artist["name"]}'
            if "Various Artists" not in fetched:
                info += fetched
        results = VideosSearch(info, limit=1)
        data = await results.next()
        items = data.get("result") or []
        if not items:
            raise RuntimeError("No YouTube match for Spotify track")
        r = items[0]
        track_details = {
            "title": r.get("title", info),
            "link": r.get("link", ""),
            "vidid": r.get("id", ""),
            "duration_min": r.get("duration"),
            "thumb": (r.get("thumbnails") or [{}])[0].get("url", "").split("?")[0],
        }
        return track_details, track_details["vidid"]

    async def playlist(self, url) -> Tuple[List[str], str]:
        if not self.spotify:
            raise RuntimeError("Spotify credentials not configured")
        playlist = await self._run(self.spotify.playlist, url)
        playlist_id = playlist["id"]
        results = []
        for item in playlist["tracks"]["items"]:
            music_track = item.get("track") or {}
            info = music_track.get("name") or ""
            for artist in music_track.get("artists") or []:
                fetched = f' {artist["name"]}'
                if "Various Artists" not in fetched:
                    info += fetched
            if info:
                results.append(info)
        return results, playlist_id

    async def album(self, url) -> Tuple[List[str], str]:
        if not self.spotify:
            raise RuntimeError("Spotify credentials not configured")
        album = await self._run(self.spotify.album, url)
        album_id = album["id"]
        results = []
        for item in album["tracks"]["items"]:
            info = item.get("name") or ""
            for artist in item.get("artists") or []:
                fetched = f' {artist["name"]}'
                if "Various Artists" not in fetched:
                    info += fetched
            if info:
                results.append(info)
        return results, album_id

    async def artist(self, url) -> Tuple[List[str], str]:
        if not self.spotify:
            raise RuntimeError("Spotify credentials not configured")
        artistinfo = await self._run(self.spotify.artist, url)
        artist_id = artistinfo["id"]
        results = []
        artisttoptracks = await self._run(self.spotify.artist_top_tracks, url)
        for item in artisttoptracks.get("tracks") or []:
            info = item.get("name") or ""
            for artist in item.get("artists") or []:
                fetched = f' {artist["name"]}'
                if "Various Artists" not in fetched:
                    info += fetched
            if info:
                results.append(info)
        return results, artist_id