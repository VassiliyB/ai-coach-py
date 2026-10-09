# bot/handlers/access.py
"""Доступ к боту: запрос от нового пользователя, одобрение и отзыв админом.

Запрос (/start без доступа) обрабатывает AccessMiddleware через handle_access_request:
до роутеров такое сообщение не доходит. Команды и кнопки здесь только для админов.
"""
import html
import logging
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, Filter
from aiogram.types import CallbackQuery, Message

from bot.keyboards import ACCESS_APPROVE_PREFIX, ACCESS_REJECT_PREFIX, get_access_request_keyboard
from database import async_session_maker
from models import AppUser
from models.user import ACCESS_APPROVED, ACCESS_BLOCKED, ACCESS_PENDING
from services.access_control import AccessControl
from services.message_service import MessageService
from services.user_service import UserService

logger = logging.getLogger(__name__)
router = Router()

ACCESS_ICONS = {ACCESS_APPROVED: "✅", ACCESS_PENDING: "⏳", ACCESS_BLOCKED: "⛔"}
ACCESS_TITLES = {ACCESS_APPROVED: "С доступом", ACCESS_PENDING: "Ждут одобрения", ACCESS_BLOCKED: "Без доступа"}

class IsAdmin(Filter):
    """Админ из ADMIN_CHAT_IDS; у остальных команды админа не срабатывают вовсе."""

    async def __call__(self, event: Message | CallbackQuery, access: AccessControl) -> bool:
        chat = event.chat if isinstance(event, Message) else (event.message.chat if event.message else None)
        return chat is not None and access.is_admin(chat.id)


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def describe_user(user: AppUser) -> str:
    """Строка о пользователе для админа (HTML)."""
    parts = [f"<code>{user.telegram_chat_id}</code>"]
    if user.first_name:
        parts.append(html.escape(user.first_name))
    if user.username:
        parts.append(f"@{html.escape(user.username)}")
    return " ".join(parts)


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


@router.message(Command("users"))
async def handle_users(message: Message, access: AccessControl) -> None:
    async with async_session_maker() as session:
        users = await UserService.list_users(session)
    if not users:
        await message.answer("Пользователей пока нет.")
        return

    lines = ["👥 <b>Пользователи бота</b>"]
    for status in (ACCESS_PENDING, ACCESS_APPROVED, ACCESS_BLOCKED):
        group = [u for u in users if u.access == status]
        if not group:
            continue
        lines.append(f"\n{ACCESS_ICONS[status]} <b>{ACCESS_TITLES[status]}</b> ({len(group)})")
        for user in group:
            mark = " (админ)" if access.is_admin(user.telegram_chat_id) else ""
            lines.append(f"• {describe_user(user)}{mark}, с {user.created_at:%d.%m.%Y}")
    lines.append("\nОткрыть доступ: <code>/approve chat_id</code>, закрыть: <code>/revoke chat_id</code>")
    for chunk in MessageService.chunk_message("\n".join(lines)):
        await message.answer(chunk, parse_mode="HTML")
