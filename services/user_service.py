# services/user_service.py
"""Тонкий фасад над репозиториями: управляет транзакциями (commit), логики здесь нет."""
import logging
from datetime import date, datetime, tzinfo
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from clients.llm_usage import UsageRecord, UsageScope
from models import AppUser, AthleteProfile, TrainingPlan, WeeklyPlan
from models.user import ACCESS_APPROVED, ACCESS_PENDING
from repositories import (
    ActivityRepository,
    MessageRepository,
    PlanRepository,
    UsageRepository,
    UsageTotals,
    UserRepository,
)
from schemas.plan import MacroPlan, WeekPlan
from services.plan_storage import plan_to_details
from services.user_time import to_tzinfo

logger = logging.getLogger(__name__)


class UserService:

    @staticmethod
    async def get_or_create_user(
        session: AsyncSession, chat_id: int,
        username: Optional[str] = None, first_name: Optional[str] = None,
    ) -> AppUser:
        """Для хендлеров: до них доходят только допущенные (AccessMiddleware), новая запись сразу с доступом.

        Новая запись здесь бывает у админа при первом обращении.
        """
        user, _ = await UserRepository(session).get_or_create(chat_id, username, first_name, access=ACCESS_APPROVED)
        await session.commit()
        return user

    @staticmethod
    async def request_access(
        session: AsyncSession, chat_id: int, username: Optional[str], first_name: Optional[str],
    ) -> Tuple[AppUser, bool]:
        """Запись пользователя без доступа (pending). True, если запрос новый и о нём надо сообщить админам."""
        user, created = await UserRepository(session).get_or_create(
            chat_id, username, first_name, access=ACCESS_PENDING,
        )
        await session.commit()
        return user, created

    @staticmethod
    async def set_access(session: AsyncSession, chat_id: int, access: str) -> Optional[AppUser]:
        user = await UserRepository(session).set_access(chat_id, access)
        await session.commit()
        return user

    @staticmethod
    async def load_approved(session: AsyncSession, admin_ids: Iterable[int]) -> List[int]:
        """При старте: админы из настроек получают доступ и в БД (иначе фоновые задачи их пропустят),
        возвращает всех одобренных."""
        repo = UserRepository(session)
        await repo.approve_existing(admin_ids)
        await session.commit()
        return await repo.approved_chat_ids()

    @staticmethod
    async def list_users(session: AsyncSession) -> List[AppUser]:
        return await UserRepository(session).list_all()

    @staticmethod
    async def delete_user(session: AsyncSession, chat_id: int) -> bool:
        """Удаляет пользователя и все его данные в БД (каскадом). True, если пользователь был."""
        deleted = await UserRepository(session).delete_by_chat_id(chat_id)
        await session.commit()
        return deleted

    @staticmethod
    async def set_timezone(session: AsyncSession, user_id: int, tz: str) -> None:
        """tz уже нормализован (services.user_time.normalize_timezone)."""
        await UserRepository(session).set_timezone(user_id, tz)
        await session.commit()

    @staticmethod
    def timezone_of(user: AppUser) -> tzinfo:
        """Часовой пояс пользователя; если не задан, пояс по умолчанию из настроек."""
        from config import settings
        return to_tzinfo(user.timezone, default=settings.DEFAULT_TIMEZONE)

    @staticmethod
    async def timezone_for_chat(session: AsyncSession, chat_id: int) -> tzinfo:
        """Пояс по chat_id (время в уведомлениях админам); записи нет: пояс по умолчанию."""
        from config import settings
        user = await UserRepository(session).get_by_chat_id(chat_id)
        return to_tzinfo(user.timezone if user else None, default=settings.DEFAULT_TIMEZONE)

    @staticmethod
    async def record_llm_usage(session: AsyncSession, scope: UsageScope, record: UsageRecord) -> None:
        """Sink для clients.llm_usage: одна строка llm_usage на вызов модели."""
        await UsageRepository(session).add(
            scope.chat_id, scope.command, scope.request_id, record.model,
            record.input_tokens, record.output_tokens, record.cache_read_tokens, record.cache_write_tokens,
        )
        await session.commit()

    @staticmethod
    async def count_requests_since(session: AsyncSession, chat_id: int, command: str, since: datetime) -> int:
        return await UsageRepository(session).count_requests(chat_id, command, since)

    @staticmethod
    async def usage_totals_since(session: AsyncSession, since: datetime) -> Dict[int, UsageTotals]:
        return await UsageRepository(session).totals_by_chat(since)

    @staticmethod
    async def recent_messages(
        session: AsyncSession, user_id: int, since: datetime, limit: int,
    ) -> List[Tuple[str, str]]:
        return await MessageRepository(session).recent(user_id, since, limit)

    @staticmethod
    async def save_ask_exchange(
        session: AsyncSession, user_id: int, question: str, answer: str, keep_since: datetime,
    ) -> None:
        """Вопрос /ask и ответ тренера; заодно удаляет историю пользователя старше keep_since."""
        repo = MessageRepository(session)
        await repo.delete_older(user_id, keep_since)
        await repo.add_exchange(user_id, question, answer)
        await session.commit()

    @staticmethod
    async def set_garmin_linked(session: AsyncSession, chat_id: int, linked: bool = True) -> None:
        await UserRepository(session).set_garmin_linked(chat_id, linked)
        await session.commit()

    @staticmethod
    async def save_athlete_profile(
        session: AsyncSession, user_id: int, profile_data: Dict[str, Any]
    ) -> AthleteProfile:
        profile = await UserRepository(session).upsert_profile(user_id, profile_data)
        await session.commit()
        return profile

    @staticmethod
    async def set_reviewed_vdot(session: AsyncSession, user_id: int, vdot: float, reviewed_on: date) -> None:
        """VDOT после пересмотра раз в 4 недели (services.vdot_review)."""
        await UserRepository(session).set_reviewed_vdot(user_id, vdot, reviewed_on)
        await session.commit()

    @staticmethod
    async def set_race_result(
        session: AsyncSession, user_id: int, distance_m: float, time_s: int, race_date: date, vdot: float,
    ) -> AthleteProfile:
        """Результат забега из /race, уже проверен services.race_result.check_race."""
        profile = await UserRepository(session).set_race_result(user_id, distance_m, time_s, race_date, vdot)
        await session.commit()
        return profile

    @staticmethod
    async def set_pulse(
        session: AsyncSession, user_id: int, manual_max_hr: Optional[int], lthr: Optional[int],
    ) -> AthleteProfile:
        """Ручные ЧССmax и ПАНО, уже проверены services.heart_rate.check_pulse."""
        profile = await UserRepository(session).set_pulse(user_id, manual_max_hr, lthr)
        await session.commit()
        return profile

    @staticmethod
    async def get_athlete_profile(session: AsyncSession, user_id: int) -> Optional[AthleteProfile]:
        return await UserRepository(session).get_profile(user_id)

    @staticmethod
    async def save_training_plan(
        session: AsyncSession, user_id: int, target_race: str,
        race_date: date, total_weeks: int, plan: MacroPlan, target_time_s: Optional[int] = None,
    ) -> TrainingPlan:
        saved = await PlanRepository(session).create_active(
            user_id, target_race, race_date, total_weeks, plan_to_details(plan), target_time_s=target_time_s,
        )
        await session.commit()
        return saved

    @staticmethod
    async def set_plan_target_time(session: AsyncSession, plan_id: int, target_time_s: Optional[int]) -> None:
        """target_time_s уже проверен services.race_goal; None убирает цель."""
        await PlanRepository(session).set_target_time(plan_id, target_time_s)
        await session.commit()

    @staticmethod
    async def get_active_plan(session: AsyncSession, user_id: int) -> Optional[TrainingPlan]:
        return await PlanRepository(session).get_active(user_id)

    @staticmethod
    async def get_all_active_plans_with_users(
        session: AsyncSession,
    ) -> List[Tuple[AppUser, TrainingPlan, Optional[AthleteProfile]]]:
        return await PlanRepository(session).list_active_with_users()

    @staticmethod
    async def has_weekly_plan(session: AsyncSession, training_plan_id: int, week_start: date) -> bool:
        return await PlanRepository(session).has_weekly(training_plan_id, week_start)

    @staticmethod
    async def save_weekly_plan(
        session: AsyncSession, user_id: int, training_plan_id: int,
        week_start: date, week_end: date, plan: WeekPlan,
    ) -> WeeklyPlan:
        weekly = await PlanRepository(session).upsert_weekly(
            user_id, training_plan_id, week_start, week_end, plan_to_details(plan)
        )
        await session.commit()
        return weekly

    @staticmethod
    async def get_weekly_plan_by_start(
        session: AsyncSession, training_plan_id: int, week_start: date,
    ) -> Optional[WeeklyPlan]:
        return await PlanRepository(session).get_weekly_by_start(training_plan_id, week_start)

    @staticmethod
    async def set_week_review(session: AsyncSession, weekly_id: int, review: Dict[str, Any]) -> None:
        await PlanRepository(session).set_week_review(weekly_id, review)
        await session.commit()

    @staticmethod
    async def update_plan_macro(session: AsyncSession, plan_id: int, plan: MacroPlan) -> None:
        """Пересчитанный макроплан (services.macro_replan) вместо прежнего."""
        await PlanRepository(session).set_plan_details(plan_id, plan_to_details(plan))
        await session.commit()

    @staticmethod
    async def get_weekly_plan(session: AsyncSession, weekly_id: int, user_id: int) -> Optional[WeeklyPlan]:
        return await PlanRepository(session).get_weekly(weekly_id, user_id)

    @staticmethod
    async def set_garmin_workouts(session: AsyncSession, weekly_id: int, workouts: List[Dict[str, Any]]) -> None:
        await PlanRepository(session).set_garmin_workouts(weekly_id, workouts)
        await session.commit()

    # ---------------- Обработанные активности (поллинг Garmin) ----------------

    @staticmethod
    async def list_linked_users_with_profiles(
        session: AsyncSession,
    ) -> List[Tuple[AppUser, Optional[AthleteProfile]]]:
        return await UserRepository(session).list_linked_with_profiles()

    @staticmethod
    async def has_processed_activities(session: AsyncSession, user_id: int) -> bool:
        return await ActivityRepository(session).has_any(user_id)

    @staticmethod
    async def mark_activities_processed(session: AsyncSession, user_id: int, activity_ids: Iterable[str]) -> None:
        await ActivityRepository(session).mark_many(user_id, activity_ids)
        await session.commit()

    @staticmethod
    async def claim_activity(session: AsyncSession, user_id: int, activity_id: str) -> bool:
        """Атомарно помечает активность обработанной. True, если её ещё никто не обработал."""
        claimed = await ActivityRepository(session).mark_processed(user_id, activity_id)
        await session.commit()
        return claimed
