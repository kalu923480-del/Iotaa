"""
Iota Bot — AI Provider System v4 — Multi-Provider Edition
══════════════════════════════════════════════════════════════════════

Supports FOUR independent AI providers, tried in a configurable
PRIORITY ORDER: if every key for the current provider is exhausted
(rate-limited, down, or misconfigured), the whole provider is skipped
and the next one in priority order is tried automatically — not just
the next key within one provider.

   1. Groq            (https://api.groq.com)
   2. Google Gemini    (https://ai.google.dev)
   3. OpenRouter       (https://openrouter.ai)
   4. Cloudflare Workers AI (https://developers.cloudflare.com/workers-ai)

Owner can add custom OpenAI-compatible providers (Together, Fireworks,
Mistral, DeepSeek, local Ollama, etc.) from Telegram DM.
"""
import asyncio
import logging
import re
import time

import aiohttp

from utils.chat_compiler import (
    compile_messages, to_gemini_format,
    normalize_openai_response, normalize_gemini_response,
)

logger = logging.getLogger(__name__)

try:
    from config import (GROQ_API_KEYS, GEMINI_API_KEYS, OPENROUTER_API_KEYS,
                         CLOUDFLARE_API_KEYS, CLOUDFLARE_ACCOUNT_ID)
except ImportError:
    GROQ_API_KEYS = []
    GEMINI_API_KEYS = []
    OPENROUTER_API_KEYS = []
    CLOUDFLARE_API_KEYS = []
    CLOUDFLARE_ACCOUNT_ID = ""

_DEFAULT_COOLDOWN_SECONDS = 60
_DEFAULT_MAX_TOKENS = 1024
_REQUEST_TIMEOUT_SECONDS = 25
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


# ══════════════════════════════════════════════════════════════════════
# API Key Pool (per-provider)
# ══════════════════════════════════════════════════════════════════════

class _KeyHealth:
    __slots__ = ("key", "status", "total_requests", "successful_requests",
                 "failed_requests", "last_error", "last_used_ts", "cooldown_until")

    def __init__(self, key: str):
        self.key = key
        self.status = "active"
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.last_error = ""
        self.last_used_ts = 0.0
        self.cooldown_until = 0.0

    def masked(self) -> str:
        k = self.key
        return f"{k[:7]}...{k[-4:]}" if len(k) > 12 else "***"


class _Provider:
    """Holds everything needed to call one AI provider."""

    def __init__(self, pid: str, name: str, kind: str, base_url: str,
                 configured_keys: list, default_free: str, default_premium: str,
                 custom: bool = False, account_id: str = "",
                 extra_headers: dict | None = None):
        self.id = pid
        self.name = name
        self.kind = kind
        self.base_url = base_url or ""
        self.keys: list[_KeyHealth] = [_KeyHealth(k) for k in configured_keys if k]
        self.free_model = default_free
        self.premium_model = default_premium
        self.enabled = True
        self.live_models: list[str] = []
        self.live_models_fetched_at = 0.0
        self.custom = custom
        self.account_id = account_id or ""
        self.extra_headers = extra_headers or {}

    def pick_key(self) -> "_KeyHealth | None":
        now = time.time()
        for k in self.keys:
            if k.status == "cooling_down" and now >= k.cooldown_until:
                k.status = "active"
        healthy = [k for k in self.keys if k.status == "active"]
        if not healthy:
            return None
        healthy.sort(key=lambda k: k.last_used_ts)
        return healthy[0]


_cooldown_seconds = _DEFAULT_COOLDOWN_SECONDS
_max_tokens_default = _DEFAULT_MAX_TOKENS

_PROVIDER_DEFS = [
    ("groq", "Groq", "openai_compat", "https://api.groq.com/openai/v1",
     GROQ_API_KEYS, "openai/gpt-oss-20b", "openai/gpt-oss-120b", False, "", {}),
    ("gemini", "Google Gemini", "gemini", "https://generativelanguage.googleapis.com/v1beta",
     GEMINI_API_KEYS, "gemini-2.0-flash", "gemini-2.5-flash", False, "", {}),
    ("openrouter", "OpenRouter", "openai_compat", "https://openrouter.ai/api/v1",
     OPENROUTER_API_KEYS, "meta-llama/llama-3.1-8b-instruct:free", "openai/gpt-4o-mini", False, "", {}),
    ("cloudflare", "Cloudflare Workers AI", "openai_compat",
      (f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1"
       if CLOUDFLARE_ACCOUNT_ID else ""),
      CLOUDFLARE_API_KEYS, "@cf/meta/llama-3.1-8b-instruct", "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
      False, CLOUDFLARE_ACCOUNT_ID or "", {}),
]

_providers: dict[str, _Provider] = {
    pid: _Provider(pid, name, kind, base_url, keys, free_m, prem_m,
                   custom=custom, account_id=account_id, extra_headers=extra_headers)
    for pid, name, kind, base_url, keys, free_m, prem_m, custom, account_id, extra_headers in _PROVIDER_DEFS
}
_provider_priority: list[str] = [p[0] for p in _PROVIDER_DEFS]


def _ordered_providers() -> list[_Provider]:
    return [_providers[pid] for pid in _provider_priority
            if pid in _providers and _providers[pid].enabled]


# ══════════════════════════════════════════════════════════════════════
# Owner-panel-facing management functions
# ══════════════════════════════════════════════════════════════════════

def add_api_key(key: str, provider: str = "groq") -> bool:
    p = _providers.get(provider)
    if not p:
        raise ValueError(f"Unknown provider '{provider}'. Options: {list(_providers)}")
    key = key.strip()
    if not key or len(key) < 8:
        raise ValueError("API key too short (min 8 chars).")
    if any(k.key == key for k in p.keys):
        return False
    p.keys.append(_KeyHealth(key))
    return True


def remove_api_key(key_prefix: str, provider: str = "groq") -> bool:
    p = _providers.get(provider)
    if not p:
        return False
    before = len(p.keys)
    p.keys = [k for k in p.keys if not k.key.startswith(key_prefix)]
    return len(p.keys) < before


def add_api_keys_bulk(keys: list[str], provider: str) -> tuple[int, int]:
    """Add multiple keys to a provider. Returns (added, skipped)."""
    p = _providers.get(provider)
    if not p:
        raise ValueError(f"Unknown provider '{provider}'. Options: {list(_providers)}")
    existing = {k.key for k in p.keys}
    added = 0
    skipped = 0
    for key in keys:
        key = key.strip()
        if not key or len(key) < 8 or key in existing:
            skipped += 1
            continue
        p.keys.append(_KeyHealth(key))
        existing.add(key)
        added += 1
    return added, skipped


def list_api_keys_masked(provider: str | None = None) -> dict:
    """Returns {provider_id: [masked_key, ...]} for one provider or all."""
    out = {}
    targets = [_providers[provider]] if provider else list(_providers.values())
    for p in targets:
        out[p.id] = [k.masked() for k in p.keys]
    return out


def clear_provider_keys(provider: str) -> int:
    """Remove all keys from a provider. Returns count removed."""
    p = _providers.get(provider)
    if not p:
        return 0
    count = len(p.keys)
    p.keys = []
    return count


def get_key_pool_status(provider: str | None = None) -> dict:
    now = time.time()
    out = {}
    targets = [_providers[provider]] if provider else list(_providers.values())
    for p in targets:
        rows = []
        for k in p.keys:
            cooldown_left = max(0, int(k.cooldown_until - now)) if k.status == "cooling_down" else 0
            rows.append({
                "masked": k.masked(), "status": k.status, "total": k.total_requests,
                "success": k.successful_requests, "failed": k.failed_requests,
                "last_error": k.last_error, "cooldown_seconds_left": cooldown_left,
            })
        out[p.id] = rows
    return out


def get_providers_status() -> list[dict]:
    now = time.time()
    out = []
    for pid in _provider_priority:
        p = _providers.get(pid)
        if not p:
            continue
        healthy_keys = sum(
            1 for k in p.keys
            if k.status == "active" or (k.status == "cooling_down" and now >= k.cooldown_until)
        )
        base_url_display = p.base_url[:40] + "..." if len(p.base_url) > 40 else p.base_url
        out.append({
            "id": p.id, "name": p.name, "enabled": p.enabled,
            "key_count": len(p.keys), "healthy_keys": healthy_keys,
            "free_model": p.free_model, "premium_model": p.premium_model,
            "custom": p.custom,
            "base_url": base_url_display,
        })
    return out


def set_provider_enabled(provider: str, enabled: bool) -> bool:
    p = _providers.get(provider)
    if not p:
        return False
    p.enabled = enabled
    return True


def set_provider_priority(order: list[str]) -> bool:
    if set(order) != set(_providers.keys()):
        return False
    global _provider_priority
    _provider_priority = list(order)
    return True


def get_provider_priority() -> list[str]:
    return list(_provider_priority)


def list_providers() -> list[str]:
    return list(_providers.keys())


def is_builtin_provider(pid: str) -> bool:
    return pid in {p[0] for p in _PROVIDER_DEFS}


def get_provider_info(pid: str) -> dict | None:
    p = _providers.get(pid)
    if not p:
        return None
    return {
        "id": p.id, "name": p.name, "kind": p.kind,
        "base_url": p.base_url, "custom": p.custom,
        "account_id": p.account_id, "extra_headers": dict(p.extra_headers),
        "enabled": p.enabled, "free_model": p.free_model,
        "premium_model": p.premium_model, "key_count": len(p.keys),
    }


def register_provider(pid, name, kind="openai_compat", base_url="", free_model="",
                      premium_model="", keys=None, enabled=True, custom=True,
                      account_id="", extra_headers=None) -> bool:
    """Register a new provider. Returns True on success, False if already exists."""
    if pid in _providers:
        return False
    pid = (pid or "").strip().lower()
    if not re.match(r'^[a-z0-9_]+$', pid):
        raise ValueError(f"Provider id must be lowercase a-z, 0-9, underscore only: {pid}")
    kind = kind or "openai_compat"
    if kind not in ("openai_compat", "gemini"):
        raise ValueError("kind must be 'openai_compat' or 'gemini'")
    base_url = (base_url or "").strip().rstrip("/")
    if kind == "openai_compat" and custom and not base_url:
        raise ValueError("base_url is required for custom OpenAI-compatible providers")
    p = _Provider(
        pid, name or pid, kind, base_url, keys or [], free_model or "", premium_model or "",
        custom=custom, account_id=account_id or "", extra_headers=extra_headers or {},
    )
    p.enabled = enabled
    _providers[pid] = p
    if pid not in _provider_priority:
        _provider_priority.append(pid)
    return True


def unregister_provider(pid) -> bool:
    """Remove a custom provider. Returns True on success, False if not found or built-in."""
    if pid not in _providers:
        return False
    if not _providers[pid].custom:
        return False
    del _providers[pid]
    if pid in _provider_priority:
        _provider_priority.remove(pid)
    return True


def update_provider(pid, **fields) -> bool:
    """Update fields on a provider. Supported: name, base_url, free_model,
    premium_model, enabled, account_id, extra_headers."""
    p = _providers.get(pid)
    if not p:
        return False
    allowed = {
        "name", "base_url", "free_model", "premium_model",
        "enabled", "account_id", "extra_headers",
    }
    for field, value in fields.items():
        if field not in allowed:
            continue
        if field == "base_url":
            p.base_url = (value or "").rstrip("/")
        elif field == "account_id":
            p.account_id = value or ""
            # Auto-rebuild Cloudflare Workers AI base URL when account id set
            if pid == "cloudflare" and p.account_id and not fields.get("base_url"):
                # OpenAI-compatible Workers AI base (…/ai/v1/chat/completions)
                p.base_url = (
                    f"https://api.cloudflare.com/client/v4/accounts/"
                    f"{p.account_id}/ai/v1"
                )
        elif field == "extra_headers":
            p.extra_headers = value if value is not None else {}
        else:
            setattr(p, field, value)
    return True


def get_current_models(provider: str | None = None) -> dict:
    if provider:
        p = _providers.get(provider)
        if not p:
            return {}
        return {"free_model": p.free_model, "premium_model": p.premium_model}
    ordered = _ordered_providers()
    if not ordered:
        return {"free_model": "", "premium_model": ""}
    p = ordered[0]
    return {"free_model": p.free_model, "premium_model": p.premium_model}


def set_model(tier: str, model: str, provider: str = "groq"):
    if tier not in ("free", "premium"):
        raise ValueError("tier must be 'free' or 'premium'")
    p = _providers.get(provider)
    if not p:
        raise ValueError(f"Unknown provider '{provider}'")
    setattr(p, f"{tier}_model", model)


def get_max_tokens() -> int:
    return _max_tokens_default


def set_max_tokens(n: int):
    global _max_tokens_default
    _max_tokens_default = max(64, min(8192, n))


def set_cooldown_seconds(seconds: int):
    global _cooldown_seconds
    _cooldown_seconds = max(5, seconds)


def get_all_models(provider: str | None = None) -> dict:
    """Returns {"live": [...], "free": [...], "premium": [...]}."""
    p = _providers.get(provider) if provider else (_ordered_providers() or [None])[0]
    if not p:
        return {"live": [], "free": [], "premium": []}
    live = p.live_models or [p.free_model, p.premium_model]
    return {"live": live, "free": [p.free_model], "premium": [p.premium_model]}


async def refresh_live_models(provider: str = "groq", force: bool = False) -> list[str]:
    p = _providers.get(provider)
    if not p:
        return []
    now = time.time()
    if not force and p.live_models and (now - p.live_models_fetched_at) < 600:
        return p.live_models

    if p.kind != "openai_compat" or not p.base_url:
        p.live_models = [p.free_model, p.premium_model]
        p.live_models_fetched_at = now
        return p.live_models

    key_obj = p.pick_key()
    if not key_obj:
        logger.warning(f"refresh_live_models({provider}): no healthy API key")
        return p.live_models

    headers = {"Authorization": f"Bearer {key_obj.key}"}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{p.base_url}/models", headers=headers,
                              timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    data = await r.json()
                    models = sorted(m["id"] for m in data.get("data", []))
                    p.live_models = models
                    p.live_models_fetched_at = now
                    return models
                else:
                    logger.warning(f"refresh_live_models({provider}): HTTP {r.status}")
    except Exception as e:
        logger.warning(f"refresh_live_models({provider}) failed: {e}")
    return p.live_models


async def test_provider_call(provider: str, prompt: str = "Say 'ok' to confirm you work.") -> tuple[bool, str]:
    """Test a single provider directly. Returns (success, message)."""
    p = _providers.get(provider)
    if not p:
        return False, f"Unknown provider: {provider}"
    if not p.keys:
        return False, f"No keys configured for {provider}"
    model = p.free_model or p.premium_model
    if not model:
        return False, f"No model configured for {provider}"
    messages = [{"role": "user", "content": prompt}]
    result = await _call_provider(p, messages, model, 20, 0.0)
    if result:
        return True, result[:200]
    return False, f"Provider {provider} failed or returned empty"


# ══════════════════════════════════════════════════════════════════════
# Persistence (MongoDB)
# ══════════════════════════════════════════════════════════════════════

async def save_model_config_db():
    try:
        from utils.mongo_db import get_db
        providers_cfg = {}
        for p in _providers.values():
            providers_cfg[p.id] = {
                "free_model": p.free_model,
                "premium_model": p.premium_model,
                "enabled": p.enabled,
                "name": p.name,
                "kind": p.kind,
                "base_url": p.base_url,
                "custom": p.custom,
                "account_id": p.account_id,
                "extra_headers": p.extra_headers,
            }
        await get_db().bot_config.update_one(
            {"_id": "ai_model_config"},
            {"$set": {
                "max_tokens": _max_tokens_default,
                "providers": providers_cfg,
                "priority": _provider_priority,
            }},
            upsert=True,
        )
    except Exception as e:
        logger.warning(f"save_model_config_db failed: {e}")


async def load_model_config_db():
    try:
        from utils.mongo_db import get_db
        doc = await get_db().bot_config.find_one({"_id": "ai_model_config"})
        if doc:
            global _max_tokens_default, _provider_priority
            _max_tokens_default = doc.get("max_tokens", _max_tokens_default)
            for pid, cfg in doc.get("providers", {}).items():
                p = _providers.get(pid)
                if p:
                    p.free_model = cfg.get("free_model", p.free_model)
                    p.premium_model = cfg.get("premium_model", p.premium_model)
                    p.enabled = cfg.get("enabled", p.enabled)
                    if "account_id" in cfg:
                        p.account_id = cfg["account_id"]
                    if "extra_headers" in cfg:
                        p.extra_headers = cfg["extra_headers"]
                    if "base_url" in cfg:
                        p.base_url = cfg["base_url"]
                    if "name" in cfg:
                        p.name = cfg["name"]
                elif cfg.get("custom", False):
                    register_provider(
                        pid,
                        cfg.get("name", pid),
                        kind=cfg.get("kind", "openai_compat"),
                        base_url=cfg.get("base_url", ""),
                        free_model=cfg.get("free_model", ""),
                        premium_model=cfg.get("premium_model", ""),
                        enabled=cfg.get("enabled", True),
                        custom=True,
                        account_id=cfg.get("account_id", ""),
                        extra_headers=cfg.get("extra_headers", {}),
                    )
            saved_priority = doc.get("priority") or []
            # Merge priority: keep saved order for known ids, append any new ones
            if saved_priority:
                known = set(_providers.keys())
                merged = [pid for pid in saved_priority if pid in known]
                for pid in _providers:
                    if pid not in merged:
                        merged.append(pid)
                _provider_priority = merged

        keys_doc = await get_db().bot_config.find_one({"_id": "ai_api_keys"})
        # When a keys doc exists, DB is the source of truth (full replace per
        # provider). This prevents removed env keys from reappearing.
        if keys_doc and "by_provider" in keys_doc:
            by_provider = keys_doc.get("by_provider") or {}
            for pid, p in _providers.items():
                if pid in by_provider:
                    p.keys = [_KeyHealth(k) for k in (by_provider[pid] or []) if k]
    except Exception as e:
        logger.warning(f"load_model_config_db failed: {e}")


async def save_api_keys_db():
    try:
        from utils.mongo_db import get_db
        by_provider = {p.id: [k.key for k in p.keys] for p in _providers.values()}
        await get_db().bot_config.update_one(
            {"_id": "ai_api_keys"},
            {"$set": {"by_provider": by_provider}},
            upsert=True,
        )
    except Exception as e:
        logger.warning(f"save_api_keys_db failed: {e}")


async def save_all_provider_state():
    """Persist both model config and API keys to MongoDB."""
    await save_model_config_db()
    await save_api_keys_db()


# ══════════════════════════════════════════════════════════════════════
# Core request logic
# ══════════════════════════════════════════════════════════════════════

async def _call_openai_compat_once(p: _Provider, key_obj: _KeyHealth, messages: list,
                                    model: str, max_tokens: int, temperature: float):
    headers = {"Authorization": f"Bearer {key_obj.key}", "Content-Type": "application/json"}
    headers.update(p.extra_headers or {})
    base = (p.base_url or "").rstrip("/")
    url = f"{base}/chat/completions"
    # Prefer max_tokens (widest OpenAI-compat support). If the provider
    # rejects it, retry once with max_completion_tokens (newer OpenAI/Groq).
    payloads = [
        {"model": model, "messages": messages, "max_tokens": max_tokens,
         "temperature": temperature},
        {"model": model, "messages": messages, "max_completion_tokens": max_tokens,
         "temperature": temperature},
    ]
    key_obj.total_requests += 1
    key_obj.last_used_ts = time.time()
    try:
        async with aiohttp.ClientSession() as s:
            last_body = ""
            last_status = 0
            for i, payload in enumerate(payloads):
                async with s.post(url, json=payload, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS)) as r:
                    last_status = r.status
                    if r.status == 200:
                        data = await r.json()
                        result = normalize_openai_response(data)
                        if result:
                            key_obj.successful_requests += 1
                            return result
                        key_obj.failed_requests += 1
                        key_obj.last_error = "empty/unrecognized response shape"
                        return None
                    last_body = await r.text()
                    # Retry with alternate token field only on 400-style param errors
                    if i == 0 and r.status == 400 and (
                        "max_tokens" in last_body.lower()
                        or "max_completion" in last_body.lower()
                    ):
                        continue
                    break

            key_obj.failed_requests += 1
            key_obj.last_error = f"HTTP {last_status}: {last_body[:150]}"
            if last_status in _RETRYABLE_STATUSES:
                key_obj.status = "cooling_down"
                key_obj.cooldown_until = time.time() + _cooldown_seconds
                logger.info(f"[{p.name}] key {key_obj.masked()} -> HTTP {last_status}, cooling down")
            else:
                logger.warning(f"[{p.name}] key {key_obj.masked()} -> non-retryable HTTP {last_status}")
            return None
    except asyncio.TimeoutError:
        key_obj.failed_requests += 1
        key_obj.last_error = "timeout"
        key_obj.status = "cooling_down"
        key_obj.cooldown_until = time.time() + _cooldown_seconds
        return None
    except Exception as e:
        key_obj.failed_requests += 1
        key_obj.last_error = str(e)[:150]
        logger.warning(f"[{p.name}] key {key_obj.masked()} -> error: {e}")
        return None


async def _call_gemini_once(p: _Provider, key_obj: _KeyHealth, messages: list,
                             model: str, max_tokens: int, temperature: float):
    headers = {"x-goog-api-key": key_obj.key, "Content-Type": "application/json"}
    body = to_gemini_format(messages)
    body["generationConfig"] = {"maxOutputTokens": max_tokens, "temperature": temperature}
    key_obj.total_requests += 1
    key_obj.last_used_ts = time.time()
    url = f"{p.base_url}/models/{model}:generateContent"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=body, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS)) as r:
                if r.status == 200:
                    data = await r.json()
                    result = normalize_gemini_response(data)
                    if result:
                        key_obj.successful_requests += 1
                        return result
                    key_obj.failed_requests += 1
                    key_obj.last_error = "empty/unrecognized response shape"
                    return None

                body_text = await r.text()
                key_obj.failed_requests += 1
                key_obj.last_error = f"HTTP {r.status}: {body_text[:150]}"
                if r.status in _RETRYABLE_STATUSES:
                    key_obj.status = "cooling_down"
                    key_obj.cooldown_until = time.time() + _cooldown_seconds
                    logger.info(f"[Gemini] key {key_obj.masked()} -> HTTP {r.status}, cooling down")
                else:
                    logger.warning(f"[Gemini] key {key_obj.masked()} -> non-retryable HTTP {r.status}")
                return None
    except asyncio.TimeoutError:
        key_obj.failed_requests += 1
        key_obj.last_error = "timeout"
        key_obj.status = "cooling_down"
        key_obj.cooldown_until = time.time() + _cooldown_seconds
        return None
    except Exception as e:
        key_obj.failed_requests += 1
        key_obj.last_error = str(e)[:150]
        logger.warning(f"[Gemini] key {key_obj.masked()} -> error: {e}")
        return None


async def _call_provider(p: _Provider, messages: list, model: str,
                          max_tokens: int, temperature: float):
    if not p.keys:
        return None
    if p.kind == "openai_compat" and not p.base_url:
        logger.warning(f"[{p.name}] not configured (missing base URL) — skipping")
        return None

    tried = set()
    for _ in range(len(p.keys)):
        key_obj = p.pick_key()
        if not key_obj or key_obj.key in tried:
            break
        tried.add(key_obj.key)
        if p.kind == "gemini":
            result = await _call_gemini_once(p, key_obj, messages, model, max_tokens, temperature)
        else:
            result = await _call_openai_compat_once(p, key_obj, messages, model, max_tokens, temperature)
        if result:
            return result
    return None


async def call_ai(messages: list, is_premium: bool = False,
                   max_tokens: int | None = None, temperature: float = 0.9) -> str:
    if max_tokens is None:
        max_tokens = _max_tokens_default

    messages = compile_messages(messages)

    ordered = _ordered_providers()
    if not ordered:
        raise Exception(
            "No AI providers configured at all. Add at least one key via "
            "/addapikey (owner panel) — Groq, Gemini, OpenRouter, and "
            "Cloudflare Workers AI are all supported, and all have free tiers."
        )

    attempted_providers = []
    for p in ordered:
        model = p.premium_model if is_premium else p.free_model
        result = await _call_provider(p, messages, model, max_tokens, temperature)
        if result:
            return result
        attempted_providers.append(p.name)

    raise Exception(
        f"All configured AI providers failed or are rate-limited right now "
        f"(tried: {', '.join(attempted_providers) or 'none configured'}). "
        f"Check /providerstatus in the owner panel."
    )
