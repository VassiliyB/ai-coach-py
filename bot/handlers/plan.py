# bot/handlers/plan.py
import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from bot.keyboards import get_target_distances_keyboard
from bot.states import PlanCreationStates
from clients.ai_client import AIClientError              # <-- новый импорт
from database import async_session_maker
from models import TrainingPlan
from services.ai_coach_service import AICoachService
from services.message_service import MessageService
from services.scheduler_service import TrainingSchedulerService
from services.user_service import UserService

logger = logging.getLogger(__name__)
router = Router()

DISTANCE_NAMES = {
    "dist_5km": "5 км",
    "dist_10km": "10 км",
    "dist_21km": "21.1 км (Полумарафон)",
    "dist_42km": "42.2 км (Марафон)",
}


@router.message(Command("plan"))
async def handle_plan_start(message: Message, state: FSMContext) -> None:
    """Старт мастера составления макроплана."""
    chat_id = message.chat.id
    async with async_session_maker() as session:
        user = await UserService.get_or_create_user(session, chat_id)
        profile = await UserService.get_athlete_profile(session, user.id)

    if not profile or not profile.raw_summary_text:
        await message.answer(
            "⚠️ Сначала обновите спортивный паспорт командой <code>/sync</code>!",
            parse_mode="HTML",
        )
        return

    await state.set_state(PlanCreationStates.waiting_for_distance)
    await message.answer(
        "🎯 <b>Создание плана подготовки</b>\n\n"
        "Выберите целевую дистанцию:",
        reply_markup=get_target_distances_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(PlanCreationStates.waiting_for_distance, F.data.startswith("dist_"))
async def handle_distance_selected(callback: CallbackQuery, state: FSMContext) -> None:
    """Выбор дистанции и переход к ожиданию даты забега."""
    distance_name = DISTANCE_NAMES.get(callback.data, "Забег")

    await state.update_data(target_race=distance_name)
    await state.set_state(PlanCreationStates.waiting_for_date)

    await callback.message.edit_text(
        f"Выбрана дистанция: <b>{distance_name}</b>\n\n"
        "📅 Введите дату забега в формате <b>ДД.ММ.ГГГГ</b> (например, <code>15.06.2027</code>):",
        parse_mode="HTML",
    )
    await callback.answer()


# F.text и ~F.text.startswith("/"): команды не попадают в этот хендлер, а стикер не роняет его
@router.message(PlanCreationStates.waiting_for_date, F.text, ~F.text.startswith("/"))
async def handle_race_date_entered(
    message: Message,
    state: FSMContext,
    ai_coach: AICoachService,       # приходит из Dispatcher (DI)
) -> None:
    """Валидация даты, генерация плана через ИИ и запись в БД."""
    try:
        race_date = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
    except ValueError:
        await message.answer(
            "❌ Неверный формат! Введите дату в виде <b>ДД.ММ.ГГГГ</b> (например, <code>15.06.2027</code>):",
            parse_mode="HTML",
        )
        return

    days_left = (race_date - datetime.now().date()).days
    if days_left < 14:
        await message.answer(
            "⚠️ До забега должно быть минимум 2 недели. Укажите более позднюю дату:"
        )
        return

    total_weeks = max(2, days_left // 7)
    data = await state.get_data()
    target_race = data.get("target_race", "Бег")
    await state.clear()

    status_msg = await message.answer(
        f"🧠 <b>Тренер рассчитывает 4-фазный план ({total_weeks} нед.)...</b>\n"
        "Это займёт до минуты.",
        parse_mode="HTML",
    )

    try:
        # 1. Читаем данные и сразу закрываем сессию (на время запроса к LLM соединение не занято)
        async with async_session_maker() as session:
            user = await UserService.get_or_create_user(session, message.chat.id)
            profile = await UserService.get_athlete_profile(session, user.id)
        profile_dict = {"summary_text": profile.raw_summary_text} if profile else {}

        # 2. Генерация плана
        plan_text = await ai_coach.generate_macrocycle_plan(
            athlete_profile=profile_dict,
            target_race=target_race,
            race_date=race_date.isoformat(),
            total_weeks=total_weeks,
        )

        # 3. Новая короткая сессия только для сохранения
        async with async_session_maker() as session:
            await UserService.save_training_plan(
                session=session,
                user_id=user.id,
                target_race=target_race,
                race_date=race_date,
                total_weeks=total_weeks,
                plan_details=plan_text,
            )

        await status_msg.delete()
        for chunk in MessageService.chunk_message(plan_text):
            await message.answer(chunk, parse_mode="HTML")

    except AIClientError as exc:                              # <-- ошибка ИИ с понятным текстом
        logger.warning("Ошибка ИИ при генерации макроплана: %s", exc)
        await status_msg.edit_text(f"⚠️ {exc}")
    except Exception as exc:                                  # <-- всё остальное
        logger.exception("Ошибка при генерации макроплана: %s", exc)
        await status_msg.edit_text("❌ Произошла ошибка при составлении плана. Попробуйте повторить запрос.")


# Раньше этот хендлер был вложен внутрь функции выше. Теперь он на уровне модуля.
@router.message(Command("test_week"))
async def handle_test_week(
    message: Message,
    scheduler_service: TrainingSchedulerService,   # из Dispatcher (DI)
) -> None:
    """Ручной запуск генерации микроцикла на неделю (отладочная команда)."""
    chat_id = message.chat.id
    status_msg = await message.answer(
        "🧠 <b>Генерирую расписание на неделю по правилу 80/20...</b>",
        parse_mode="HTML",
    )

    try:
        async with async_session_maker() as session:
            user = await UserService.get_or_create_user(session, chat_id)
            profile = await UserService.get_athlete_profile(session, user.id)
            stmt = (
                select(TrainingPlan)
                .where(TrainingPlan.user_id == user.id, TrainingPlan.active.is_(True))
                .order_by(TrainingPlan.created_at.desc())
                .limit(1)
            )
            active_plan = (await session.execute(stmt)).scalars().first()

        if not active_plan:
            await status_msg.edit_text(
                "⚠️ У вас нет активного плана. Сначала создайте его через <code>/plan</code>!",
                parse_mode="HTML",
            )
            return

        success = await scheduler_service.generate_and_send_microcycle_for_user(
            user=user,
            plan=active_plan,
            profile=profile,
        )

        if success:
            await status_msg.delete()
        else:
            await status_msg.edit_text("❌ Не удалось сформировать недельный план. Проверьте логи.")

    except Exception as exc:
        logger.exception("Ошибка при вызове /test_week: %s", exc)
        await status_msg.edit_text("❌ Произошла ошибка при генерации расписания.")