# Authored By Iota Coders © 2025
import httpx
from pyrogram import filters
from pyrogram.types import Message

from config import (
    AI_API_KEY,
    AI_API_URL,
    AI_MAX_TOKENS,
    AI_MODEL,
    AI_PROVIDER,
    AI_SYSTEM_PROMPT,
    BANNED_USERS,
)
from IotaXMedia import app


def _split_text(text: str, limit: int = 4000):
    """Split a long reply into Telegram-safe chunks."""
    if len(text) <= limit:
        return [text]
    parts, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit:
            parts.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        parts.append(current)
    return parts


async def _query_openai(prompt: str) -> str:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {AI_API_KEY}",
    }
    payload = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": AI_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": AI_MAX_TOKENS,
    }
    async with httpx.AsyncClient(timeout=90, verify=False) as client:
        resp = await client.post(AI_API_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()


async def _query_gemini(prompt: str) -> str:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{AI_MODEL}:generateContent?key={AI_API_KEY}"
    )
    headers = {"Content-Type": "application/json"}
    payload = {
        "systemInstruction": {"parts": [{"text": AI_SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
    }
    async with httpx.AsyncClient(timeout=90, verify=False) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()


@app.on_message(
    filters.command(["ai", "ask", "iota"], prefixes=["/", "."]) & ~filters.user(list(BANNED_USERS))
)
async def ai_command(client, message: Message):
    if not AI_API_KEY:
        return await message.reply_text(
            "⚠️ **ɪᴏᴛᴀ ᴀɪ ɪꜱ ᴅɪꜱᴀʙʟᴇᴅ.**\n\n"
            "ᴛʜᴇ ᴏᴡɴᴇʀ ʜᴀꜱɴ'ᴛ ᴄᴏɴꜰɪɢᴜʀᴇᴅ ᴀɴ ᴀɪ ᴋᴇʏ ʏᴇᴛ. "
            "ꜱᴇᴛ `AI_API_KEY` ɪɴ `.env` ᴛᴏ ᴇɴᴀʙʟᴇ ᴛʜᴇ `/ai` ᴄᴏᴍᴍᴀɴᴅ."
        )

    if len(message.command) > 1:
        prompt = message.text.split(None, 1)[1].strip()
    elif message.reply_to_message and message.reply_to_message.text:
        prompt = message.reply_to_message.text
    else:
        return await message.reply_text(
            "**ᴜꜱᴀɢᴇ :** `/ai <ʏᴏᴜʀ Qᴜᴇꜱᴛɪᴏɴ>`\n"
            "**ᴇxᴀᴍᴩʟᴇ :** `/ai ᴡʜᴏ ᴀʀᴇ ʏᴏᴜ?`"
        )

    wait = await message.reply_text("🤖 **ɪᴏᴛᴀ ɪꜱ ᴛʜɪɴᴋɪɴɢ...**")

    try:
        if AI_PROVIDER == "gemini":
            answer = await _query_gemini(prompt)
        else:
            answer = await _query_openai(prompt)
    except httpx.HTTPStatusError as e:
        await wait.edit_text(
            f"❌ **ᴀɪ ʀᴇǫᴜᴇꜱᴛ ꜰᴀɪʟᴇᴅ** (HTTP {e.response.status_code}). "
            "ᴄʜᴇᴄᴋ ʏᴏᴜʀ `AI_API_KEY` / `AI_MODEL` ᴄᴏɴꜰɪɢ."
        )
        return
    except Exception as e:
        await wait.edit_text(
            "❌ **ᴀɪ ɪꜱ ᴜɴʀᴇᴀᴄʜᴀʙʟᴇ ʀɪɢʜᴛ ɴᴏᴡ.** ᴛʀʏ ᴀɢᴀɪɴ ʟᴀᴛᴇʀ."
        )
        print(f"❌ AI error: {e}")
        return

    if not answer:
        await wait.edit_text("❌ ᴛʜᴇ ᴀɪ ʀᴇᴛᴜʀɴᴇᴅ ᴀɴ ᴇᴍᴩᴛʏ ʀᴇꜱᴩᴏɴꜱᴇ.")
        return

    chunks = _split_text(answer)
    await wait.delete()
    for chunk in chunks:
        await message.reply_text(chunk, quote=True)
