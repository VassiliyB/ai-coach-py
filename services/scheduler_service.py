# services/scheduler_service.py
import logging
from datetime import date, datetime
from enum import Enum
from typing import Optional

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from database import async_session_maker
from models.athlete_profile import AthleteProfile
from models.training_plan import TrainingPlan
from models.user import AppUser
from services.coach_service import build_profile_context, zones_for_profile
from services.message_service import MessageService
from services.plan_calendar import DATE_FORMAT, next_week_dates, plan_week_number
from services.plan_generator import PlanGenerator
from services.plan_renderer import render_week
from services.plan_storage import parse_macro
from services.user_service import UserService

logger = logging.getLogger(__name__)

LEGACY_PLAN_NOTICE = (
    "⚠️ Ваш план подготовки создан в старом формате, по нему нельзя построить недельное расписание.\n"
    "Создайте план заново командой <code>/plan</code>."
)


class WeekStatus(str, Enum):
    SENT = "sent"                # расписание отправлено
    LEGACY_PLAN = "legacy_plan"  # макроплан в старом текстовом формате, пользователю отправлена подсказка
    FINISHED = "finished"        # неделя после забега: план завершён


class TrainingSchedulerService:
    """Сервис фоновых периодических задач (рассылка планов и опрос активностей)."""

    def __init__(self, bot: Bot, plan_generator: PlanGenerator) -> None:
        self.bot = bot
        self.plan_generator = plan_generator
        self.scheduler = AsyncIOScheduler()

    async def send_week(
        self,
        user: AppUser,
        plan: TrainingPlan,
        profile: Optional[AthleteProfile],
        today: Optional[date] = None,
    ) -> WeekStatus:
        """Генерирует расписание на следующую неделю, сохраняет и отправляет в Telegram.

        Ошибки генерации (PlanGenerationError, AIClientError) пробрасываются: решает вызывающий код.
        """
        macro = parse_macro(plan.plan_details)
        if macro is None:
            await self.bot.send_message(user.telegram_chat_id, LEGACY_PLAN_NOTICE, parse_mode="HTML")
            return WeekStatus.LEGACY_PLAN

        monday, sunday = next_week_dates(today or datetime.now().date())
        week_number = plan_week_number(plan.race_date, macro.total_weeks, monday)
        if week_number is None:
            logger.info("План user_id=%s завершён: забег %s уже прошёл", user.id, plan.race_date)
            return WeekStatus.FINISHED

        week_start, week_end = monday.strftime(DATE_FORMAT), sunday.strftime(DATE_FORMAT)
        zones = zones_for_profile(profile)
        logger.info(
            "Генерация недели для user_id=%s (chat_id=%s): №%d из %d (%s - %s)",
            user.id, user.telegram_chat_id, week_number, macro.total_weeks, week_start, week_end,
        )

        # 1. Модель выбирает тренировки, схема и правила проверяются, при замечаниях повтор
        week = await self.plan_generator.generate_week(
            athlete_profile=build_profile_context(profile),
            target_race=plan.target_race,
            macro=macro,
            week_number=week_number,
            week_start=week_start,
            week_end=week_end,
            zones=zones,
        )

        # 2. Сохранение (повторная генерация той же недели перезаписывает запись)
        async with async_session_maker() as session:
            await UserService.save_weekly_plan(
                session=session,
                user_id=user.id,
                training_plan_id=plan.id,
                week_start=monday,
                week_end=sunday,
                plan=week,
            )

        # 3. Текст собирает код: темп по VDOT, пульс по ЧССmax, текст модели экранирован
        text = render_week(
            week,
            target_race=plan.target_race,
            week_number=week_number,
            total_weeks=macro.total_weeks,
            week_start=week_start,
            week_end=week_end,
            phase=macro.phase_for_week(week_number),
            zones=zones,
            max_hr=getattr(profile, "max_heart_rate", None),
        )
        for chunk in MessageService.chunk_message(text):
            await self.bot.send_message(chat_id=user.telegram_chat_id, text=chunk, parse_mode="HTML")
        return WeekStatus.SENT

    async def sunday_weekly_distribution_job(self) -> None:
        """Задача Cron: рассылка недельных расписаний каждое воскресенье в 15:00."""
        logger.info("Запуск автоматической воскресной рассылки микроциклов...")

        async with async_session_maker() as session:
            active_plans = await UserService.get_all_active_plans_with_users(session)

        if not active_plans:
            logger.info("Активных планов для рассылки не найдено.")
            return

        sent = 0
        for user, plan, profile in active_plans:
            try:
                if await self.send_week(user, plan, profile) == WeekStatus.SENT:
                    sent += 1
            except Exception as exc:  # ошибка одного пользователя не останавливает рассылку остальным
                logger.exception("Ошибка при генерации недели для user_id=%s: %s", user.id, exc)

        logger.info("Воскресная рассылка завершена. Отправлено расписаний: %d из %d", sent, len(active_plans))

    def start(self) -> None:
        """Запуск планировщика задач."""
        # Каждое воскресенье в 15:00 (по системному времени сервера)
        self.scheduler.add_job(
            self.sunday_weekly_distribution_job,
            trigger=CronTrigger(day_of_week="sun", hour=15, minute=0),
            id="sunday_weekly_plan_job",
            replace_existing=True,
        )
        self.scheduler.start()
        logger.info("Фоновый планировщик запущен (воскресный микроцикл: ВС 15:00).")

    def shutdown(self) -> None:
        """Остановка планировщика при завершении программы."""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Фоновый планировщик остановлен.")
