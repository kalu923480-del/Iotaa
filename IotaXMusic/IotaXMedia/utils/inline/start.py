# Authored By Iota Coders © 2025
from pyrogram.types import InlineKeyboardButton

import config
from IotaXMedia import app


def start_panel(_):
    buttons = [
        [
            InlineKeyboardButton(
                text=_["S_B_1"], url=f"https://t.me/{app.username}?startgroup=true"
            ),
            InlineKeyboardButton(text=_["S_B_2"], url=config.SUPPORT_CHANNEL),
        ],
    ]
    return buttons


def private_panel(_):
    owner_btn = InlineKeyboardButton(
        text=_["S_B_7"],
        url=f"tg://user?id={config.OWNER_ID}",
    )
    buttons = [
        [
            InlineKeyboardButton(
                text=_["S_B_1"],
                url=f"https://t.me/{app.username}?startgroup=true",
            )
        ],
        [
            owner_btn,
            InlineKeyboardButton(text=_["S_B_4"], url=config.SUPPORT_CHAT),
        ],
        [
            InlineKeyboardButton(text=_["S_B_3"], callback_data="open_help"),
        ],
    ]
    return buttons
