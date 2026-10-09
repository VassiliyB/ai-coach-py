# bot/keyboards.py
from typing import Optional

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


ACCESS_APPROVE_PREFIX = "access:approve:"
ACCESS_REJECT_PREFIX = "access:reject:"


def get_access_request_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """Кнопки админу под запросом доступа нового пользователя."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Пустить", callback_data=f"{ACCESS_APPROVE_PREFIX}{chat_id}"),
        InlineKeyboardButton(text="⛔ Отказать", callback_data=f"{ACCESS_REJECT_PREFIX}{chat_id}"),
    ]])


DELETE_CONFIRM = "delete_me:confirm"
DELETE_CANCEL = "delete_me:cancel"


def get_delete_confirm_keyboard() -> InlineKeyboardMarkup:
    """Подтверждение /delete_me: удаление необратимо, поэтому только по явной кнопке."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗑 Удалить всё", callback_data=DELETE_CONFIRM),
        InlineKeyboardButton(text="Отмена", callback_data=DELETE_CANCEL),
    ]])


GARMIN_EXPORT_PREFIX = "garmin_export:"


def get_garmin_export_keyboard(weekly_id: int) -> InlineKeyboardMarkup:
    """Кнопка под расписанием недели: выгрузить тренировки в календарь Garmin."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📅 В календарь Garmin", callback_data=f"{GARMIN_EXPORT_PREFIX}{weekly_id}"),
    ]])


GOAL_NONE = "goal:none"
GOAL_SUGGESTED = "goal:suggested"


def get_goal_keyboard(suggested: Optional[str] = None) -> InlineKeyboardMarkup:
    """Шаг цели в /plan: предложенная реалистичная цель (если VDOT известен) и план без цели."""
    rows = []
    if suggested:
        rows.append([InlineKeyboardButton(text=f"🎯 Цель {suggested}", callback_data=GOAL_SUGGESTED)])
    rows.append([InlineKeyboardButton(text="Без цели, по текущей форме", callback_data=GOAL_NONE)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


PLAN_GOAL_EDIT = "plan_goal:edit"
PLAN_GOAL_CLEAR = "plan_goal:clear"
PLAN_GOAL_CANCEL = "plan_goal:cancel"


def get_show_plan_keyboard(has_goal: bool, can_set_goal: bool) -> Optional[InlineKeyboardMarkup]:
    """Кнопки под /show_plan: изменить цель (если её можно оценить по VDOT) и убрать цель."""
    rows = []
    if can_set_goal:
        text = "✏️ Изменить цель" if has_goal else "🎯 Задать цель"
        rows.append([InlineKeyboardButton(text=text, callback_data=PLAN_GOAL_EDIT)])
    if has_goal:
        rows.append([InlineKeyboardButton(text="🗑 Убрать цель", callback_data=PLAN_GOAL_CLEAR)])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def get_goal_edit_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Отмена", callback_data=PLAN_GOAL_CANCEL),
    ]])
