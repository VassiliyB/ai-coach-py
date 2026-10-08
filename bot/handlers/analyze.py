# bot/handlers/analyze.py
import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from clients.garmin import GarminAuthError, GarminClient
from database import async_session_maker
from services.ai_coach_service import AICoachService
from services.coach_service import build_profile_context
from services.message_service import MessageService
from services.user_service import UserService

logger = logging.getLogger(__name__)
router = Router()
garmin_client = GarminClient()
ai_coach = AICoachService()


@router.message(Command("analyze"))
async def handle_analyze(message: Message) -> None:
    """Анализ последней тренировки («План vs Факт», зоны, серые зоны)."""
    chat_id = message.chat.id

    if not garmin_client.has_saved_tokens(chat_id):
        await message.answer("⚠️ Сначала подключите Garmin через <code>/start</code>.", parse_mode="HTML")
        return

    status_msg = await message.answer("🔍 Загружаю последнюю пробежку из Garmin...")

    try:
        activity = await garmin_client.get_last_activity(chat_id)
        if not activity:
            await status_msg.edit_text("Тренировок в аккаунте Garmin не обнаружено.")
            return

        async with async_session_maker() as session:
            user = await UserService.get_or_create_user(session, chat_id)
            profile = await UserService.get_athlete_profile(session, user.id)
            profile_dict = build_profile_context(profile)  # текст паспорта + готовые зоны темпа

        await status_msg.edit_text("🧠 Анализирую пульс, зоны и эффект тренировки...")

        analysis_result = await ai_coach.analyze_activity(
            athlete_profile=profile_dict,
            activity=activity,
        )

        await status_msg.delete()
        for chunk in MessageService.chunk_message(analysis_result):
            await message.answer(chunk, parse_mode="HTML")

    except GarminAuthError:
        await status_msg.edit_text(
            "❌ Сессия Garmin истекла. Выполните повторный вход через <code>/start</code>.",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.exception("Ошибка при экспресс-анализе тренировки: %s", exc)
        await status_msg.edit_text("❌ Не удалось проанализировать пробежку. Попробуйте позже.")