# Authored By Iota Coders © 2025
from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGO_DB_URI
from ..logging import LOGGER

LOGGER(__name__).info("Connecting to your Mongo Database...")

_mongo_async_ = None
mongodb = None

try:
    if not MONGO_DB_URI or "<db_password>" in (MONGO_DB_URI or ""):
        raise RuntimeError("MONGO_DB_URI missing or still has password placeholder")
    _mongo_async_ = AsyncIOMotorClient(MONGO_DB_URI, serverSelectionTimeoutMS=12500)
    mongodb = _mongo_async_.Iota
    LOGGER(__name__).info("Connected to your Mongo Database.")
except Exception as e:
    LOGGER(__name__).error(f"Failed to connect to your Mongo Database: {e}")
    LOGGER(__name__).error(
        "Bot cannot start without MongoDB. Fix MONGO_DB_URI in .env and restart."
    )
    raise SystemExit(1) from e
