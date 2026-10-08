# bot/handlers/plan.py
import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards import get_target_distances_keyboard
from bot.states import PlanCreationStates
from clients.ai_client import AIClientError
from database import async_session_maker
from services.coach_service import build_profile_context, zones_for_profile
from services.message_service import MessageService
from services.plan_calendar import plan_total_weeks
from services.plan_generator import PlanGenerationError, PlanGenerator
from services.plan_renderer import render_macro
from services.scheduler_service import TrainingSchedulerService, WeekStatus
from services.user_service import UserService
from services.user_time import local_today

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


@router.message(PlanCreationStates.waiting_for_date, flags={"user_lock": "составление плана"})
async def handle_race_date_entered(message: Message, state: FSMContext, plan_generator: PlanGenerator) -> None:
    """Валидация даты, генерация структурного макроплана, запись в БД и показ пользователю."""
    date_text = (message.text or "").strip()
    try:
        race_date = datetime.strptime(date_text, "%d.%m.%Y").date()
    except ValueError:
        await message.answer(
            "❌ Неверный формат! Введите дату в виде <b>ДД.ММ.ГГГГ</b> (например, <code>15.06.2027</code>):",
            parse_mode="HTML",
        )
        return

    async with async_session_maker() as session:
        user = await UserService.get_or_create_user(session, message.chat.id)
    today = local_today(UserService.timezone_of(user))  # дата у пользователя, а не на сервере

    days_left = (race_date - today).days
    if days_left < 14:
        await message.answer(
            "⚠️ До старта должно быть минимум 2 недели для построения периодизации. Укажите более позднюю дату:"
        )
        return

    # Тот же календарь, что у рассылки: от следующего понедельника до недели забега
    total_weeks = max(2, plan_total_weeks(today, race_date))
    data = await state.get_data()
    target_race = data.get("target_race", "Бег")
    await state.clear()

    status_msg = await message.answer(
        f"🧠 <b>Тренер рассчитывает 4-фазный план ({total_weeks} нед.)...</b>\n"
        "Это может занять до минуты.",
        parse_mode="HTML",
    )

    try:
        # 1. Читаем профиль и сразу закрываем сессию: на время вызова LLM соединение с БД не держим
        async with async_session_maker() as session:
            user = await UserService.get_or_create_user(session, message.chat.id)
            profile = await UserService.get_athlete_profile(session, user.id)
            user_id = user.id
            profile_dict = build_profile_context(profile)
            zones = zones_for_profile(profile)

        # 2. Модель выбирает фазы и километраж; схема и правила проверяются, при замечаниях повтор
        macro = await plan_generator.generate_macro(
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
                plan=macro,
            )

        await status_msg.delete()

        # 4. Текст собирает код: зоны темпа из VDOT, текст модели экранирован
        text = render_macro(macro, target_race, race_date=race_date, zones=zones)
        for chunk in MessageService.chunk_message(text):
            await message.answer(chunk, parse_mode="HTML")

    except PlanGenerationError as exc:
        logger.warning("Макроплан не прошёл проверки (chat_id=%s): %s", message.chat.id, exc.problems)
        await status_msg.edit_text(
            "❌ Не получилось составить план, который проходит проверку по правилам тренировок. "
            "Попробуйте ещё раз через /plan."
        )
    except AIClientError as exc:
        # Текст ошибки клиента уже адресован пользователю (лимит запросов, сеть и т.п.)
        await status_msg.edit_text(f"❌ {exc}")
    except Exception as exc:
        logger.exception("Ошибка при генерации макроплана: %s", exc)
        await status_msg.edit_text("❌ Произошла ошибка при составлении плана. Попробуйте повторить запрос.")


@router.message(Command("test_week"), flags={"user_lock": "/test_week"})
async def handle_test_week(message: Message, scheduler_service: TrainingSchedulerService) -> None:
    """Ручной запуск генерации микроцикла на неделю (отладочная команда)."""
    chat_id = message.chat.id
    status_msg = await message.answer(
        "🧠 <b>Генерирую расписание на следующую неделю по правилу 80/20...</b>", parse_mode="HTML"
    )

    try:
        async with async_session_maker() as session:
            user = await UserService.get_or_create_user(session, chat_id)
            profile = await UserService.get_athlete_profile(session, user.id)
            active_plan = await UserService.get_active_plan(session, user.id)

        if not active_plan:
            await status_msg.edit_text(
                "⚠️ У вас нет активного плана подготовки. Сначала создайте его через <code>/plan</code>!",
                parse_mode="HTML",
            )
            return

        today = local_today(UserService.timezone_of(user))
        status = await scheduler_service.send_week(user=user, plan=active_plan, profile=profile, today=today)

        if status == WeekStatus.FINISHED:
            await status_msg.edit_text(
                "🏁 Забег по текущему плану уже прошёл. Создайте новый план через <code>/plan</code>.",
                parse_mode="HTML",
            )
        else:
            # Расписание или подсказка про старый формат плана уже отправлены
            await status_msg.delete()

    except PlanGenerationError as exc:
        logger.warning("Неделя не прошла проверки (chat_id=%s): %s", chat_id, exc.problems)
        await status_msg.edit_text(
            "❌ Не получилось составить неделю, которая проходит проверку по правилам тренировок. "
            "Попробуйте ещё раз через /test_week."
        )
    except AIClientError as exc:
        await status_msg.edit_text(f"❌ {exc}")
    except Exception as exc:
        logger.exception("Ошибка при вызове /test_week: %s", exc)
        await status_msg.edit_text("❌ Произошла ошибка при генерации расписания.")