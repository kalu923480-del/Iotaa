# Authored By Iota Coders © 2025
import asyncio
import os

CPU = os.cpu_count() or 4
MAX_CONCURRENT = min(64, CPU * 8)
CHUNK_SIZE = 128 * 1024
# yt-dlp + deno n-sig can exceed 30s on cold start
YTDLP_TIMEOUT = int(os.environ.get("YTDLP_TIMEOUT", "90"))
YOUTUBE_META_TTL = 600
YOUTUBE_META_MAX = 2048
SEM = asyncio.Semaphore(MAX_CONCURRENT)
