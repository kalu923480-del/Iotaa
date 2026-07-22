"""
Iota Music Bot — Pyrogram assistant session generator.

The bot REQUIRES at least one Pyrogram "assistant" session (STRING_SESSION)
to join voice chats and stream music. Run this script once to generate it:

    python3 session.py

It will ask for the assistant account's phone number, the login code Telegram
sends, and (if enabled) the 2FA password. When done, it prints a session
STRING — copy that value into IotaXMusic/.env as STRING_SESSION.
"""
import os
import sys
from pyrogram import Client
from dotenv import load_dotenv

load_dotenv()

API_ID = os.getenv("API_ID", "").strip()
API_HASH = os.getenv("API_HASH", "").strip()


def main() -> None:
    if not API_ID or not API_HASH:
        print(
            "❌ Set API_ID and API_HASH in IotaXMusic/.env "
            "(from https://my.telegram.org) before running session.py"
        )
        sys.exit(1)
    print("» Generating Iota Music Bot assistant session string…")
    with Client(
        "iota_music_assistant",
        api_id=int(API_ID),
        api_hash=API_HASH,
    ) as app:
        session_string = app.export_session_string()
    print("\n✅ Session string generated. Copy everything below into .env:\n")
    print(session_string)
    print(
        "\nThen set STRING_SESSION=<above> in IotaXMusic/.env and start the bot "
        "with: python3 -m IotaXMedia"
    )


if __name__ == "__main__":
    main()
