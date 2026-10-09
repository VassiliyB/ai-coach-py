# bot/handlers/show_plan.py
"""/show_plan: обзор активного плана (неделя, период, цель) и изменение цели в реалистичных пределах."""
import logging
from datetime import date
from typing import Any, Optional, Tuple

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards import (
    PLAN_GOAL_CANCEL,
    PLAN_GOAL_CLEAR,
    PLAN_GOAL_EDIT,
    get_goal_edit_cancel_keyboard,
    get_show_plan_keyboard,
)
from bot.states import ShowPlanStates
from database import async_session_maker
from models.training_plan import TrainingPlan
from services.coach_service import MAX_VDOT, MIN_VDOT
from services.plan_calendar import current_plan_week, plan_start_monday, plan_total_weeks
from services.plan_renderer import render_plan_overview
from services.plan_storage import parse_macro
from services.race_goal import (
    GOAL_FORMATS,
    assess_goal,
    fastest_realistic_s,
    format_duration,
    goal_pace_text,
    parse_goal,
    race_distance_m,
    render_forecast_line,
    render_goal_line,
    render_goal_rejection,
)
from services.user_service import UserService
from services.user_time import local_today

logger = logging.getLogger(__name__)
router = Router()

NO_PLAN_TEXT = "⚠️ У вас нет активного плана подготовки. Создайте его через <code>/plan</code>."


def _current_vdot(profile: Any) -> Optional[float]:
    vdot = getattr(profile, "vdot", None)
    return vdot if vdot and MIN_VDOT <= vdot <= MAX_VDOT else None


async def _load(chat_id: int) -> Tuple[Optional[TrainingPlan], Optional[float], date]:
    """Активный план, текущий VDOT и сегодняшняя дата по поясу пользователя."""
    async with async_session_maker() as session:
        user = await UserService.get_or_create_user(session, chat_id)
        plan = await UserService.get_active_plan(session, user.id)
        profile = await UserService.get_athlete_profile(session, user.id)
    return plan, _current_vdot(profile), local_today(UserService.timezone_of(user))


def _weeks_left(today: date, race_date: date) -> int:
    """Недели подготовки, оставшиеся до забега (с ближайшего понедельника), для оценки цели."""
    return max(0, plan_total_weeks(today, race_date))


def _overview(plan: TrainingPlan, vdot: Optional[float], today: date) -> Tuple[str, Any]:
    """Текст обзора и клавиатура. Цель переоценивается по текущему VDOT и оставшимся неделям."""
    distance_m = race_distance_m(plan.target_race)
    goal_line = forecast_line = None
    if vdot and distance_m:
        forecast_line = render_forecast_line(vdot, distance_m)
        if plan.target_time_s:
            assessment = assess_goal(vdot, plan.target_time_s, distance_m, _weeks_left(today, plan.race_date))
            goal_line = render_goal_line(assessment, distance_m)
    if plan.target_time_s and goal_line is None and distance_m:
        # VDOT пропал (например, нет свежих пробежек): цель показываем без оценки
        goal_line = (
            f"⏱ Цель: <b>{format_duration(plan.target_time_s)}</b> "
            f"(темп {goal_pace_text(plan.target_time_s, distance_m)})"
        )

    text = render_plan_overview(
        target_race=plan.target_race,
        race_date=plan.race_date,
        today=today,
        total_weeks=plan.total_weeks,
        week_number=current_plan_week(today, plan.race_date, plan.total_weeks),
        start_monday=plan_start_monday(plan.race_date, plan.total_weeks),
        macro=parse_macro(plan.plan_details),
        goal_line=goal_line,
        forecast_line=forecast_line,
    )
    race_ahead = plan.race_date > today
    keyboard = get_show_plan_keyboard(
        has_goal=bool(plan.target_time_s) and race_ahead,
        can_set_goal=bool(vdot and distance_m) and race_ahead,
    )
    return text, keyboard


@router.message(Command("show_plan"))
async def handle_show_plan(message: Message, state: FSMContext) -> None:
    await state.clear()
    plan, vdot, today = await _load(message.chat.id)
    if plan is None:
        await message.answer(NO_PLAN_TEXT, parse_mode="HTML")
        return
    text, keyboard = _overview(plan, vdot, today)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == PLAN_GOAL_EDIT)
async def handle_goal_edit(callback: CallbackQuery, state: FSMContext) -> None:
    """Запрос новой цели с допустимой границей: быстрее лучшего реального результата нельзя."""
    plan, vdot, today = await _load(callback.message.chat.id)
    distance_m = race_distance_m(plan.target_race) if plan else None
    if plan is None or not vdot or not distance_m or plan.race_date <= today:
        await callback.answer("Цель сейчас изменить нельзя: обновите /show_plan.", show_alert=True)
        return

    weeks = _weeks_left(today, plan.race_date)
    fastest = fastest_realistic_s(vdot, distance_m, weeks)
    await state.set_state(ShowPlanStates.waiting_for_new_goal)
    await state.update_data(plan_id=plan.id)
    await callback.answer()
    await callback.message.answer(
        f"✏️ <b>Новая цель</b> ({weeks} нед. до забега)\n\n"
        f"{render_forecast_line(vdot, distance_m)}\n"
        f"Самая быстрая реалистичная цель: <b>{format_duration(fastest)}</b> "
        f"({goal_pace_text(fastest, distance_m)}).\n\n"
        f"{GOAL_FORMATS}\n"
        "Объём плана не пересчитывается: меняется цель и прогноз.",
        reply_markup=get_goal_edit_cancel_keyboard(),
        parse_mode="HTML",
    )


@router.message(ShowPlanStates.waiting_for_new_goal, ~F.text.startswith("/"))   # команды не считаем целью
async def handle_new_goal(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    plan, vdot, today = await _load(message.chat.id)
    distance_m = race_distance_m(plan.target_race) if plan else None
    if plan is None or plan.id != data.get("plan_id") or not vdot or not distance_m:
        await state.clear()
        await message.answer("⚠️ План изменился. Откройте /show_plan заново.")
        return

    goal_s = parse_goal(message.text or "", distance_m)
    if goal_s is None:
        await message.answer(
            f"❌ Не удалось распознать цель.\n{GOAL_FORMATS}",
            reply_markup=get_goal_edit_cancel_keyboard(), parse_mode="HTML",
        )
        return

    weeks = _weeks_left(today, plan.race_date)
    assessment = assess_goal(vdot, goal_s, distance_m, weeks)
    if not assessment.accepted:
        await message.answer(
            f"{render_goal_rejection(assessment, weeks)}\n\nВведите другую цель.",
            reply_markup=get_goal_edit_cancel_keyboard(), parse_mode="HTML",
        )
        return

    async with async_session_maker() as session:
        await UserService.set_plan_target_time(session, plan.id, goal_s)
    await state.clear()
    plan.target_time_s = goal_s
    logger.info("Цель плана %s изменена на %s с (chat_id=%s)", plan.id, goal_s, message.chat.id)
    text, keyboard = _overview(plan, vdot, today)
    await message.answer(f"✅ Цель обновлена.\n\n{text}", reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == PLAN_GOAL_CLEAR)
async def handle_goal_clear(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    plan, vdot, today = await _load(callback.message.chat.id)
    if plan is None:
        await callback.answer("Активного плана нет.", show_alert=True)
        return
    async with async_session_maker() as session:
        await UserService.set_plan_target_time(session, plan.id, None)
    plan.target_time_s = None
    await callback.answer("Цель убрана")
    text, keyboard = _overview(plan, vdot, today)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == PLAN_GOAL_CANCEL)
async def handle_goal_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await callback.message.edit_text("Изменение цели отменено.")
