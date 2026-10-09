from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import exists, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import AppUser, AthleteProfile, TrainingPlan, WeeklyPlan
from models.base import utcnow


class PlanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_active(self, user_id: int) -> Optional[TrainingPlan]:
        result = await self.session.execute(
            select(TrainingPlan).where(TrainingPlan.user_id == user_id, TrainingPlan.active.is_(True))
        )
        return result.scalar_one_or_none()

    async def create_active(
        self, user_id: int, target_race: str, race_date: date, total_weeks: int, plan_details: Dict[str, Any],
        target_time_s: Optional[int] = None,
    ) -> TrainingPlan:
        """Деактивирует старые планы и создаёт новый в одной транзакции."""
        # UPDATE выполняется сразу (не через autoflush), поэтому частичный unique-индекс не сработает
        await self.session.execute(
            update(TrainingPlan)
            .where(TrainingPlan.user_id == user_id, TrainingPlan.active.is_(True))
            .values(active=False)
        )
        plan = TrainingPlan(
            user_id=user_id, target_race=target_race, race_date=race_date,
            total_weeks=total_weeks, plan_details=plan_details, active=True, target_time_s=target_time_s,
        )
        self.session.add(plan)
        await self.session.flush()
        return plan

    async def list_active_with_users(self) -> List[Tuple[AppUser, TrainingPlan, Optional[AthleteProfile]]]:
        stmt = (
            select(AppUser, TrainingPlan, AthleteProfile)
            .join(TrainingPlan, TrainingPlan.user_id == AppUser.id)
            .outerjoin(AthleteProfile, AthleteProfile.user_id == AppUser.id)
            .where(TrainingPlan.active.is_(True), AppUser.garmin_linked.is_(True))
        )
        return list((await self.session.execute(stmt)).tuples().all())

    async def has_weekly(self, training_plan_id: int, week_start: date) -> bool:
        stmt = select(exists().where(
            WeeklyPlan.training_plan_id == training_plan_id, WeeklyPlan.week_start_date == week_start,
        ))
        return bool((await self.session.execute(stmt)).scalar())

    async def upsert_weekly(
        self, user_id: int, training_plan_id: int, week_start: date, week_end: date, plan_details: Dict[str, Any]
    ) -> WeeklyPlan:
        """Повторная генерация той же недели перезаписывает расписание, а не плодит дубли."""
        stmt = (
            pg_insert(WeeklyPlan)
            .values(
                user_id=user_id, training_plan_id=training_plan_id,
                week_start_date=week_start, week_end_date=week_end, plan_details=plan_details,
            )
            .on_conflict_do_update(
                constraint="uq_weekly_plans_plan_week",
                set_={"week_end_date": week_end, "plan_details": plan_details, "created_at": utcnow()},
            )
            .returning(WeeklyPlan)
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def get_weekly(self, weekly_id: int, user_id: int) -> Optional[WeeklyPlan]:
        """Неделя пользователя по id (чужую не вернёт)."""
        result = await self.session.execute(
            select(WeeklyPlan).where(WeeklyPlan.id == weekly_id, WeeklyPlan.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def set_garmin_workouts(self, weekly_id: int, workouts: List[Dict[str, Any]]) -> None:
        await self.session.execute(
            update(WeeklyPlan).where(WeeklyPlan.id == weekly_id).values(garmin_workouts=workouts)
        )

    async def set_target_time(self, plan_id: int, target_time_s: Optional[int]) -> None:
        await self.session.execute(
            update(TrainingPlan).where(TrainingPlan.id == plan_id).values(target_time_s=target_time_s)
        )
