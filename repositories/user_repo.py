from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import AppUser, AthleteProfile
from models.base import utcnow


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_chat_id(self, chat_id: int) -> Optional[AppUser]:
        result = await self.session.execute(select(AppUser).where(AppUser.telegram_chat_id == chat_id))
        return result.scalar_one_or_none()

    async def get_or_create(
        self, chat_id: int, username: Optional[str] = None, first_name: Optional[str] = None
    ) -> AppUser:
        """Атомарно: INSERT ... ON CONFLICT DO NOTHING, затем SELECT. Без гонок при двух /start."""
        await self.session.execute(
            pg_insert(AppUser)
            .values(telegram_chat_id=chat_id, username=username, first_name=first_name, garmin_linked=False)
            .on_conflict_do_nothing(index_elements=[AppUser.telegram_chat_id])
        )
        user = await self.get_by_chat_id(chat_id)
        assert user is not None
        # Актуализируем имя, если человек сменил его в Telegram
        if (username, first_name) != (None, None) and (user.username, user.first_name) != (username, first_name):
            user.username, user.first_name = username, first_name
            await self.session.flush()
        return user

    async def set_garmin_linked(self, chat_id: int, linked: bool) -> None:
        await self.session.execute(
            update(AppUser).where(AppUser.telegram_chat_id == chat_id).values(garmin_linked=linked)
        )

    async def list_linked_with_profiles(self) -> List[Tuple[AppUser, Optional[AthleteProfile]]]:
        """Пользователи с привязанным Garmin и их профили (профиля может ещё не быть)."""
        stmt = (
            select(AppUser, AthleteProfile)
            .outerjoin(AthleteProfile, AthleteProfile.user_id == AppUser.id)
            .where(AppUser.garmin_linked.is_(True))
            .order_by(AppUser.id)
        )
        return list((await self.session.execute(stmt)).tuples().all())

    async def get_profile(self, user_id: int) -> Optional[AthleteProfile]:
        result = await self.session.execute(select(AthleteProfile).where(AthleteProfile.user_id == user_id))
        return result.scalar_one_or_none()

    async def upsert_profile(self, user_id: int, data: Dict[str, Any]) -> AthleteProfile:
        values = {
            "raw_summary_text": data.get("summary_text"),
            "average_weekly_km": data.get("average_weekly_km"),
            "max_heart_rate": data.get("max_hr"),
            "typical_easy_heart_rate": data.get("typical_easy_hr"),
            "garmin_vo2_max": data.get("vo2_max"),
            "updated_at": utcnow(),  # onupdate не срабатывает в Core-upsert, ставим явно
            "vdot": data.get("vdot"),
            "best_effort_distance_m": data.get("best_effort_distance_m"),
            "best_effort_time_s": data.get("best_effort_time_s"),
        }
        stmt = (
            pg_insert(AthleteProfile)
            .values(user_id=user_id, **values)
            .on_conflict_do_update(index_elements=[AthleteProfile.user_id], set_=values)
            .returning(AthleteProfile)
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(stmt)).scalar_one()