# bot/handlers/sync.py
import logging
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from database import async_session_maker
from services.user_service import UserService
from clients.garmin import GarminClient, GarminAuthError

logger = logging.getLogger(__name__)
router = Router()
garmin_client = GarminClient()


@router.message(Command("sync"))
async def handle_sync(message: Message) -> None:
    """Выгрузка тренировок за 90 дней и формирование паспорта атлета."""
    chat_id = message.chat.id

    if not garmin_client.has_saved_tokens(chat_id):
        await message.answer("⚠️ Garmin Connect не подключен. Введите <code>/start</code> для авторизации.", parse_mode="HTML")
        return

    status_msg = await message.answer("⏳ Загружаю и анализирую тренировки за последние 90 дней...")

    try:
        profile_data = await garmin_client.get_athlete_profile_90d(chat_id=chat_id, days=90)

        async with async_session_maker() as session:
            user = await UserService.get_or_create_user(session, chat_id)
            await UserService.save_athlete_profile(session, user.id, profile_data)

        await status_msg.edit_text(
            f"📊 <b>Ваш спортивный паспорт обновлен:</b>\n\n"
            f"{profile_data['summary_text']}\n\n"
            "Теперь можно составить целевой макроплан через <code>/plan</code>!",
            parse_mode="HTML",
        )
    except GarminAuthError:
        await status_msg.edit_text("❌ Сессия Garmin истекла. Пожалуйста, выполните повторный вход через <code>/start</code>.")
    except Exception as exc:
        logger.exception("Ошибка при синхронизации профиля: %s", exc)
        await status_msg.edit_text("❌ Не удалось получить данные из Garmin. Попробуйте позже.")