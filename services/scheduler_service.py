import logging
from datetime import date, datetime, timedelta
from typing import Optional

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from database import async_session_maker
from services.user_service import UserService
from services.ai_coach_service import AICoachService
from services.message_service import MessageService
from models.user import AppUser
from models.training_plan import TrainingPlan
from models.athlete_profile import AthleteProfile

logger = logging.getLogger(__name__)


class TrainingSchedulerService:
    """Сервис фоновых периодических задач (рассылка планов и опрос активностей)."""

    def __init__(self, bot: Bot, ai_coach: Optional[AICoachService] = None) -> None:
        self.bot = bot
        self.ai_coach = ai_coach or AICoachService()
        self.scheduler = AsyncIOScheduler()

    @staticmethod
    def get_next_week_dates(base_date: Optional[date] = None) -> tuple[date, date, int]:
        """
        Вычисляет границы следующей недели (Понедельник — Воскресенье).
        Возвращает: (дата_понедельника, дата_воскресенья, номер_недели_в_году).
        """
        today = base_date or datetime.now().date()
        # Дней до следующего понедельника (если сегодня ВС (6), то +1 день)
        days_ahead = 7 - today.weekday()
        if days_ahead == 0:
            days_ahead = 7

        next_monday = today + timedelta(days=days_ahead)
        next_sunday = next_monday + timedelta(days=6)
        week_number = next_monday.isocalendar()[1]
        return next_monday, next_sunday, week_number

    async def generate_and_send_microcycle_for_user(
        self,
        user: AppUser,
        plan: TrainingPlan,
        profile: Optional[AthleteProfile],
        target_monday: Optional[date] = None,
        target_sunday: Optional[date] = None,
    ) -> bool:
        """Генерирует недельный микроцикл для конкретного пользователя и отправляет в Telegram."""
        if not target_monday or not target_sunday:
            target_monday, target_sunday, _ = self.get_next_week_dates()

        # Вычисляем порядковый номер недели относительно старта плана
        plan_created = plan.created_at.date() if isinstance(plan.created_at, datetime) else plan.created_at
        weeks_elapsed = max(1, ((target_monday - plan_created).days // 7) + 1)

        profile_dict = {"summary_text": profile.raw_summary_text} if profile else {}

        logger.info(
            "Генерация микроцикла для user_id=%s (chat_id=%s), Неделя #%d (%s - %s)",
            user.id, user.telegram_chat_id, weeks_elapsed, target_monday, target_sunday
        )

        try:
            # 1. Генерация расписания по правилу 80/20 через спортивный ИИ
            weekly_text = await self.ai_coach.generate_weekly_microcycle(
                athlete_profile=profile_dict,
                target_race=plan.target_race,
                macro_plan_summary=plan.plan_details,
                week_number=weeks_elapsed,
                week_start=target_monday.strftime("%d.%m.%Y"),
                week_end=target_sunday.strftime("%d.%m.%Y"),
                total_weeks=plan.total_weeks,
            )

            # 2. Сохранение в БД
            async with async_session_maker() as session:
                await UserService.save_weekly_plan(
                    session=session,
                    user_id=user.id,
                    training_plan_id=plan.id,
                    week_start=target_monday,
                    week_end=target_sunday,
                    plan_details=weekly_text,
                )

            # 3. Отправка пользователю в Telegram
            header = (
                f"📋 <b>Расписание тренировок на неделю (Пн–Вс):</b>\n"
                f"Период: <code>{target_monday.strftime('%d.%m')} — {target_sunday.strftime('%d.%m.%Y')}</code>\n"
                f"Цель: <b>{plan.target_race}</b> | Неделя подготовки: <b>#{weeks_elapsed}</b>\n\n"
            )

            chunks = MessageService.chunk_message(header + weekly_text)
            for chunk in chunks:
                await self.bot.send_message(
                    chat_id=user.telegram_chat_id,
                    text=chunk,
                    parse_mode="HTML",
                )
            return True

        except Exception as exc:
            logger.exception("Ошибка при генерации микроцикла для user_id=%s: %s", user.id, exc)
            return False

    async def sunday_weekly_distribution_job(self) -> None:
        """Задача Cron: рассылка микроциклов каждое воскресенье в 15:00."""
        logger.info("Запуск автоматической воскресной рассылки микроциклов...")

        async with async_session_maker() as session:
            active_plans = await UserService.get_all_active_plans_with_users(session)

        if not active_plans:
            logger.info("Активных планов для рассылки не найдено.")
            return

        success_count = 0
        for user, plan, profile in active_plans:
            success = await self.generate_and_send_microcycle_for_user(user, plan, profile)
            if success:
                success_count += 1

        logger.info("Воскресная рассылка завершена. Успешно отправлено: %d из %d", success_count, len(active_plans))

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
        logger.info("Фоновый планировщик успешно запущен (воскресный микроцикл настроен на ВС 15:00).")

    def shutdown(self) -> None:
        """Остановка планировщика при завершении программы."""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Фоновый планировщик остановлен.")