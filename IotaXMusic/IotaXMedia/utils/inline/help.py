# Authored By Iota Coders © 2025
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from IotaXMedia import app


TOTAL_SECTIONS = 31

# Help sections that list owner/sudo-only features (marked "[Only for Sudoers]").
# These buttons are hidden from normal users and only shown to sudoers/owner.
RESTRICTED_HELP_SECTIONS = {4, 5, 8, 9, 14}


def generate_help_buttons(_, start: int, end: int, current_page: int, is_authorized: bool = False):
    """Create a grid of three buttons per row for the given range."""
    buttons, per_row = [], 3
    for idx, i in enumerate(range(start, end + 1)):
        if i in RESTRICTED_HELP_SECTIONS and not is_authorized:
            continue
        if idx % per_row == 0:
            buttons.append([])
        buttons[-1].append(
            InlineKeyboardButton(
                text=_[f"H_B_{i}"],
                callback_data=f"help_callback hb{i}_p{current_page}"
            )
        )
    return buttons


def first_page(_, is_authorized: bool = False):
    buttons = generate_help_buttons(_, 1, 15, current_page=1, is_authorized=is_authorized)
    buttons.append(
        [
            InlineKeyboardButton(text="๏ ᴍᴇɴᴜ ๏", callback_data="back_to_main"),
            InlineKeyboardButton(text="๏ ɴᴇxᴛ ๏", callback_data="help_next_2")
        ]
    )
    return InlineKeyboardMarkup(buttons)


def second_page(_, is_authorized: bool = False):
    buttons = generate_help_buttons(_, 16, TOTAL_SECTIONS, current_page=2, is_authorized=is_authorized)
    buttons.append(
        [
            InlineKeyboardButton(text="๏ ʙᴀᴄᴋ ๏", callback_data="help_prev_1"),
            InlineKeyboardButton(text="๏ ᴍᴇɴᴜ ๏", callback_data="back_to_main")
        ]
    )
    return InlineKeyboardMarkup(buttons)


def action_sub_menu(_, current_page: int):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text=_[ "H_B_S_1" ],
                    callback_data="action_prom_1"
                ),
                InlineKeyboardButton(
                    text=_[ "H_B_S_2" ],
                    callback_data="action_pun_1"
                )
            ],
            [
                InlineKeyboardButton(
                    text=_["BACK_BUTTON"],
                    callback_data=f"help_back_{current_page}"
                )
            ]
        ]
    )


def help_back_markup(_, current_page: int):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text=_["BACK_BUTTON"],
                    callback_data=f"help_back_{current_page}"
                ),
                InlineKeyboardButton(
                    text=_["CLOSE_BUTTON"],
                    callback_data="close"
                ),
            ]
        ]
    )


def private_help_panel(_):
    return [
        [
            InlineKeyboardButton(
                text=_["S_B_3"],
                url=f"https://t.me/{app.username}?start=help"
            ),
        ],
    ]
