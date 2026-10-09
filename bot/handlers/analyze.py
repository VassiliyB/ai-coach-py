# bot/handlers/analyze.py
import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from clients.ai_client import AIClientError
from clients.garmin import GarminAuthError, GarminClient
from database import async_session_maker
from services.ai_coach_service import AICoachService
from services.coach_service import build_profile_context, zones_for_profile
from services.garmin_link import RELOGIN_TEXT, reset_garmin_link
from services.message_service import MessageService
from services.user_service import UserService

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("analyze"), flags={"user_lock": "/analyze"})
async def handle_analyze(message: Message, garmin: GarminClient, ai_coach: AICoachService) -> None:
    """Анализ последней тренировки: зону по темпу и пульсу считает код, модель даёт вердикт."""
    chat_id = message.chat.id

    if not garmin.has_saved_tokens(chat_id):
        await message.answer("⚠️ Сначала подключите Garmin через <code>/start</code>.", parse_mode="HTML")
        return

    status_msg = await message.answer("🔍 Загружаю последнюю пробежку из Garmin...")

    try:
        activity = await garmin.get_last_activity(chat_id)
        if not activity:
            await status_msg.edit_text("Тренировок в аккаунте Garmin не обнаружено.")
            return

        async with async_session_maker() as session:
            user = await UserService.get_or_create_user(session, chat_id)
            profile = await UserService.get_athlete_profile(session, user.id)
            profile_dict = build_profile_context(profile)  # текст паспорта + готовые зоны темпа
            zones = zones_for_profile(profile)
            max_hr = getattr(profile, "max_heart_rate", None)

        await status_msg.edit_text("🧠 Анализирую пульс, зоны и эффект тренировки...")

        analysis_result = await ai_coach.analyze_activity(
            athlete_profile=profile_dict,
            activity=activity,
            zones=zones,
            max_hr=max_hr,
        )

        await status_msg.delete()
        for chunk in MessageService.chunk_message(analysis_result):
            await message.answer(chunk, parse_mode="HTML")

    except GarminAuthError:
        await reset_garmin_link(garmin, chat_id)   # иначе /start не покажет кнопку входа
        await status_msg.edit_text(RELOGIN_TEXT, parse_mode="HTML")
    except AIClientError as exc:
        await status_msg.edit_text(f"❌ {exc}")
    except Exception as exc:
        logger.exception("Ошибка при экспресс-анализе тренировки: %s", exc)
        await status_msg.edit_text("❌ Не удалось проанализировать пробежку. Попробуйте позже.")
