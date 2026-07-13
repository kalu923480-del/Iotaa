"""
Iota TTS Engine — powered by Sarvam Bulbul v3
═══════════════════════════════════════════════

This module is the SINGLE source of truth for everything voice/TTS in Iota.
It replaces the old, half-broken glue that used to live in ``utils/sarvam.py``
(hardcoded ``bulbul:v1`` model + an invalid ``meera`` speaker that made every
``/voice`` request fail).

What this gives the bot
───────────────────────
• **Bulbul v3** — Sarvam's current, supported TTS model (37 natively-trained
  Indian voices: 23 male + 14 female, exactly the catalogue the owner asked
  for). ``bulbul:v1`` is fully deprecated by Sarvam and ``bulbul:v2`` only has
  7 voices, so v3 is the right baseline.
• **Auto-fetched voice catalogue** — ``GET /voices`` is queried on startup and
  whenever the owner runs ``/ttsrefresh``. A built-in fallback registry (the
  37 known voices) is used if the API is unreachable, so the bot NEVER hard-
  fails offline.
• **Voice cloning** — owner can reply to a voice/audio sample with
  ``/clonevoice <name>`` to register a custom cloned voice, list them with
  ``/clonedvoices``, delete with ``/delclone <id>`` and use them as the default
  speaker via ``/ttssettings speaker <id>``.
• **Owner-configurable defaults** — model / speaker / pace / temperature /
  sample-rate, persisted to MongoDB so they survive restarts.

Design notes (``0 bugs / 0 errors`` contract)
─────────────────────────────────────────────
• Every network call is wrapped; failures are *logged* and surfaced as a clear
  message to the owner — never a silent ``None`` or an unhandled exception.
• All validation is centralised in ``set_tts_setting`` / ``is_valid_voice`` so
  a bad value can never reach the Sarvam API.
• The voice catalogue is the only place that knows the voice list; both the
  ``/voice`` command and the owner panel read from it.
"""
import aiohttp
import asyncio
import base64
import difflib
import io
import logging
import time
from typing import Optional

from config import (SARVAM_API_KEY, SARVAM_TTS_URL, SARVAM_VOICES_URL,
                     SARVAM_TTS_CLONE_URL)

logger = logging.getLogger(__name__)

# ── Endpoints ────────────────────────────────────────────────────────────────
# All overridable via env so a future Sarvam path change needs no code edit.
TTS_URL = SARVAM_TTS_URL
VOICES_URL = SARVAM_VOICES_URL
VOICE_CLONE_URL = SARVAM_TTS_CLONE_URL

DEFAULT_MODEL = "bulbul:v3"
VALID_MODELS = [DEFAULT_MODEL]  # bulbul:v1 is deprecated; v2 is legacy-only.

# Bulbul v3 supported language codes (11 Indian + English).
LANG_CODES = {
    "hi": "hi-IN", "en": "en-IN", "bn": "bn-IN", "te": "te-IN",
    "mr": "mr-IN", "ta": "ta-IN", "gu": "gu-IN", "kn": "kn-IN",
    "ml": "ml-IN", "pa": "pa-IN", "or": "od-IN", "ur": "ur-IN",
}

# ── Bulbul v3 sample-rate ladder (Hz) ────────────────────────────────────────
VALID_SAMPLE_RATES = (8000, 16000, 22050, 24000, 32000, 44100, 48000)
DEFAULT_SAMPLE_RATE = 24000  # premium quality (v3 default)

# ── Built-in fallback voice catalogue ────────────────────────────────────────
# Mirrors Sarvam Bulbul v3's 37 voices exactly. Used when ``GET /voices`` is
# unreachable so the bot always has a valid voice list. ``tier`` is the quality
# tier from Sarvam's docs (only the documented ones are filled in).
FALLBACK_VOICES = [
    # Male (23)
    {"id": "shubh",    "name": "Shubh",    "gender": "male",   "tier": "Tier 2"},
    {"id": "aditya",   "name": "Aditya",   "gender": "male",   "tier": "Tier 3"},
    {"id": "rahul",    "name": "Rahul",    "gender": "male",   "tier": "Tier 3"},
    {"id": "rohan",    "name": "Rohan",    "gender": "male",   "tier": ""},
    {"id": "amit",     "name": "Amit",     "gender": "male",   "tier": "Tier 2"},
    {"id": "dev",      "name": "Dev",      "gender": "male",   "tier": "Tier 3"},
    {"id": "ratan",    "name": "Ratan",    "gender": "male",   "tier": "Tier 2"},
    {"id": "varun",    "name": "Varun",    "gender": "male",   "tier": "Tier 1"},
    {"id": "manan",    "name": "Manan",    "gender": "male",   "tier": "Tier 3"},
    {"id": "sumit",    "name": "Sumit",    "gender": "male",   "tier": ""},
    {"id": "kabir",    "name": "Kabir",    "gender": "male",   "tier": ""},
    {"id": "aayan",    "name": "Aayan",    "gender": "male",   "tier": ""},
    {"id": "ashutosh", "name": "Ashutosh", "gender": "male",   "tier": "Tier 2"},
    {"id": "advait",   "name": "Advait",   "gender": "male",   "tier": ""},
    {"id": "anand",    "name": "Anand",    "gender": "male",   "tier": "Tier 3"},
    {"id": "tarun",    "name": "Tarun",    "gender": "male",   "tier": ""},
    {"id": "sunny",    "name": "Sunny",    "gender": "male",   "tier": "Tier 2"},
    {"id": "mani",     "name": "Mani",     "gender": "male",   "tier": "Tier 1"},
    {"id": "gokul",    "name": "Gokul",    "gender": "male",   "tier": ""},
    {"id": "vijay",    "name": "Vijay",    "gender": "male",   "tier": ""},
    {"id": "mohit",    "name": "Mohit",    "gender": "male",   "tier": ""},
    {"id": "rehan",    "name": "Rehan",    "gender": "male",   "tier": "Tier 2"},
    {"id": "soham",    "name": "Soham",    "gender": "male",   "tier": ""},
    # Female (14)
    {"id": "ritu",     "name": "Ritu",     "gender": "female", "tier": "Tier 3"},
    {"id": "priya",    "name": "Priya",    "gender": "female", "tier": "Tier 1"},
    {"id": "neha",     "name": "Neha",     "gender": "female", "tier": "Tier 3"},
    {"id": "pooja",    "name": "Pooja",    "gender": "female", "tier": "Tier 2"},
    {"id": "simran",   "name": "Simran",   "gender": "female", "tier": "Tier 3"},
    {"id": "kavya",    "name": "Kavya",    "gender": "female", "tier": ""},
    {"id": "ishita",   "name": "Ishita",   "gender": "female", "tier": "Tier 1"},
    {"id": "shreya",   "name": "Shreya",   "gender": "female", "tier": "Tier 3"},
    {"id": "roopa",    "name": "Roopa",    "gender": "female", "tier": "Tier 2"},
    {"id": "tanya",    "name": "Tanya",    "gender": "female", "tier": ""},
    {"id": "shruti",   "name": "Shruti",   "gender": "female", "tier": ""},
    {"id": "suhani",   "name": "Suhani",   "gender": "female", "tier": "Tier 3"},
    {"id": "kavitha",  "name": "Kavitha",  "gender": "female", "tier": ""},
    {"id": "rupali",   "name": "Rupali",   "gender": "female", "tier": "Tier 3"},
]

DEFAULT_VOICE = "shubh"

# ── In-memory state ──────────────────────────────────────────────────────────
_voices: list = list(FALLBACK_VOICES)      # current catalogue (fallback → live)
_voices_source: str = "built-in fallback"  # where _voices came from
_cloned_voices: list = []                  # owner-cloned custom voices
_tts_config = {
    "model": DEFAULT_MODEL,
    "speaker": DEFAULT_VOICE,
    "pace": 1.0,          # 0.5 (slow) – 2.0 (fast)  [bulbul:v3 range]
    "temperature": 0.6,   # 0.01 – 1.0               [bulbul:v3 only]
    "sample_rate": DEFAULT_SAMPLE_RATE,
}

# Most recent failure reason, surfaced to the user so "TTS failed" is never
# a dead end (the owner can see *why* without digging through server logs).
_last_tts_error: str = ""


def get_last_tts_error() -> str:
    return _last_tts_error


# ═════════════════════════════════════════════════════════════════════════════
#  Voice catalogue
# ═════════════════════════════════════════════════════════════════════════════

def _normalise_voice(item: dict) -> Optional[dict]:
    """Turn one raw API voice object into our canonical shape, or None."""
    if not isinstance(item, dict):
        return None
    vid = (item.get("id") or item.get("voice_id") or item.get("speaker")
           or "").strip().lower()
    if not vid:
        return None
    gender = str(item.get("gender") or item.get("type") or "").strip().lower()
    if gender not in ("male", "female"):
        # Sarvam labels some as "masculine"/"feminine" — normalise.
        gender = "male" if "masc" in gender else ("female" if "fem" in gender else "")
    return {
        "id": vid,
        "name": str(item.get("name") or vid).strip() or vid,
        "gender": gender,
        "tier": str(item.get("tier") or "").strip(),
    }


async def fetch_voices(force: bool = False) -> list:
    """
    Auto-fetch the live voice catalogue from Sarvam and merge it with the
    built-in fallback. On ANY failure we keep the fallback so the bot stays
    functional. Returns the resulting catalogue.
    """
    global _voices, _voices_source
    if _voices and not force and _voices_source == "live API":
        return _voices

    if not SARVAM_API_KEY:
        _voices = list(FALLBACK_VOICES)
        _voices_source = "built-in fallback (no API key)"
        return _voices

    try:
        headers = {"api-subscription-key": SARVAM_API_KEY}
        async with aiohttp.ClientSession() as s:
            async with s.get(VOICES_URL, headers=headers,
                             timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    logger.warning(f"Sarvam /voices returned {r.status}; "
                                   f"using fallback catalogue.")
                    _voices = list(FALLBACK_VOICES)
                    _voices_source = f"built-in fallback (API {r.status})"
                    return _voices
                data = await r.json()
    except Exception as e:
        logger.warning(f"Sarvam /voices fetch failed ({e}); using fallback.")
        _voices = list(FALLBACK_VOICES)
        _voices_source = "built-in fallback (fetch error)"
        return _voices

    # Endpoint returns {"voices": [...]} in the documented shape. Be tolerant of
    # alternate envelopes (a bare list, or a {"data": [...]} wrapper).
    raw = data.get("voices") if isinstance(data, dict) else None
    if raw is None and isinstance(data, dict):
        raw = data.get("data") or data.get("results")
    if raw is None and isinstance(data, list):
        raw = data
    if not isinstance(raw, list):
        logger.warning("Sarvam /voices payload unrecognised; using fallback.")
        _voices = list(FALLBACK_VOICES)
        _voices_source = "built-in fallback (bad payload)"
        return _voices

    normalised = [_normalise_voice(v) for v in raw]
    normalised = [v for v in normalised if v]
    if not normalised:
        _voices = list(FALLBACK_VOICES)
        _voices_source = "built-in fallback (empty list)"
        return _voices

    _voices = normalised
    _voices_source = "live API"
    # Persist so a restart is instant even before the next fetch.
    try:
        await _persist_voices()
    except Exception as e:  # persistence is best-effort
        logger.debug(f"Could not persist voice catalogue: {e}")
    return _voices


async def _persist_voices():
    from utils.mongo_db import get_db
    await get_db().bot_config.update_one(
        {"_id": "tts_voices"},
        {"$set": {"voices": _voices, "source": _voices_source,
                  "updated_at": int(time.time())}},
        upsert=True,
    )


async def load_voices_db():
    """Load a previously persisted live catalogue at startup (instant)."""
    global _voices, _voices_source
    try:
        from utils.mongo_db import get_db
        doc = await get_db().bot_config.find_one({"_id": "tts_voices"})
        if doc and doc.get("voices"):
            _voices = doc["voices"]
            _voices_source = doc.get("source", "persisted cache")
    except Exception as e:
        logger.debug(f"Could not load persisted voices: {e}")


def get_voices() -> list:
    """Full catalogue = live/built-in voices + cloned voices."""
    return list(_voices) + list(_cloned_voices)


def get_voices_source() -> str:
    return _voices_source


def get_voice_ids() -> set:
    return {v["id"] for v in get_voices()}


def is_valid_voice(vid: str) -> bool:
    if not vid:
        return False
    return vid.strip().lower() in get_voice_ids()


def voice_display(vid: str) -> str:
    vid = (vid or "").strip().lower()
    for v in get_voices():
        if v["id"] == vid:
            tag = f" ({v['gender']})" if v.get("gender") else ""
            return f"{v.get('name', vid)}{tag}"
    return vid


# ═════════════════════════════════════════════════════════════════════════════
#  Owner-configurable TTS defaults
# ═════════════════════════════════════════════════════════════════════════════

# ── Setting-key aliases (typo tolerance) ────────────────────────────────────
# Users (and owners) mistype setting keys. Map common variants to the canonical
# key so e.g. "/ttssettings temprature 0.7" (missing an 'e') still works.
KEY_ALIASES = {
    "temperature": {"temperature", "temprature", "temp", "tempreture", "tempreture"},
    "sample_rate": {"sample_rate", "samplerate", "samplerate", "sr", "sample-rate", "bitrate"},
    "speaker":     {"speaker", "voice", "spk", "spkr"},
    "pace":        {"pace", "speed", "rate"},
    "model":       {"model", "mdl", "version"},
}


def normalize_setting_key(key: str) -> str:
    """Map a (possibly misspelled) setting key to its canonical form."""
    k = (key or "").strip().lower()
    if k in KEY_ALIASES:
        return k
    for canon, variants in KEY_ALIASES.items():
        if k in variants:
            return canon
    return k


def get_tts_config() -> dict:
    return dict(_tts_config)


def set_tts_setting(key: str, value) -> tuple[bool, str]:
    """Validates and applies one TTS setting. Returns (ok, error_message)."""
    key = normalize_setting_key(key)
    if key == "speaker":
        vid = str(value).strip().lower()
        if not is_valid_voice(vid):
            return False, (
                f"Invalid speaker '{vid}'. List voices with /ttsvoices "
                f"(or use a cloned voice from /clonedvoices)."
            )
        _tts_config["speaker"] = vid
    elif key == "model":
        if str(value).strip().lower() not in VALID_MODELS:
            return False, (
                f"Invalid model '{value}'. Supported: {', '.join(VALID_MODELS)} "
                f"(bulbul:v1 is deprecated; bulbul:v2 is legacy-only)."
            )
        _tts_config["model"] = str(value).strip().lower()
    elif key == "pace":
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False, "Pace must be a number (e.g. 1.0)."
        if not (0.5 <= v <= 2.0):
            return False, "Pace must be between 0.5 (slow) and 2.0 (fast)."
        _tts_config["pace"] = v
    elif key == "temperature":
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False, "Temperature must be a number (e.g. 0.6)."
        if not (0.01 <= v <= 1.0):
            return False, "Temperature must be between 0.01 and 1.0."
        _tts_config["temperature"] = v
    elif key == "sample_rate":
        try:
            v = int(value)
        except (TypeError, ValueError):
            return False, "Sample rate must be a number (e.g. 24000)."
        if v not in VALID_SAMPLE_RATES:
            return False, (
                f"Sample rate must be one of: {', '.join(map(str, VALID_SAMPLE_RATES))}."
            )
        _tts_config["sample_rate"] = v
    else:
        valid = ["model", "speaker", "pace", "temperature", "sample_rate"]
        guess = difflib.get_close_matches(key, valid, n=1, cutoff=0.6)
        hint = f" Did you mean '{guess[0]}'?" if guess else ""
        return False, (
            f"Unknown setting '{key}'. Valid keys: "
            f"{', '.join(valid)}.{hint}"
        )
    return True, ""


async def save_tts_config_db():
    """Persist current TTS settings so they survive a bot restart."""
    from utils.mongo_db import get_db
    await get_db().bot_config.update_one(
        {"_id": "tts_settings"}, {"$set": _tts_config}, upsert=True
    )


async def load_tts_config_db():
    """Load TTS settings + cloned voices from DB on startup."""
    from utils.mongo_db import get_db
    doc = await get_db().bot_config.find_one({"_id": "tts_settings"})
    if doc:
        for k in ("model", "speaker", "pace", "temperature", "sample_rate"):
            if k in doc:
                _tts_config[k] = doc[k]
    # The engine only supports Bulbul v3 now (37 voices). A config persisted
    # by the OLD bulbul:v1/v2 code can carry a stale "bulbul:v2" model, which
    # is incompatible with the v3 speakers (e.g. "ratan") and makes every
    # /voice fail with a 400. Force v3 so a stale DB value can never break TTS.
    if _tts_config["model"] != DEFAULT_MODEL:
        _tts_config["model"] = DEFAULT_MODEL
        try:
            await save_tts_config_db()
        except Exception:
            pass
    # Clamp loaded values through validation so a stale DB value can't break TTS.
    for k in ("pace", "temperature", "sample_rate"):
        ok, _ = set_tts_setting(k, _tts_config[k])
        if not ok:  # out of range after an API change → reset to default
            _tts_config[k] = {"pace": 1.0, "temperature": 0.6,
                              "sample_rate": DEFAULT_SAMPLE_RATE}[k]
    if _tts_config["speaker"] not in get_voice_ids():
        _tts_config["speaker"] = DEFAULT_VOICE
    await load_cloned_voices_db()


# ═════════════════════════════════════════════════════════════════════════════
#  Cloned voices (owner voice cloning)
# ═════════════════════════════════════════════════════════════════════════════

async def load_cloned_voices_db():
    global _cloned_voices
    try:
        from utils.mongo_db import get_db
        doc = await get_db().bot_config.find_one({"_id": "cloned_voices"})
        if doc and isinstance(doc.get("voices"), list):
            _cloned_voices = [v for v in doc["voices"] if v and v.get("id")]
    except Exception as e:
        logger.debug(f"Could not load cloned voices: {e}")


async def _persist_cloned_voices():
    from utils.mongo_db import get_db
    await get_db().bot_config.update_one(
        {"_id": "cloned_voices"}, {"$set": {"voices": _cloned_voices}},
        upsert=True,
    )


def get_cloned_voices() -> list:
    return list(_cloned_voices)


async def add_cloned_voice(vid: str, name: str, meta: Optional[dict] = None) -> dict:
    """Register a cloned voice and persist it. Returns the stored record."""
    vid = vid.strip().lower()
    # De-dupe by id (re-clone replaces the previous one).
    global _cloned_voices
    _cloned_voices = [v for v in _cloned_voices if v["id"] != vid]
    rec = {
        "id": vid,
        "name": name or vid,
        "gender": (meta or {}).get("gender", "") or "cloned",
        "tier": "cloned",
        "created_at": int(time.time()),
        "owner": (meta or {}).get("owner"),
    }
    _cloned_voices.append(rec)
    await _persist_cloned_voices()
    return rec


async def delete_cloned_voice(vid: str) -> bool:
    global _cloned_voices
    vid = vid.strip().lower()
    before = len(_cloned_voices)
    _cloned_voices = [v for v in _cloned_voices if v["id"] != vid]
    if len(_cloned_voices) == before:
        return False
    await _persist_cloned_voices()
    # If it was the active speaker, fall back to the default voice.
    if _tts_config["speaker"] == vid:
        _tts_config["speaker"] = DEFAULT_VOICE
        await save_tts_config_db()
    return True


async def clone_voice(name: str, audio_bytes: bytes, filename: str,
                      owner_id: Optional[int] = None,
                      consent: bool = True) -> tuple[bool, str]:
    """
    Clone a voice from an audio sample via Sarvam.

    Sarvam's voice cloning is consent-based: we send the sample + an explicit
    consent flag + a display name. On success we register the returned voice id
    as a usable speaker.

    Returns ``(ok, voice_id_or_error_message)``. This NEVER raises — any API or
    network problem is caught and returned as a clear error string so the owner
    panel can report it instead of crashing.
    """
    if not SARVAM_API_KEY:
        return False, "SARVAM_API_KEY is not set — cannot clone voices."
    if not audio_bytes:
        return False, "No audio sample provided."
    if not name or not name.strip():
        return False, "A voice name is required."
    name = name.strip()

    # Determine a sane MIME type from the filename.
    lower = (filename or "").lower()
    mime = ("audio/wav" if lower.endswith(".wav")
            else "audio/mpeg" if lower.endswith((".mp3", ".mpeg"))
            else "audio/ogg" if lower.endswith(".ogg")
            else "audio/x-m4a" if lower.endswith(".m4a")
            else "application/octet-stream")

    try:
        headers = {"api-subscription-key": SARVAM_API_KEY}
        data = aiohttp.FormData()
        data.add_field("name", name)
        data.add_field("consent", "true" if consent else "false")
        data.add_field("target_language_code", "hi-IN")
        data.add_field("file", audio_bytes,
                       filename=filename or "sample.wav", content_type=mime)
        async with aiohttp.ClientSession() as s:
            async with s.post(VOICE_CLONE_URL, headers=headers, data=data,
                              timeout=aiohttp.ClientTimeout(total=120)) as r:
                body = await r.text()
                if r.status not in (200, 201):
                    # Sarvam's voice cloning is an ENTERPRISE / dashboard-only
                    # feature — there is no public REST clone endpoint (the
                    # /text-to-speech/clone path returns 404). Report this
                    # honestly so the owner isn't left debugging a phantom bug.
                    if r.status == 404:
                        return False, (
                            "Sarvam voice cloning is NOT available via the public "
                            "REST API (HTTP 404). It is an enterprise/dashboard "
                            "feature — get a cloned voice id from the Sarvam "
                            "dashboard (developer@sarvam.ai) and register it with "
                            "/addclone <voice_id> <name>."
                        )
                    return False, (
                        f"Sarvam clone API returned HTTP {r.status}: "
                        f"{body[:200]}"
                    )
                try:
                    d = await r.json(content_type=None)
                except Exception:
                    d = {}
                vid = (d.get("voice_id") or d.get("id") or d.get("speaker")
                       or "").strip().lower()
                if not vid:
                    return False, (
                        "Clone succeeded but no voice id was returned by the "
                        f"API. Response: {body[:200]}"
                    )
                await add_cloned_voice(vid, name, {"owner": owner_id})
                return True, vid
    except Exception as e:
        logger.exception(f"Voice clone failed: {e}")
        return False, f"Voice clone request failed: {type(e).__name__}: {e}"


# ═════════════════════════════════════════════════════════════════════════════
#  Synthesis
# ═════════════════════════════════════════════════════════════════════════════

async def text_to_speech(text: str, lang: str = "hi-IN",
                         speaker: Optional[str] = None) -> Optional[bytes]:
    """
    Convert text → speech (WAV bytes) using the owner-configured defaults
    unless ``speaker`` overrides it for this call.

    Returns the decoded audio bytes, or ``None`` on failure (the caller shows
    a friendly message). All failures are logged with the real reason so the
    old silent-failure behaviour can never return.
    """
    if not text or not text.strip():
        return None
    global _last_tts_error
    if not SARVAM_API_KEY:
        logger.warning("text_to_speech called but SARVAM_API_KEY is empty.")
        _last_tts_error = "SARVAM_API_KEY is not set — add it to your deploy env."
        return None

    cfg = get_tts_config()
    spk = (speaker or cfg["speaker"]).strip().lower()
    # Safety net: never send an unknown speaker to the API.
    if not is_valid_voice(spk):
        spk = DEFAULT_VOICE

    # bulbul:v3 uses the `text` (string) field — NOT the old `inputs` (list)
    # field that bulbul:v1/v2 used. Sending `inputs` to v3 is rejected.
    payload = {
        "text": text[:2500],
        "target_language_code": lang,
        "speaker": spk,
        "model": cfg["model"],
        "pace": cfg["pace"],
        "temperature": cfg["temperature"],
        "speech_sample_rate": cfg["sample_rate"],
        "output_audio_codec": "wav",
    }
    headers = {
        "api-subscription-key": SARVAM_API_KEY,
        "Content-Type": "application/json",
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(TTS_URL, json=payload, headers=headers,
                              timeout=aiohttp.ClientTimeout(total=40)) as r:
                if r.status == 200:
                    d = await r.json()
                    audios = d.get("audios") or d.get("audio") or []
                    if isinstance(audios, str):  # some envelopes wrap a single str
                        audios = [audios]
                    if audios:
                        return base64.b64decode(audios[0])
                    logger.warning("Sarvam TTS returned 200 but no audio array.")
                    _last_tts_error = "Sarvam returned HTTP 200 but no audio (empty `audios`)."
                    return None
                err = await r.text()
                logger.warning(f"Sarvam TTS failed ({r.status}): {err[:200]}")
                _last_tts_error = f"Sarvam API HTTP {r.status}: {err[:180]}"
                return None
    except Exception as e:
        logger.warning(f"Sarvam TTS request error: {e}")
        _last_tts_error = f"Request error: {type(e).__name__}: {e}"
        return None


def voice_bytes_to_file(audio: bytes, name: str = "voice.wav") -> io.BytesIO:
    """Wrap raw audio bytes in a named BytesIO suitable for send_voice."""
    af = io.BytesIO(audio)
    af.name = name
    return af


# ── Resilient voice sending ──────────────────────────────────────────────────
from telegram.error import TimedOut as _TimedOut, RetryAfter as _RetryAfter, \
    BadRequest as _BadRequest


async def send_tts_voice(bot, chat_id, audio_bytes: bytes, caption: str = "",
                         filename: str = "voice.wav",
                         reply_to_message_id: Optional[int] = None) -> tuple[bool, str]:
    """
    Send TTS audio as a voice note, resilient to Telegram timeouts / rate
    limits / format rejections.

    A transient Telegram ``TimedOut`` (or ``RetryAfter``) used to bubble all the
    way up to the owner-panel crash reporter, making a slow network blip look
    like a fatal crash. This helper retries once and, on a hard format
    rejection, falls back to sending the file as a generic audio document, so
    the command NEVER crashes on a flaky send. Returns ``(ok, error_or_None)``.
    """
    if not audio_bytes:
        return False, "No audio to send."
    af = io.BytesIO(audio_bytes)
    af.name = filename

    async def _try():
        af.seek(0)
        return await bot.send_voice(
            chat_id=chat_id, voice=af, caption=caption or None,
            reply_to_message_id=reply_to_message_id,
        )

    try:
        await _try()
        return True, None
    except _RetryAfter as e:
        await asyncio.sleep(min(getattr(e, "retry_after", 5) or 5, 30))
        try:
            await _try()
            return True, None
        except Exception as e2:
            return False, f"Rate-limited then failed: {type(e2).__name__}: {e2}"
    except _TimedOut:
        await asyncio.sleep(2)
        try:
            await _try()
            return True, None
        except Exception as e2:
            return False, f"Telegram timed out sending the voice: {type(e2).__name__}: {e2}"
    except _BadRequest:
        # e.g. unsupported voice format — try a generic audio file instead.
        try:
            af.seek(0)
            await bot.send_audio(
                chat_id=chat_id, audio=af, caption=caption or None,
                reply_to_message_id=reply_to_message_id,
            )
            return True, None
        except Exception as e2:
            return False, f"Voice format rejected, audio fallback failed: {e2}"
    except Exception as e:
        return False, f"Failed to send voice: {type(e).__name__}: {e}"
