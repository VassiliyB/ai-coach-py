# bot/handlers/plan.py
import logging
from datetime import date, datetime
from typing import Any, Dict, Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards import (
    GOAL_NONE,
    GOAL_SUGGESTED,
    get_garmin_export_keyboard,
    get_goal_keyboard,
    get_target_distances_keyboard,
)
from bot.states import PlanCreationStates
from clients.ai_client import AIClientError
from database import async_session_maker
from services.coach_service import MAX_VDOT, MIN_VDOT, TrainingZones, build_profile_context, zones_for_profile
from services.message_service import MessageService
from services.plan_calendar import intro_days, monday_of, plan_total_weeks
from services.plan_generator import PlanGenerationError, PlanGenerator
from services.plan_renderer import render_intro_days, render_macro
from services.race_goal import (
    GOAL_FORMATS,
    RACE_DISTANCES,
    GoalAssessment,
    assess_goal,
    format_duration,
    goal_pace_text,
    goal_prompt_text,
    parse_goal,
    realistic_goal_s,
    render_goal_line,
    render_goal_rejection,
)
from services.scheduler_service import TrainingSchedulerService, WeekStatus
from services.user_service import UserService
from services.user_time import local_today

logger = logging.getLogger(__name__)
router = Router()

NO_GOAL_WORDS = {"без цели", "нет", "-"}


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
    distance_name, distance_m = RACE_DISTANCES.get(callback.data, ("Забег", None))

    await state.update_data(target_race=distance_name, distance_m=distance_m)
    await state.set_state(PlanCreationStates.waiting_for_date)

    await callback.message.edit_text(
        f"Выбрана дистанция: <b>{distance_name}</b>\n\n"
        "📅 Введите дату забега в формате: <b>ДД.ММ.ГГГГ</b> (например, <code>15.06.2027</code>):",
        parse_mode="HTML",
    )
    await callback.answer()


def _current_vdot(profile: Any) -> Optional[float]:
    vdot = getattr(profile, "vdot", None)
    return vdot if vdot and MIN_VDOT <= vdot <= MAX_VDOT else None


@router.message(PlanCreationStates.waiting_for_date)
async def handle_race_date_entered(message: Message, state: FSMContext) -> None:
    """Валидация даты и переход к выбору цели: прогноз по текущей форме и предложенная реалистичная цель."""
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
        profile = await UserService.get_athlete_profile(session, user.id)
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
    distance_m = data.get("distance_m")
    vdot = _current_vdot(profile)

    suggested = None
    if vdot and distance_m:
        suggested = realistic_goal_s(vdot, distance_m, total_weeks)
        forecast = assess_goal(vdot, suggested, distance_m, total_weeks)
        hint = (
            f"По текущей форме (VDOT {vdot:.1f}) прогноз: <b>{format_duration(forecast.predicted_now_s)}</b>.\n"
            f"За {total_weeks} нед. реально выйти примерно на <b>{format_duration(forecast.best_realistic_s)}</b>, "
            f"предлагаю цель <b>{format_duration(suggested)}</b> ({goal_pace_text(suggested, distance_m)}).\n\n"
            f"{GOAL_FORMATS}"
        )
    else:
        hint = (
            "VDOT ещё не рассчитан (нужны пробежки от 3 км, затем /sync), поэтому оценить цель по времени "
            "нельзя. План будет построен по текущей форме."
        )

    await state.update_data(
        race_date=race_date.isoformat(), total_weeks=total_weeks, suggested_goal_s=suggested,
    )
    await state.set_state(PlanCreationStates.waiting_for_goal)
    await message.answer(
        f"🏁 <b>Цель на забег</b> ({total_weeks} нед. подготовки)\n\n{hint}",
        reply_markup=get_goal_keyboard(format_duration(suggested) if suggested else None),
        parse_mode="HTML",
    )


@router.message(PlanCreationStates.waiting_for_goal, flags={"user_lock": "составление плана"})
async def handle_goal_entered(message: Message, state: FSMContext, plan_generator: PlanGenerator) -> None:
    """Цель временем или темпом: нереалистичную не принимаем, объясняем почему и что достижимо."""
    data = await state.get_data()
    distance_m = data.get("distance_m")
    async with async_session_maker() as session:
        user = await UserService.get_or_create_user(session, message.chat.id)
        profile = await UserService.get_athlete_profile(session, user.id)
    vdot = _current_vdot(profile)

    if not vdot or not distance_m:
        await message.answer(
            "ℹ️ Без VDOT цель по времени оценить нельзя. Нажмите «Без цели» или выполните /sync и начните /plan заново.",
            reply_markup=get_goal_keyboard(),
        )
        return

    if (message.text or "").strip().lower() in NO_GOAL_WORDS:
        await _create_plan(message, state, plan_generator, None)
        return

    goal_s = parse_goal(message.text or "", distance_m)
    if goal_s is None:
        await message.answer(f"❌ Не удалось распознать цель.\n{GOAL_FORMATS}", parse_mode="HTML")
        return

    assessment = assess_goal(vdot, goal_s, distance_m, data["total_weeks"])
    if not assessment.accepted:
        suggested = data.get("suggested_goal_s")
        await message.answer(
            f"{render_goal_rejection(assessment, data['total_weeks'])}\n\n"
            f"Введите другую цель или выберите кнопку ниже.\n{GOAL_FORMATS}",
            reply_markup=get_goal_keyboard(format_duration(suggested) if suggested else None),
            parse_mode="HTML",
        )
        return

    await _create_plan(message, state, plan_generator, assessment)


@router.callback_query(
    PlanCreationStates.waiting_for_goal, F.data.in_({GOAL_NONE, GOAL_SUGGESTED}),
    flags={"user_lock": "составление плана"},
)
async def handle_goal_button(callback: CallbackQuery, state: FSMContext, plan_generator: PlanGenerator) -> None:
    """«Без цели» или предложенная реалистичная цель."""
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    data = await state.get_data()

    assessment = None
    suggested = data.get("suggested_goal_s")
    if callback.data == GOAL_SUGGESTED and suggested:
        async with async_session_maker() as session:
            user = await UserService.get_or_create_user(session, callback.message.chat.id)
            profile = await UserService.get_athlete_profile(session, user.id)
        vdot = _current_vdot(profile)
        if vdot:
            assessment = assess_goal(vdot, suggested, data["distance_m"], data["total_weeks"])
    await _create_plan(callback.message, state, plan_generator, assessment)


async def _create_plan(
    message: Message,
    state: FSMContext,
    plan_generator: PlanGenerator,
    assessment: Optional[GoalAssessment],
) -> None:
    """Генерация структурного макроплана, запись в БД и показ пользователю. assessment: принятая цель или None."""
    data: Dict[str, Any] = await state.get_data()
    await state.clear()
    target_race = data.get("target_race", "Бег")
    race_date = date.fromisoformat(data["race_date"])
    total_weeks = data["total_weeks"]
    distance_m = data.get("distance_m")
    goal_s = int(assessment.target_time_s) if assessment else None
    goal_text = goal_prompt_text(assessment, distance_m) if assessment and distance_m else None

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
            garmin_linked = user.garmin_linked
            today = local_today(UserService.timezone_of(user))
            profile_dict = build_profile_context(profile)
            zones = zones_for_profile(profile)

        # 2. Модель выбирает фазы и километраж; схема и правила проверяются, при замечаниях повтор
        macro = await plan_generator.generate_macro(
            athlete_profile=profile_dict,
            target_race=target_race,
            race_date=race_date.isoformat(),
            total_weeks=total_weeks,
            goal_text=goal_text,
        )

        # 3. Сохранение в БД (старый активный план деактивируется в той же транзакции)
        async with async_session_maker() as session:
            saved = await UserService.save_training_plan(
                session=session,
                user_id=user_id,
                target_race=target_race,
                race_date=race_date,
                total_weeks=total_weeks,
                plan=macro,
                target_time_s=goal_s,
            )

        await status_msg.delete()

        # 4. Текст собирает код: зоны темпа из VDOT, цель из race_goal, текст модели экранирован
        goal_line = render_goal_line(assessment, distance_m) if assessment and distance_m else None
        text = render_macro(macro, target_race, race_date=race_date, zones=zones, goal_line=goal_line)
        for chunk in MessageService.chunk_message(text):
            await message.answer(chunk, parse_mode="HTML")

        # 5. Дни до понедельника, чтобы не простаивать до недели №1. Ошибка здесь план не отменяет
        weekly_km = getattr(profile, "average_weekly_km", None) or macro.weekly_km[0]
        await _send_intro_days(
            message, plan_generator, profile_dict, target_race, today,
            weekly_km=weekly_km, zones=zones, max_hr=getattr(profile, "max_heart_rate", None),
            user_id=user_id, plan_id=saved.id, garmin_linked=garmin_linked,
        )

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


async def _send_intro_days(
    message: Message,
    plan_generator: PlanGenerator,
    profile_dict: dict,
    target_race: str,
    today: date,
    *,
    weekly_km: float,
    zones: Optional[TrainingZones],
    max_hr: Optional[int],
    user_id: int,
    plan_id: int,
    garmin_linked: bool,
) -> None:
    """Тренировки с завтрашнего дня до воскресенья по поясу пользователя.

    Сохраняются неделей текущего понедельника: неделя №1 и рассылка от этого не меняются.
    """
    days = intro_days(today)
    if not days:
        return  # воскресенье: неделя №1 начнётся завтра и придёт рассылкой
    status_msg = await message.answer("🧠 Составляю тренировки до понедельника...")
    try:
        week = await plan_generator.generate_intro_days(
            athlete_profile=profile_dict, target_race=target_race, days=days, weekly_km=weekly_km, zones=zones,
        )
        async with async_session_maker() as session:
            weekly = await UserService.save_weekly_plan(
                session=session, user_id=user_id, training_plan_id=plan_id,
                week_start=monday_of(today), week_end=days[-1], plan=week,
            )
        await status_msg.delete()
        text = render_intro_days(week, days, zones=zones, max_hr=max_hr)
        chunks = MessageService.chunk_message(text)
        markup = get_garmin_export_keyboard(weekly.id) if garmin_linked else None
        for i, chunk in enumerate(chunks):
            await message.answer(chunk, parse_mode="HTML", reply_markup=markup if i == len(chunks) - 1 else None)
    except Exception as exc:
        logger.warning("Вводные дни не составлены (chat_id=%s): %s", message.chat.id, exc)
        await status_msg.edit_text(
            "⚠️ Тренировки до понедельника составить не удалось. План сохранён, "
            "неделя №1 придёт в воскресенье, а получить её раньше можно через /test_week."
        )


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
