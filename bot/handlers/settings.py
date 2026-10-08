# bot/handlers/settings.py
import html
import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from database import async_session_maker
from services.user_service import UserService
from services.user_time import local_now, normalize_timezone

logger = logging.getLogger(__name__)
router = Router()

TIMEZONE_HELP = (
    "Укажите пояс названием или смещением от UTC, например:\n"
    "• <code>/timezone Asia/Almaty</code>\n"
    "• <code>/timezone Europe/Moscow</code>\n"
    "• <code>/timezone +5</code> или <code>/timezone UTC+05:30</code>\n\n"
    "Название учитывает переход на летнее время, смещение нет."
)


@router.message(Command("timezone"))
async def handle_timezone(message: Message, command: CommandObject) -> None:
    """Просмотр и смена часового пояса: от него зависят даты недель и время воскресной рассылки."""
    async with async_session_maker() as session:
        user = await UserService.get_or_create_user(session, message.chat.id)

        if not command.args:
            current = UserService.timezone_of(user)
            source = html.escape(user.timezone) if user.timezone else "по умолчанию"
            await message.answer(
                f"🕒 Ваш часовой пояс: <b>{source}</b>, сейчас у вас "
                f"<b>{local_now(current):%H:%M %d.%m}</b>.\n"
                "Расписание на неделю приходит в воскресенье после 15:00 по этому времени.\n\n"
                f"{TIMEZONE_HELP}",
                parse_mode="HTML",
            )
            return

        tz = normalize_timezone(command.args)
        if tz is None:
            await message.answer(
                f"❌ Не удалось распознать пояс «{html.escape(command.args.strip()[:50])}».\n\n{TIMEZONE_HELP}",
                parse_mode="HTML",
            )
            return

        await UserService.set_timezone(session, user.id, tz)
        user.timezone = tz

    await message.answer(
        f"✅ Часовой пояс: <b>{html.escape(tz)}</b>, сейчас у вас "
        f"<b>{local_now(UserService.timezone_of(user)):%H:%M %d.%m}</b>.",
        parse_mode="HTML",
    )
