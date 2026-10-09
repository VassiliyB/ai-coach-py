# bot/handlers/race.py
import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from database import async_session_maker
from services.coach_service import calculate_zones
from services.plan_calendar import plan_total_weeks
from services.plan_renderer import render_zones
from services.race_goal import assess_goal, race_distance_m, render_goal_after_review
from services.race_result import (
    RACE_HELP,
    check_race,
    parse_race,
    race_from_profile,
    render_race_saved,
    render_race_status,
)
from services.user_service import UserService
from services.user_time import local_today

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("race"), flags={"user_lock": "/race"})
async def handle_race(message: Message, command: CommandObject) -> None:
    """Результат забега вручную: VDOT по нему заменяет оценку по тренировкам (services.race_result)."""
    async with async_session_maker() as session:
        user = await UserService.get_or_create_user(session, message.chat.id)
        profile = await UserService.get_athlete_profile(session, user.id)
        plan = await UserService.get_active_plan(session, user.id)
    today = local_today(UserService.timezone_of(user))
    saved = race_from_profile(profile)
    old_vdot = getattr(profile, "vdot", None)

    if not command.args:
        await message.answer(render_race_status(saved, old_vdot, today), parse_mode="HTML")
        return

    race = parse_race(command.args, today)
    if race is None:
        await message.answer(f"❌ Не удалось распознать результат.\n\n{RACE_HELP}", parse_mode="HTML")
        return
    error = check_race(race, today, getattr(profile, "vdot_reviewed_on", None), saved)
    if error:
        await message.answer(f"❌ {error}", parse_mode="HTML")
        return

    async with async_session_maker() as session:
        await UserService.set_race_result(session, user.id, race.distance_m, race.time_s, race.race_date, race.vdot)
    logger.info(
        "Результат забега (chat_id=%s): %s м за %s с, %s, VDOT %s -> %s",
        message.chat.id, race.distance_m, race.time_s, race.race_date, old_vdot, race.vdot,
    )

    # Цель плана переоценивается по новому VDOT, без запаса на «скрытую» форму: забег её уже показал
    goal_note = None
    distance_m = race_distance_m(plan.target_race) if plan else None
    if plan and plan.target_time_s and distance_m and plan.race_date > today:
        assessment = assess_goal(
            race.vdot, plan.target_time_s, distance_m, max(0, plan_total_weeks(today, plan.race_date)), margin=0.0,
        )
        goal_note = render_goal_after_review(assessment, distance_m)

    text = render_race_saved(race, old_vdot, render_zones(calculate_zones(race.vdot)), goal_note)
    await message.answer(text, parse_mode="HTML")
