# bot/handlers/garmin_export.py
"""Выгрузка недели в календарь Garmin по кнопке под расписанием."""
import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.keyboards import GARMIN_EXPORT_PREFIX
from clients.garmin import GarminAuthError, GarminClient, GarminRateLimitError
from database import async_session_maker
from services.coach_service import zones_for_profile
from services.garmin_link import RELOGIN_TEXT, reset_garmin_link
from services.garmin_workouts import build_workout, export_days, split_previous
from services.plan_renderer import DAY_NAMES, TYPE_LABELS
from services.plan_storage import parse_week
from services.user_service import UserService
from services.user_time import local_today

logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data.startswith(GARMIN_EXPORT_PREFIX), flags={"user_lock": "выгрузка в Garmin"})
async def handle_garmin_export(callback: CallbackQuery, garmin: GarminClient) -> None:
    """Беговые дни недели с сегодняшнего: шаги, темп и пульс считает код, повторная выгрузка заменяет прежнюю."""
    chat_id = callback.message.chat.id
    try:
        weekly_id = int(callback.data.removeprefix(GARMIN_EXPORT_PREFIX))
    except ValueError:
        await callback.answer()
        return

    if not garmin.has_saved_tokens(chat_id):
        await callback.answer("Garmin Connect не подключён. Отправьте /start, чтобы войти.", show_alert=True)
        return

    async with async_session_maker() as session:
        user = await UserService.get_or_create_user(session, chat_id)
        weekly = await UserService.get_weekly_plan(session, weekly_id, user.id)
        profile = await UserService.get_athlete_profile(session, user.id)

    week = parse_week(weekly.plan_details) if weekly else None
    if week is None:
        await callback.answer("Это расписание больше недоступно.", show_alert=True)
        return

    today = local_today(UserService.timezone_of(user))
    days = export_days(week, weekly.week_start_date, weekly.week_end_date, today)
    if not days:
        await callback.answer("На этой неделе не осталось беговых тренировок для выгрузки.", show_alert=True)
        return

    zones = zones_for_profile(profile)
    max_hr = getattr(profile, "max_heart_rate", None)
    workouts = [(when.isoformat(), build_workout(day, zones, max_hr)) for when, day in days]
    replace_ids, keep = split_previous(weekly.garmin_workouts, today)

    await callback.answer("Выгружаю в Garmin...")
    status_msg = await callback.message.answer("⏳ Выгружаю тренировки в календарь Garmin...")
    try:
        created = await garmin.schedule_workouts(chat_id, workouts, replace_ids=replace_ids)
    except GarminRateLimitError as exc:
        await status_msg.edit_text(f"⚠️ {exc}")
        return
    except GarminAuthError:
        await reset_garmin_link(garmin, chat_id)
        await status_msg.edit_text(RELOGIN_TEXT, parse_mode="HTML")
        return
    except Exception:
        logger.exception("Ошибка выгрузки недели %s в Garmin (chat_id=%s)", weekly_id, chat_id)
        await status_msg.edit_text("❌ Не удалось выгрузить тренировки в Garmin. Попробуйте позже.")
        return

    async with async_session_maker() as session:
        await UserService.set_garmin_workouts(session, weekly_id, keep + created)

    lines = [f"✅ В календарь Garmin добавлено тренировок: <b>{len(created)}</b>"]
    lines += [
        f"• {DAY_NAMES[when.weekday()]} {when:%d.%m}: {TYPE_LABELS[day.type]}, {day.distance_km:g} км"
        for when, day in days
    ]
    if replace_ids:
        lines.append("Прежняя выгрузка этих дней заменена.")
    if zones is None:
        lines.append("ℹ️ Темп на часах не задан: выполните /sync, чтобы рассчитать VDOT.")
    lines.append("Синхронизируйте часы, чтобы тренировки появились на них.")
    await status_msg.edit_text("\n".join(lines), parse_mode="HTML")
