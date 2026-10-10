# services/scheduler_service.py
import logging
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from bot.keyboards import get_garmin_export_keyboard
from clients.garmin import GarminClient, GarminClientError
from clients.llm_usage import USAGE_WEEKLY, usage_scope
from database import async_session_maker
from models.athlete_profile import AthleteProfile
from models.training_plan import TrainingPlan
from models.user import AppUser
from schemas.plan import MacroPlan
from services.activity_poller import ActivityPoller
from services.backup_watch import check_backups
from services.coach_service import TrainingZones, build_profile_context, calculate_zones, zones_for_profile
from services.heart_rate import HrRef, profile_hr_basis
from services.macro_replan import (
    REPLAN_WINDOW_DAYS,
    adjustment_after_replan,
    replan_macro,
    replan_reasons,
    start_km_after,
)
from services.message_service import MessageService
from services.plan_calendar import DATE_FORMAT, monday_of, next_week_dates, plan_total_weeks, plan_week_number
from services.plan_generator import PlanGenerator, target_km_for_week
from services.plan_renderer import render_replan, render_today, render_vdot_review, render_week, today_workout
from services.plan_storage import parse_macro, parse_week
from services.race_goal import assess_goal, race_distance_m, render_goal_after_review
from services.race_result import goal_margin
from services.user_locks import UserLocks
from services.user_service import UserService
from services.user_time import WEEKLY_SEND_HOUR, is_weekly_send_time, local_now, local_today
from services.vdot_review import RECENT_WEEKS, longest_gap_days, review_due, review_vdot
from services.week_adaptation import (
    RecoverySignals,
    WeekAdjustment,
    adjust_next_week,
    review_to_dict,
    review_week,
    run_facts,
)
from services.week_summary import has_content, render_week_summary, summarize_week

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

    def __init__(
        self,
        bot: Bot,
        plan_generator: PlanGenerator,
        activity_poller: Optional[ActivityPoller] = None,
        poll_minutes: int = 0,
        locks: Optional[UserLocks] = None,
        garmin: Optional[GarminClient] = None,
        backup_dir: Optional[Path] = None,
    ) -> None:
        """activity_poller и poll_minutes > 0 включают опрос Garmin с этим интервалом.

        garmin: для корректировки недели по факту прошлой; без него недели строятся строго по макроплану.
        backup_dir: папка копий БД; с ней раз в день проверяется, что копирование не остановилось.
        """
        self.bot = bot
        self.garmin = garmin
        self.plan_generator = plan_generator
        self.activity_poller = activity_poller if poll_minutes > 0 else None
        self.poll_minutes = poll_minutes
        self.locks = locks or UserLocks()
        self.backup_dir = backup_dir
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

        today = today or local_today(UserService.timezone_of(user))
        monday, sunday = next_week_dates(today)
        week_number = plan_week_number(plan.race_date, macro.total_weeks, monday)
        if week_number is None:
            logger.info("План user_id=%s завершён: забег %s уже прошёл", user.id, plan.race_date)
            return WeekStatus.FINISHED

        week_start, week_end = monday.strftime(DATE_FORMAT), sunday.strftime(DATE_FORMAT)
        await self._review_vdot(user, plan, profile, week_number, today)  # обновляет profile.vdot
        zones = zones_for_profile(profile)
        hr_basis = profile_hr_basis(profile)
        adjustment, macro, summary_text = await self._adapt_week(
            user, plan, macro, week_number, today, zones, hr_basis,
        )
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
            adjustment=adjustment,
        )

        # 2. Сохранение (повторная генерация той же недели перезаписывает запись)
        async with async_session_maker() as session:
            weekly = await UserService.save_weekly_plan(
                session=session,
                user_id=user.id,
                training_plan_id=plan.id,
                week_start=monday,
                week_end=sunday,
                plan=week,
            )

        # 3. Итог прошлой недели только после сохранения новой: если генерация упала, рассылка повторит
        #    попытку через час, и итог иначе пришёл бы снова
        summary_sent = summary_text is not None and await self._send_week_summary(user.telegram_chat_id, summary_text)

        # 4. Текст собирает код: темп по VDOT, пульс по ЧССmax или ПАНО, текст модели экранирован
        text = render_week(
            week,
            target_race=plan.target_race,
            week_number=week_number,
            total_weeks=macro.total_weeks,
            week_start=week_start,
            week_end=week_end,
            phase=macro.phase_for_week(week_number),
            zones=zones,
            hr_basis=hr_basis,
            # Итог недели уже пришёл отдельным сообщением: строку «Прошлая неделя» не повторяем
            adjustment=replace(adjustment, summary=None) if adjustment and summary_sent else adjustment,
        )
        # Кнопка выгрузки в календарь Garmin под последней частью расписания
        chunks = MessageService.chunk_message(text)
        markup = get_garmin_export_keyboard(weekly.id) if user.garmin_linked else None
        for i, chunk in enumerate(chunks):
            await self.bot.send_message(
                chat_id=user.telegram_chat_id, text=chunk, parse_mode="HTML",
                reply_markup=markup if i == len(chunks) - 1 else None,
            )
        return WeekStatus.SENT

    async def _review_vdot(
        self, user: AppUser, plan: TrainingPlan, profile: Optional[AthleteProfile], week_number: int, today: date,
    ) -> None:
        """Пересмотр VDOT перед неделями 5, 9, 13, ...: сохраняет новый VDOT в профиль и сообщает итог.

        Меняет profile.vdot на месте, чтобы неделя строилась по новым темпам. Сбой не останавливает рассылку.
        """
        chat_id = user.telegram_chat_id
        if (
            profile is None or not profile.vdot or self.garmin is None or not user.garmin_linked
            or not self.garmin.has_saved_tokens(chat_id)
            or not review_due(week_number, profile.vdot_reviewed_on, today)
        ):
            return
        try:
            runs = await self.garmin.get_runs_raw(chat_id, today - timedelta(weeks=RECENT_WEEKS), today)
        except GarminClientError as exc:
            logger.warning("Пересмотр VDOT отложен: нет данных Garmin (chat_id=%s): %s", chat_id, exc)
            return

        review = review_vdot(profile.vdot, runs, today)
        async with async_session_maker() as session:
            await UserService.set_reviewed_vdot(session, user.id, review.new, today)
        profile.vdot, profile.vdot_reviewed_on = review.new, today
        logger.info("Пересмотр VDOT (chat_id=%s): %s -> %s, %s", chat_id, review.old, review.new, review.reason)

        goal_note = None
        distance_m = race_distance_m(plan.target_race)
        if plan.target_time_s and distance_m:
            assessment = assess_goal(
                review.new, plan.target_time_s, distance_m, max(0, plan_total_weeks(today, plan.race_date)),
                goal_margin(profile, today),
            )
            goal_note = render_goal_after_review(assessment, distance_m)
        text = render_vdot_review(review, calculate_zones(review.new), goal_note)
        await self.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")

    async def _adapt_week(
        self,
        user: AppUser,
        plan: TrainingPlan,
        macro: MacroPlan,
        week_number: int,
        today: date,
        zones: Optional[TrainingZones],
        hr_basis: HrRef,
    ) -> Tuple[Optional[WeekAdjustment], MacroPlan, Optional[str]]:
        """Адаптация по факту: поправка следующей недели (уровень 1) и пересчёт макроплана по событиям (уровень 3).

        Сравнивает план текущей недели в БД с пробежками Garmin, собирает итог недели, сохраняет факт,
        при перерыве или двух слабых неделях подряд пересчитывает оставшийся километраж и сообщает об этом.
        Возвращает (поправка или None без данных Garmin, актуальный макроплан, текст итога недели или None).
        Сбой рассылку не останавливает.
        """
        chat_id = user.telegram_chat_id
        if self.garmin is None or not user.garmin_linked or not self.garmin.has_saved_tokens(chat_id):
            return None, macro, None
        current_monday = monday_of(today)
        window_start = today - timedelta(days=REPLAN_WINDOW_DAYS - 1)
        try:
            facts = await self.garmin.get_week_facts(chat_id, min(window_start, current_monday), today)
            async with async_session_maker() as session:
                weekly = await UserService.get_weekly_plan_by_start(session, plan.id, current_monday)
                previous = await UserService.get_weekly_plan_by_start(
                    session, plan.id, current_monday - timedelta(days=7),
                )
            planned = parse_week(weekly.plan_details) if weekly else None
        except GarminClientError as exc:
            logger.warning("Факт недели из Garmin не получен (chat_id=%s): %s", chat_id, exc)
            return None, macro, None
        except Exception:
            logger.exception("Ошибка подготовки корректировки недели (chat_id=%s)", chat_id)
            return None, macro, None

        runs = run_facts(facts["runs"])
        review = review_week(planned, current_monday, runs, today, zones, hr_basis) if planned else None
        if weekly is not None and review is not None and review.compliance is not None:
            async with async_session_maker() as session:
                await UserService.set_week_review(session, weekly.id, review_to_dict(review))
        summary = summarize_week(current_monday, runs, today, review, zones, hr_basis)
        summary_text = render_week_summary(summary) if has_content(summary) else None

        adjustment = adjust_next_week(
            target_km_for_week(macro, week_number), review,
            RecoverySignals(hrv_status=facts.get("hrv_status"), readiness=facts.get("readiness")),
        )
        if adjustment.changed:
            logger.info(
                "Корректировка недели (chat_id=%s): %s км, качественных не больше %s; %s",
                chat_id, adjustment.target_km, adjustment.max_quality, adjustment.reasons,
            )

        # Уровень 3: пересчёт оставшихся недель. Неделя №1 только что построена от текущей формы
        gap = longest_gap_days((r.day for r in runs), window_start, today)
        previous_compliance = (previous.review or {}).get("compliance") if previous else None
        reasons = replan_reasons(gap, [previous_compliance, review.compliance if review else None])
        if reasons and week_number > 1:
            plan_target = target_km_for_week(macro, week_number)
            start_km = start_km_after(plan_target, adjustment.target_km, gap)
            replan = replan_macro(macro, week_number, start_km, reasons)
            if replan.changes():
                async with async_session_maker() as session:
                    await UserService.update_plan_macro(session, plan.id, replan.macro)
                logger.info("План пересчитан (chat_id=%s) с недели %d: %s", chat_id, week_number, reasons)
                await self.bot.send_message(chat_id=chat_id, text=render_replan(replan), parse_mode="HTML")
                macro = replan.macro
                adjustment = adjustment_after_replan(adjustment, replan, gap)
        return adjustment, macro, summary_text

    async def _send_week_summary(self, chat_id: int, text: str) -> bool:
        """Итог уходящей недели отдельным сообщением перед новой неделей. Сбой отправки рассылку не останавливает."""
        try:
            await self.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        except TelegramAPIError as exc:
            logger.warning("Итог недели не отправлен (chat_id=%s): %s", chat_id, exc)
            return False
        return True

    async def weekly_distribution_tick(self, now_utc: Optional[datetime] = None) -> int:
        """Ежечасная проверка: отправляет неделю тем, у кого по их поясу воскресенье и уже 15:00.

        Повторную отправку отсекает проверка «неделя уже создана»; если генерация упала,
        следующая проверка через час попробует снова. Возвращает число отправленных недель.
        """
        now_utc = now_utc or datetime.now(timezone.utc)
        async with async_session_maker() as session:
            active_plans = await UserService.get_all_active_plans_with_users(session)

        sent = 0
        for user, plan, profile in active_plans:
            local = local_now(UserService.timezone_of(user), now_utc)
            if not is_weekly_send_time(local):
                continue
            if parse_macro(plan.plan_details) is None and local.hour != WEEKLY_SEND_HOUR:
                continue  # подсказку про старый формат плана шлём один раз, в первый час рассылки
            monday, _ = next_week_dates(local.date())
            try:
                async with self.locks.hold(user.telegram_chat_id, "рассылка недели") as acquired:
                    if not acquired:  # идёт команда пользователя: неделя не создана, попробуем через час
                        continue
                    async with async_session_maker() as session:
                        if await UserService.has_weekly_plan(session, plan.id, monday):
                            continue
                    with usage_scope(user.telegram_chat_id, USAGE_WEEKLY):
                        status = await self.send_week(user, plan, profile, today=local.date())
                    if status == WeekStatus.SENT:
                        sent += 1
            except Exception as exc:  # ошибка одного пользователя не останавливает рассылку остальным
                logger.exception("Ошибка при генерации недели для user_id=%s: %s", user.id, exc)

        if sent:
            logger.info("Рассылка недель: отправлено расписаний %d", sent)
        return sent

    def start(self) -> None:
        """Запуск планировщика задач."""
        # Каждый час в :00 проверяем, у кого по местному времени наступило воскресенье 15:00
        self.scheduler.add_job(
            self.weekly_distribution_tick,
            trigger=CronTrigger(minute=0),
            id="weekly_plan_tick",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        if self.activity_poller is not None:
            # max_instances=1: если круг опроса затянулся, следующий не стартует поверх него
            self.scheduler.add_job(
                self.activity_poller.poll_all,
                trigger=IntervalTrigger(minutes=self.poll_minutes),
                id="activity_poll_job",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
        # Каждый час в :00: у кого по местному времени наступил час напоминания, тому тренировка дня.
        # Задача есть всегда: свой час могут задать и при выключенном REMINDER_HOUR по умолчанию
        self.scheduler.add_job(
            self.morning_reminder_tick,
            trigger=CronTrigger(minute=0),
            id="morning_reminder_tick",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        if self.backup_dir is not None:
            # Раз в день: копирование БД не остановилось (иначе ERROR и уведомление админам)
            self.scheduler.add_job(
                self.check_backups,
                trigger=CronTrigger(hour=9, minute=30),
                id="backup_watch_job",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
        self.scheduler.start()
        logger.info(
            "Фоновый планировщик запущен (недели: ВС с 15:00 по времени пользователя; опрос Garmin: %s).",
            f"каждые {self.poll_minutes} мин" if self.activity_poller else "выключен",
        )

    async def morning_reminder_tick(self, now_utc: Optional[datetime] = None) -> int:
        """Ежечасно: тем, у кого по их поясу наступил их час напоминания (/reminder, по умолчанию REMINDER_HOUR),
        тренировка дня из сохранённой недели.

        Модель и Garmin не нужны: день уже в БД, темп и пульс считает код.
        shortcut: проверка только в начале часа; если бот был выключен в этот час, напоминание за день пропадает.
        """
        now_utc = now_utc or datetime.now(timezone.utc)
        due = []
        async with async_session_maker() as session:
            for user, plan, profile in await UserService.get_all_active_plans_with_users(session):
                local = local_now(UserService.timezone_of(user), now_utc)
                if local.hour != UserService.reminder_hour_of(user):   # -1 (выключено) не совпадёт ни с каким часом
                    continue
                weekly = await UserService.get_weekly_plan_by_start(session, plan.id, monday_of(local.date()))
                day = today_workout(parse_week(weekly.plan_details) if weekly else None, local.date())
                if day is not None:
                    due.append((user, profile, day, local.date()))

        sent = 0
        for user, profile, day, today in due:   # сессия БД закрыта: отправка не держит соединение
            text = render_today(day, today, zones_for_profile(profile), profile_hr_basis(profile))
            try:
                await self.bot.send_message(chat_id=user.telegram_chat_id, text=text, parse_mode="HTML")
                sent += 1
            except TelegramAPIError as exc:   # пользователь заблокировал бота: не повод будить админа
                logger.warning("Напоминание не отправлено (chat_id=%s): %s", user.telegram_chat_id, exc)
        if sent:
            logger.info("Утренние напоминания: отправлено %d", sent)
        return sent

    async def check_backups(self) -> None:
        check_backups(self.backup_dir, datetime.now(timezone.utc).date())

    def shutdown(self) -> None:
        """Остановка планировщика при завершении программы."""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Фоновый планировщик остановлен.")
