# bot/handlers/analyze.py
import html
import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from clients.ai_client import AIClientError
from clients.garmin import GarminAuthError, GarminClient, GarminRateLimitError
from database import async_session_maker
from services.ai_coach_service import AICoachService
from services.message_service import MessageService
from services.user_service import UserService

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("analyze"))
async def handle_analyze(message: Message, garmin: GarminClient, ai_coach: AICoachService) -> None:
    """Анализ последней пробежки («План vs Факт», зоны, серая зона)."""
    chat_id = message.chat.id

    if not garmin.has_saved_tokens(chat_id):
        await message.answer(
            "⚠️ Сначала подключите Garmin через <code>/start</code>.", parse_mode="HTML"
        )
        return

    status_msg = await message.answer("🔍 Загружаю последнюю тренировку из Garmin...")

    try:
        activity = await garmin.get_last_activity(chat_id)
        if not activity:
            await status_msg.edit_text("Тренировок в аккаунте Garmin не обнаружено.")
            return

        if not activity.get("is_running"):
            kind = html.escape(str(activity.get("activity_type", "неизвестно")))
            await status_msg.edit_text(
                f"ℹ️ Последняя активность — не бег (тип: <code>{kind}</code>). "
                "Анализ пока работает только для пробежек.",
                parse_mode="HTML",
            )
            return

        async with async_session_maker() as session:
            user = await UserService.get_or_create_user(session, chat_id)
            profile = await UserService.get_athlete_profile(session, user.id)
        profile_dict = {"summary_text": profile.raw_summary_text} if profile else {}

        await status_msg.edit_text("🧠 Анализирую пульс, зоны и эффект тренировки...")

        analysis = await ai_coach.analyze_activity(
            athlete_profile=profile_dict,
            activity=activity,
        )

        await status_msg.delete()
        for chunk in MessageService.chunk_message(analysis):
            await message.answer(chunk, parse_mode="HTML")

    except GarminRateLimitError as exc:
        await status_msg.edit_text(f"⚠️ {exc}")
    except GarminAuthError:
        await status_msg.edit_text(
            "❌ Сессия Garmin истекла. Выполните повторный вход через <code>/start</code>.",
            parse_mode="HTML",
        )
    except AIClientError as exc:
        logger.warning("Ошибка ИИ при анализе (chat_id=%s): %s", chat_id, exc)
        await status_msg.edit_text(f"⚠️ {exc}")
    except Exception:
        logger.exception("Ошибка экспресс-анализа (chat_id=%s)", chat_id)
        await status_msg.edit_text("❌ Не удалось проанализировать пробежку. Попробуйте позже.")