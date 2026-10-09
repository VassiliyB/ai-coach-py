# bot/handlers/sync.py
import html
import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from clients.garmin import GarminAuthError, GarminClient, GarminRateLimitError
from database import async_session_maker
from services.garmin_link import RELOGIN_TEXT, reset_garmin_link
from services.message_service import MessageService
from services.user_service import UserService

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("sync"), flags={"user_lock": "/sync"})
async def handle_sync(message: Message, garmin: GarminClient) -> None:
    """Выгрузка тренировок за 90 дней и обновление паспорта атлета."""
    chat_id = message.chat.id

    if not garmin.has_saved_tokens(chat_id):
        await message.answer(
            "⚠️ Garmin Connect не подключен. Введите <code>/start</code> для авторизации.",
            parse_mode="HTML",
        )
        return

    status_msg = await message.answer("⏳ Загружаю и анализирую тренировки за 90 дней...")

    try:
        profile_data = await garmin.get_athlete_profile_90d(chat_id=chat_id, days=90)

        detected_tz = profile_data.get("utc_offset")
        async with async_session_maker() as session:
            user = await UserService.get_or_create_user(session, chat_id)
            await UserService.save_athlete_profile(session, user.id, profile_data)
            # Пояс, заданный вручную через /timezone, не перезаписываем
            tz_saved = user.timezone is None and detected_tz is not None
            if tz_saved:
                await UserService.set_timezone(session, user.id, detected_tz)

        await status_msg.delete()

        tz_line = (
            f"🕒 Часовой пояс по данным Garmin: <b>{detected_tz}</b>. Изменить: <code>/timezone</code>\n\n"
            if tz_saved else ""
        )
        text = (
            "📊 <b>Ваш спортивный паспорт обновлён:</b>\n\n"
            f"{html.escape(profile_data['summary_text'])}\n\n"
            f"{tz_line}"
            "Теперь можно составить макроплан через <code>/plan</code>!"
        )
        for chunk in MessageService.chunk_message(text):
            await message.answer(chunk, parse_mode="HTML")

    except GarminRateLimitError as exc:
        await status_msg.edit_text(f"⚠️ {exc}")
    except GarminAuthError:
        await reset_garmin_link(garmin, chat_id)   # иначе /start не покажет кнопку входа
        await status_msg.edit_text(RELOGIN_TEXT, parse_mode="HTML")
    except Exception:
        logger.exception("Ошибка синхронизации профиля (chat_id=%s)", chat_id)
        await status_msg.edit_text("❌ Не удалось получить данные из Garmin. Попробуйте позже.")
