"""
Iota Bot — Chat Compiler
══════════════════════════════════════════════════════════════════════
Manages conversation state BEFORE it's sent to any AI provider, and
normalizes each provider's response back into one common string. This
keeps utils/ai_provider.py's call_ai() simple — it doesn't need to know
which provider is being used, and every provider's quirks are handled
in exactly one place.

Responsibilities:
  • Trim conversation history to a configurable max length (oldest
    messages dropped first, system prompt always kept)
  • Remove consecutive duplicate messages (a common symptom of a retry
    loop or a doubled event) so context doesn't get bloated for no
    reason
  • Convert the generic OpenAI-style {"role", "content"} message list
    into whatever shape a specific provider needs (Gemini's
    contents/parts + system_instruction, vs everyone else's plain
    OpenAI messages array)
  • Normalize every provider's raw response back into a plain string
"""
import logging

logger = logging.getLogger(__name__)

# Chat turns only (not counting system). Keep low — free tiers die on long context.
DEFAULT_MAX_HISTORY = 8
MAX_MSG_CHARS = 500
MAX_SYSTEM_CHARS = 4500


def _clip_msg(m: dict, max_chars: int = MAX_MSG_CHARS) -> dict:
    c = m.get("content")
    if not isinstance(c, str) or len(c) <= max_chars:
        return m
    out = dict(m)
    out["content"] = c[: max_chars - 1] + "…"
    return out


def compile_messages(messages: list, max_history: int = DEFAULT_MAX_HISTORY) -> list:
    """
    Cleans up a raw {"role","content"} message list before sending it
    anywhere: keeps the system prompt (if present) untouched, drops
    consecutive exact-duplicate messages, trims long bodies, and trims
    from the oldest non-system messages if the list is too long.
    """
    if not messages:
        return messages

    system_msgs = []
    for m in messages:
        if m.get("role") != "system":
            continue
        sm = dict(m)
        c = sm.get("content") or ""
        if isinstance(c, str) and len(c) > MAX_SYSTEM_CHARS:
            sm["content"] = c[: MAX_SYSTEM_CHARS - 20] + "\n…[truncated]"
        system_msgs.append(sm)

    convo_msgs = [m for m in messages if m.get("role") != "system"]

    # Drop consecutive duplicates (same role + same content back to back)
    deduped = []
    for m in convo_msgs:
        if (
            deduped
            and deduped[-1].get("role") == m.get("role")
            and deduped[-1].get("content") == m.get("content")
        ):
            continue
        deduped.append(_clip_msg(m))

    # Trim oldest first if over the limit
    max_history = max(2, min(int(max_history or DEFAULT_MAX_HISTORY), 16))
    if len(deduped) > max_history:
        deduped = deduped[-max_history:]

    return system_msgs + deduped


def to_gemini_format(messages: list) -> dict:
    """
    Converts a standard {"role","content"} message list into Gemini's
    REST body shape: a top-level system_instruction plus a contents
    array using Gemini's "user"/"model" roles (Gemini has no separate
    "assistant" role — it calls the AI's turns "model").
    """
    system_text = "\n".join(m["content"] for m in messages if m.get("role") == "system")
    contents = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
        gemini_role = "model" if role == "assistant" else "user"
        contents.append({"role": gemini_role, "parts": [{"text": m.get("content", "")}]})

    body = {"contents": contents}
    if system_text:
        body["system_instruction"] = {"parts": [{"text": system_text}]}
    return body


def normalize_openai_response(data: dict) -> str | None:
    """Extracts the reply text from any OpenAI-compatible response
    (Groq, OpenRouter, Cloudflare Workers AI all use this exact shape)."""
    try:
        content = data["choices"][0]["message"]["content"]
        return content.strip() if content else None
    except (KeyError, IndexError, TypeError) as e:
        logger.debug(f"normalize_openai_response: unexpected shape: {e}")
        return None


def normalize_gemini_response(data: dict) -> str | None:
    """Extracts the reply text from a Gemini generateContent response."""
    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts)
        return text.strip() if text else None
    except (KeyError, IndexError, TypeError) as e:
        logger.debug(f"normalize_gemini_response: unexpected shape: {e}")
        return None
