# bot/commands.py
"""Меню команд Telegram (кнопка слева от поля ввода): общее для всех и расширенное для админов.

Меню задаётся при каждом запуске бота, поэтому оно всегда совпадает с кодом.
Тот же список выводит приветствие /start.
"""
import logging
from typing import Iterable, List, Tuple

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat

logger = logging.getLogger(__name__)

# (команда без "/", описание): описание до 256 символов, команда строчными латинскими буквами
USER_COMMANDS: List[Tuple[str, str]] = [
    ("start", "Начало работы и подключение Garmin"),
    ("sync", "Обновить спортивный паспорт за 90 дней"),
    ("plan", "Составить макроплан к забегу"),
    ("show_plan", "Текущий план, неделя и цель на забег"),
    ("race", "Ввести результат забега: точные темпы"),
    ("analyze", "Разобрать последнюю пробежку"),
    ("ask", "Вопрос тренеру (по книгам Дэниелса и Фицджеральда)"),
    ("timezone", "Часовой пояс для расписаний"),
    ("delete_me", "Удалить все свои данные"),
]

# /approve и /revoke с chat_id работают, но в меню их нет: без аргумента пункт меню бесполезен, кнопки в /users удобнее
ADMIN_COMMANDS: List[Tuple[str, str]] = [
    ("users", "Пользователи и доступ: открыть или закрыть кнопками"),
    ("test_week", "Сгенерировать следующую неделю сейчас"),
]


def _to_bot_commands(commands: Iterable[Tuple[str, str]]) -> List[BotCommand]:
    return [BotCommand(command=name, description=description) for name, description in commands]


def commands_help() -> str:
    """Список команд для приветствия /start (HTML), без самой /start."""
    return "\n".join(
        f"• <code>/{name}</code> — {description[0].lower()}{description[1:]}"
        for name, description in USER_COMMANDS if name != "start"
    )


async def setup_bot_commands(bot: Bot, admin_ids: Iterable[int]) -> None:
    """Общее меню для личных чатов и расширенное (общее + команды админа) в чате каждого админа."""
    await bot.set_my_commands(_to_bot_commands(USER_COMMANDS), scope=BotCommandScopeAllPrivateChats())
    admin_menu = _to_bot_commands(USER_COMMANDS + ADMIN_COMMANDS)
    for admin_id in admin_ids:
        try:
            await bot.set_my_commands(admin_menu, scope=BotCommandScopeChat(chat_id=admin_id))
        except TelegramAPIError as exc:   # админ ещё не писал боту: чат Telegram не найден
            logger.warning("Не удалось задать меню админа (chat_id=%s): %s", admin_id, exc)
