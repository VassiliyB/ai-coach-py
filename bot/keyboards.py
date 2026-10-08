# bot/keyboards.py
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

GARMIN_WEBAPP_URL = "https://vassiliyb.github.io/garmin-login/"


def get_garmin_auth_keyboard() -> ReplyKeyboardMarkup:
    """Нижняя клавиатура с кнопкой вызова WebApp для безопасного входа."""
    button = KeyboardButton(
        text="🔑 Подключить Garmin Connect",
        web_app=WebAppInfo(url=GARMIN_WEBAPP_URL),
    )
    return ReplyKeyboardMarkup(
        keyboard=[[button]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def get_target_distances_keyboard() -> InlineKeyboardMarkup:
    """Инлайн-кнопки выбора целевой дистанции."""
    buttons = [
        [
            InlineKeyboardButton(text="5 км", callback_data="dist_5km"),
            InlineKeyboardButton(text="10 км", callback_data="dist_10km"),
        ],
        [
            InlineKeyboardButton(text="21.1 км (Полумарафон)", callback_data="dist_21km"),
            InlineKeyboardButton(text="42.2 км (Марафон)", callback_data="dist_42km"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


DELETE_CONFIRM = "delete_me:confirm"
DELETE_CANCEL = "delete_me:cancel"


def get_delete_confirm_keyboard() -> InlineKeyboardMarkup:
    """Подтверждение /delete_me: удаление необратимо, поэтому только по явной кнопке."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗑 Удалить всё", callback_data=DELETE_CONFIRM),
        InlineKeyboardButton(text="Отмена", callback_data=DELETE_CANCEL),
    ]])
