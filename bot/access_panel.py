# bot/access_panel.py
"""Панель /users: список пользователей по статусу доступа и кнопки смены доступа (без I/O)."""
import html
from typing import Collection, List, Sequence, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from models import AppUser
from models.user import ACCESS_APPROVED, ACCESS_BLOCKED, ACCESS_PENDING

PANEL_APPROVE_PREFIX = "users:approve:"
PANEL_BLOCK_PREFIX = "users:block:"

ACCESS_ICONS = {ACCESS_APPROVED: "✅", ACCESS_PENDING: "⏳", ACCESS_BLOCKED: "⛔"}
ACCESS_TITLES = {ACCESS_APPROVED: "С доступом", ACCESS_PENDING: "Ждут одобрения", ACCESS_BLOCKED: "Без доступа"}
STATUS_ORDER = (ACCESS_PENDING, ACCESS_APPROVED, ACCESS_BLOCKED)

# Подсказка под списком есть всегда: без неё панель из одного админа непонятна
HELP_WITH_USERS = (
    "\n<b>Как управлять доступом</b>\n"
    "• Нажмите кнопку под списком: «Пустить» / «Отказать» для новых, "
    "«Закрыть доступ» или «Вернуть доступ» для остальных.\n"
    "• Пользователь получит уведомление, список обновится здесь же.\n"
    "• Новые запросы приходят вам отдельным сообщением с теми же кнопками."
)
HELP_ONLY_ADMINS = (
    "\n<b>Как пустить человека</b>\n"
    "1. Дайте ему ссылку на бота, пусть отправит /start.\n"
    "2. Вам придёт сообщение «Запрос доступа» с кнопками «Пустить» / «Отказать».\n"
    "3. После этого он появится здесь, и доступ можно будет закрыть или вернуть кнопкой."
)

BUTTON_NAME_LIMIT = 24   # имя на кнопке; длинное обрезается, полное есть в тексте над кнопками


def describe_user(user: AppUser) -> str:
    """Строка о пользователе для админа (HTML)."""
    parts = [f"<code>{user.telegram_chat_id}</code>"]
    if user.first_name:
        parts.append(html.escape(user.first_name))
    if user.username:
        parts.append(f"@{html.escape(user.username)}")
    return " ".join(parts)


def button_name(user: AppUser) -> str:
    """Короткое имя для кнопки (обычный текст, не HTML)."""
    name = user.first_name or (f"@{user.username}" if user.username else str(user.telegram_chat_id))
    return name if len(name) <= BUTTON_NAME_LIMIT else name[: BUTTON_NAME_LIMIT - 1] + "…"


def _buttons(user: AppUser) -> List[InlineKeyboardButton]:
    chat_id, name = user.telegram_chat_id, button_name(user)
    approve = f"{PANEL_APPROVE_PREFIX}{chat_id}"
    block = f"{PANEL_BLOCK_PREFIX}{chat_id}"
    if user.access == ACCESS_PENDING:
        return [
            InlineKeyboardButton(text=f"✅ Пустить: {name}", callback_data=approve),
            InlineKeyboardButton(text="⛔ Отказать", callback_data=block),
        ]
    if user.access == ACCESS_APPROVED:
        return [InlineKeyboardButton(text=f"⛔ Закрыть доступ: {name}", callback_data=block)]
    return [InlineKeyboardButton(text=f"✅ Вернуть доступ: {name}", callback_data=approve)]


def render_users_panel(
    users: Sequence[AppUser], admin_ids: Collection[int],
) -> Tuple[str, InlineKeyboardMarkup | None]:
    """Текст списка (HTML) и кнопки: по строке на пользователя, у админов кнопок нет."""
    if not users:
        return "Пользователей пока нет.", None

    lines = ["👥 <b>Пользователи бота</b>"]
    rows: List[List[InlineKeyboardButton]] = []
    for status in STATUS_ORDER:
        group = [u for u in users if u.access == status]
        if not group:
            continue
        lines.append(f"\n{ACCESS_ICONS[status]} <b>{ACCESS_TITLES[status]}</b> ({len(group)})")
        for user in group:
            is_admin = user.telegram_chat_id in admin_ids
            mark = " (админ)" if is_admin else ""
            lines.append(f"• {describe_user(user)}{mark}, с {user.created_at:%d.%m.%Y}")
            if not is_admin:
                rows.append(_buttons(user))
    lines.append(HELP_WITH_USERS if rows else HELP_ONLY_ADMINS)
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
