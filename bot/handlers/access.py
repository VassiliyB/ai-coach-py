# bot/handlers/access.py
"""Доступ к боту: запрос от нового пользователя, одобрение и отзыв админом.

Запрос (/start без доступа) обрабатывает AccessMiddleware через handle_access_request:
до роутеров такое сообщение не доходит. Команды и кнопки здесь только для админов.
"""
import logging
from typing import Optional, Tuple

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandObject, Filter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bot.access_panel import PANEL_APPROVE_PREFIX, PANEL_BLOCK_PREFIX, describe_user, render_users_panel
from bot.keyboards import ACCESS_APPROVE_PREFIX, ACCESS_REJECT_PREFIX, get_access_request_keyboard
from database import async_session_maker
from models import AppUser
from models.user import ACCESS_APPROVED, ACCESS_BLOCKED
from services.access_control import AccessControl
from services.user_service import UserService
from services.user_time import local_month_start

logger = logging.getLogger(__name__)
router = Router()


class IsAdmin(Filter):
    """Админ из ADMIN_CHAT_IDS; у остальных команды админа не срабатывают вовсе."""

    async def __call__(self, event: Message | CallbackQuery, access: AccessControl) -> bool:
        chat = event.chat if isinstance(event, Message) else (event.message.chat if event.message else None)
        return chat is not None and access.is_admin(chat.id)


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def _notify(bot: Bot, chat_id: int, text: str, **kwargs) -> bool:
    """Сообщение без падения: пользователь мог заблокировать бота, админ мог ещё не писать ему."""
    try:
        await bot.send_message(chat_id, text, **kwargs)
        return True
    except TelegramAPIError as exc:
        logger.warning("Не удалось отправить сообщение (chat_id=%s): %s", chat_id, exc)
        return False


async def handle_access_request(message: Message, bot: Bot, access: AccessControl) -> None:
    """/start от пользователя без доступа: новый запрос уходит админам, иначе ответ о статусе."""
    chat_id = message.chat.id
    async with async_session_maker() as session:
        user, created = await UserService.request_access(
            session, chat_id, message.from_user.username, message.from_user.first_name,
        )

    if user.access == ACCESS_APPROVED:    # одобрен в БД, но реестр не знал (например, правка вручную)
        access.approve(chat_id)
        await message.answer("✅ Доступ открыт. Отправьте /start ещё раз.")
        return
    if user.access == ACCESS_BLOCKED:
        await message.answer("⛔ Доступ к боту закрыт.")
        return
    if not created:
        await message.answer("⏳ Запрос уже у администратора. Я напишу, когда доступ откроют.")
        return

    sent = 0
    for admin_id in access.admin_ids:
        sent += await _notify(
            bot, admin_id, f"🙋 Запрос доступа к боту:\n{describe_user(user)}",
            reply_markup=get_access_request_keyboard(chat_id), parse_mode="HTML",
        )
    if not sent:
        logger.warning("Запрос доступа (chat_id=%s) не доставлен ни одному админу: проверьте ADMIN_CHAT_IDS", chat_id)
    logger.info("Новый запрос доступа (chat_id=%s)", chat_id)
    await message.answer(
        "🔒 Бот работает по приглашению.\n"
        "Запрос отправлен администратору, я напишу, когда доступ откроют."
    )


async def _set_access(bot: Bot, access: AccessControl, chat_id: int, new_access: str) -> Optional[AppUser]:
    """Меняет доступ в БД и в реестре и сообщает пользователю. None, если пользователя нет в БД."""
    async with async_session_maker() as session:
        user = await UserService.set_access(session, chat_id, new_access)
    if user is None:
        return None
    if new_access == ACCESS_APPROVED:
        access.approve(chat_id)
        await _notify(bot, chat_id, "✅ Доступ к боту открыт! Отправьте /start, чтобы начать.")
    else:
        access.revoke(chat_id)
        await _notify(bot, chat_id, "⛔ Доступ к боту закрыт.")
    logger.info("Доступ пользователя chat_id=%s: %s", chat_id, new_access)
    return user


def _target_id(callback_data: str, prefix: str) -> Optional[int]:
    try:
        return int(callback_data.removeprefix(prefix))
    except ValueError:
        return None


@router.callback_query(F.data.startswith(ACCESS_APPROVE_PREFIX))
async def handle_approve_button(callback: CallbackQuery, bot: Bot, access: AccessControl) -> None:
    await _handle_decision(callback, bot, access, ACCESS_APPROVE_PREFIX, ACCESS_APPROVED, "✅ Доступ открыт")


@router.callback_query(F.data.startswith(ACCESS_REJECT_PREFIX))
async def handle_reject_button(callback: CallbackQuery, bot: Bot, access: AccessControl) -> None:
    await _handle_decision(callback, bot, access, ACCESS_REJECT_PREFIX, ACCESS_BLOCKED, "⛔ Отказано")


async def _handle_decision(
    callback: CallbackQuery, bot: Bot, access: AccessControl, prefix: str, new_access: str, verdict: str,
) -> None:
    chat_id = _target_id(callback.data, prefix)
    if chat_id is None:
        await callback.answer()
        return
    user = await _set_access(bot, access, chat_id, new_access)
    if user is None:
        await callback.message.edit_text(f"Пользователя <code>{chat_id}</code> уже нет в базе.", parse_mode="HTML")
    else:
        await callback.message.edit_text(f"{verdict}: {describe_user(user)}", parse_mode="HTML")
    await callback.answer()


@router.message(Command("approve"))
async def handle_approve(message: Message, command: CommandObject, bot: Bot, access: AccessControl) -> None:
    await _handle_command(message, command, bot, access, ACCESS_APPROVED)


@router.message(Command("revoke"))
async def handle_revoke(message: Message, command: CommandObject, bot: Bot, access: AccessControl) -> None:
    await _handle_command(message, command, bot, access, ACCESS_BLOCKED)


async def _handle_command(
    message: Message, command: CommandObject, bot: Bot, access: AccessControl, new_access: str,
) -> None:
    name = "/approve" if new_access == ACCESS_APPROVED else "/revoke"
    try:
        chat_id = int((command.args or "").strip())
    except ValueError:
        await message.answer(
            f"Укажите chat_id: <code>{name} 123456789</code>. Список пользователей: /users", parse_mode="HTML",
        )
        return
    if access.is_admin(chat_id):
        await message.answer("У админа доступ есть всегда: его задаёт ADMIN_CHAT_IDS в .env.")
        return
    user = await _set_access(bot, access, chat_id, new_access)
    if user is None:
        await message.answer(
            f"Пользователя <code>{chat_id}</code> нет в базе: он ещё не писал боту /start.", parse_mode="HTML",
        )
        return
    verdict = "✅ Доступ открыт" if new_access == ACCESS_APPROVED else "⛔ Доступ закрыт"
    await message.answer(f"{verdict}: {describe_user(user)}", parse_mode="HTML")


async def _panel(admin_chat_id: int, access: AccessControl) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """Панель со списком и расходом токенов за текущий месяц по поясу админа."""
    async with async_session_maker() as session:
        users = await UserService.list_users(session)
        since = local_month_start(await UserService.timezone_for_chat(session, admin_chat_id))
        usage = await UserService.usage_totals_since(session, since)
    return render_users_panel(users, access.admin_ids, usage, since.date())


@router.message(Command("users"))
async def handle_users(message: Message, access: AccessControl) -> None:
    """Панель доступа: список пользователей с кнопками; нажатие обновляет список на месте."""
    text, markup = await _panel(message.chat.id, access)
    await message.answer(text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data.startswith(PANEL_APPROVE_PREFIX) | F.data.startswith(PANEL_BLOCK_PREFIX))
async def handle_panel_button(callback: CallbackQuery, bot: Bot, access: AccessControl) -> None:
    approve = callback.data.startswith(PANEL_APPROVE_PREFIX)
    prefix = PANEL_APPROVE_PREFIX if approve else PANEL_BLOCK_PREFIX
    chat_id = _target_id(callback.data, prefix)
    if chat_id is None or access.is_admin(chat_id):
        await callback.answer()
        return

    user = await _set_access(bot, access, chat_id, ACCESS_APPROVED if approve else ACCESS_BLOCKED)
    if user is None:
        notice = "Пользователя уже нет в базе."
    else:
        notice = f"{'✅ Доступ открыт' if approve else '⛔ Доступ закрыт'}: {user.first_name or chat_id}"

    text, markup = await _panel(callback.message.chat.id, access)
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest as exc:   # повторное нажатие: список не изменился
        if "message is not modified" not in str(exc):
            raise
    await callback.answer(notice)
