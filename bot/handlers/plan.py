# bot/handlers/plan.py
import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards import get_target_distances_keyboard
from bot.states import PlanCreationStates
from database import async_session_maker
from repositories import PlanRepository
from services.ai_coach_service import AICoachService
from services.coach_service import build_profile_context
from services.message_service import MessageService
from services.scheduler_service import TrainingSchedulerService
from services.user_service import UserService

logger = logging.getLogger(__name__)
router = Router()
ai_coach = AICoachService()

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
        await message.answer("⚠️ Сначала обновите спортивный паспорт командой <code>/sync</code>!", parse_mode="HTML")
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
    """Выбор дистанции и переход к ожиданию даты старта."""
    distance_name = DISTANCE_NAMES.get(callback.data, "Забег")

    await state.update_data(target_race=distance_name)
    await state.set_state(PlanCreationStates.waiting_for_date)

    await callback.message.edit_text(
        f"Выбрана дистанция: <b>{distance_name}</b>\n\n"
        "📅 Введите дату забега в формате: <b>ДД.ММ.ГГГГ</b> (например, <code>15.06.2027</code>):",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(PlanCreationStates.waiting_for_date)
async def handle_race_date_entered(message: Message, state: FSMContext) -> None:
    """Валидация даты, генерация плана через ИИ и запись в БД."""
    date_text = (message.text or "").strip()
    try:
        race_date = datetime.strptime(date_text, "%d.%m.%Y").date()
    except ValueError:
        await message.answer(
            "❌ Неверный формат! Введите дату в виде <b>ДД.ММ.ГГГГ</b> (например, <code>15.06.2027</code>):",
            parse_mode="HTML",
        )
        return

    days_left = (race_date - datetime.now().date()).days
    if days_left < 14:
        await message.answer(
            "⚠️ До старта должно быть минимум 2 недели для построения периодизации. Укажите более позднюю дату:"
        )
        return

    total_weeks = max(2, days_left // 7)
    data = await state.get_data()
    target_race = data.get("target_race", "Бег")
    await state.clear()

    status_msg = await message.answer(
        f"🧠 <b>Тренер рассчитывает 4-фазный план ({total_weeks} нед.)...</b>\n"
        "Это займет несколько секунд.",
        parse_mode="HTML",
    )

    try:
        # 1. Читаем профиль и сразу закрываем сессию: на время вызова LLM соединение с БД не держим
        async with async_session_maker() as session:
            user = await UserService.get_or_create_user(session, message.chat.id)
            profile = await UserService.get_athlete_profile(session, user.id)
            user_id = user.id
            profile_dict = build_profile_context(profile)  # текст паспорта + готовые зоны темпа

        # 2. Генерация плана через спортивный ИИ
        plan_text = await ai_coach.generate_macrocycle_plan(
            athlete_profile=profile_dict,
            target_race=target_race,
            race_date=race_date.isoformat(),
            total_weeks=total_weeks,
        )

        # 3. Сохранение в БД (старый активный план деактивируется в той же транзакции)
        async with async_session_maker() as session:
            await UserService.save_training_plan(
                session=session,
                user_id=user_id,
                target_race=target_race,
                race_date=race_date,
                total_weeks=total_weeks,
                plan_details=plan_text,
            )

        await status_msg.delete()

        # Безопасная нарезка ответа (лимит Telegram 4096 символов)
        for chunk in MessageService.chunk_message(plan_text):
            await message.answer(chunk, parse_mode="HTML")

    except Exception as exc:
        logger.exception("Ошибка при генерации макроплана: %s", exc)
        await status_msg.edit_text("❌ Произошла ошибка при составлении плана. Попробуйте повторить запрос.")


@router.message(Command("test_week"))
async def handle_test_week(message: Message) -> None:
    """Ручной запуск генерации микроцикла на неделю (отладочная команда)."""
    chat_id = message.chat.id
    status_msg = await message.answer(
        "🧠 <b>Генерирую расписание на неделю по правилу 80/20...</b>", parse_mode="HTML"
    )

    try:
        async with async_session_maker() as session:
            user = await UserService.get_or_create_user(session, chat_id)
            profile = await UserService.get_athlete_profile(session, user.id)
            active_plan = await PlanRepository(session).get_active(user.id)

        if not active_plan:
            await status_msg.edit_text(
                "⚠️ У вас нет активного плана подготовки. Сначала создайте его через <code>/plan</code>!",
                parse_mode="HTML",
            )
            return

        scheduler_service = TrainingSchedulerService(bot=message.bot, ai_coach=ai_coach)
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