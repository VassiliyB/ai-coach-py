# services/user_service.py
import logging
from datetime import date
from typing import Any, Dict, Optional, List, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import WeeklyPlan
from models.user import AppUser
from models.athlete_profile import AthleteProfile
from models.training_plan import TrainingPlan

logger = logging.getLogger(__name__)


class UserService:
    """Изолированный сервис для работы с пользователями, профилями и планами в БД."""

    @staticmethod
    async def get_or_create_user(
        session: AsyncSession,
        chat_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
    ) -> AppUser:
        """Находит пользователя по chat_id или создает нового."""
        stmt = select(AppUser).where(AppUser.telegram_chat_id == chat_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            user = AppUser(
                telegram_chat_id=chat_id,
                username=username,
                first_name=first_name,
                garmin_linked=False,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            logger.info("Создан новый пользователь chat_id=%s", chat_id)
        return user

    @staticmethod
    async def set_garmin_linked(session: AsyncSession, chat_id: int, linked: bool = True) -> None:
        """Обновляет флаг успешной привязки аккаунта Garmin."""
        stmt = select(AppUser).where(AppUser.telegram_chat_id == chat_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if user:
            user.garmin_linked = linked
            await session.commit()

    @staticmethod
    async def save_athlete_profile(
        session: AsyncSession,
        user_id: int,
        profile_data: Dict[str, Any],
    ) -> AthleteProfile:
        """Сохраняет или обновляет 90-дневный паспорт атлета."""
        stmt = select(AthleteProfile).where(AthleteProfile.user_id == user_id)
        result = await session.execute(stmt)
        profile = result.scalar_one_or_none()

        if not profile:
            profile = AthleteProfile(user_id=user_id)
            session.add(profile)

        profile.raw_summary_text = profile_data.get("summary_text")
        profile.average_weekly_km = profile_data.get("average_weekly_km")
        profile.max_heart_rate = profile_data.get("max_hr")
        profile.typical_easy_heart_rate = profile_data.get("typical_easy_hr")
        profile.garmin_vo2_max = profile_data.get("vo2_max")

        await session.commit()
        await session.refresh(profile)
        return profile

    @staticmethod
    async def get_athlete_profile(session: AsyncSession, user_id: int) -> Optional[AthleteProfile]:
        """Возвращает текущий паспорт атлета."""
        stmt = select(AthleteProfile).where(AthleteProfile.user_id == user_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def save_training_plan(
        session: AsyncSession,
        user_id: int,
        target_race: str,
        race_date: date,
        total_weeks: int,
        plan_details: str,
    ) -> TrainingPlan:
        """Сохраняет сформированный макроцикл, деактивируя старые."""
        # Деактивируем предыдущие активные планы
        stmt = select(TrainingPlan).where(
            TrainingPlan.user_id == user_id,
            TrainingPlan.active == True  # noqa: E712
        )
        result = await session.execute(stmt)
        for old_plan in result.scalars().all():
            old_plan.active = False

        new_plan = TrainingPlan(
            user_id=user_id,
            target_race=target_race,
            race_date=race_date,
            total_weeks=total_weeks,
            plan_details=plan_details,
            active=True,
        )
        session.add(new_plan)
        await session.commit()
        await session.refresh(new_plan)
        return new_plan

    @staticmethod
    async def get_all_active_plans_with_users(session: AsyncSession) -> List[
        Tuple[AppUser, TrainingPlan, Optional[AthleteProfile]]]:
        """Возвращает список кортежей (пользователь, активный план, паспорт) для рассылки."""
        stmt = (
            select(AppUser, TrainingPlan, AthleteProfile)
            .join(TrainingPlan, TrainingPlan.user_id == AppUser.id)
            .outerjoin(AthleteProfile, AthleteProfile.user_id == AppUser.id)
            .where(
                TrainingPlan.active == True,  # noqa: E712
                AppUser.garmin_linked == True,  # noqa: E712
            )
        )
        result = await session.execute(stmt)
        return result.all()  # type: ignore

    @staticmethod
    async def save_weekly_plan(
            session: AsyncSession,
            user_id: int,
            training_plan_id: int,
            week_start: date,
            week_end: date,
            plan_details: str,
    ) -> WeeklyPlan:
        """Сохраняет сгенерированный микроцикл на неделю."""
        weekly_plan = WeeklyPlan(
            user_id=user_id,
            training_plan_id=training_plan_id,
            week_start_date=week_start,
            week_end_date=week_end,
            plan_details=plan_details,
        )
        session.add(weekly_plan)
        await session.commit()
        await session.refresh(weekly_plan)
        return weekly_plan