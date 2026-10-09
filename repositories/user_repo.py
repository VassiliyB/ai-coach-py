from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import AppUser, AthleteProfile
from models.base import utcnow
from models.user import ACCESS_APPROVED, ACCESS_PENDING


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_chat_id(self, chat_id: int) -> Optional[AppUser]:
        result = await self.session.execute(select(AppUser).where(AppUser.telegram_chat_id == chat_id))
        return result.scalar_one_or_none()

    async def get_or_create(
        self, chat_id: int, username: Optional[str] = None, first_name: Optional[str] = None,
        access: str = ACCESS_PENDING,
    ) -> Tuple[AppUser, bool]:
        """Атомарно: INSERT ... ON CONFLICT DO NOTHING, затем SELECT. Без гонок при двух /start.

        access задаёт статус только новой записи. Второе значение: True, если запись создана сейчас.
        """
        inserted = await self.session.execute(
            pg_insert(AppUser)
            .values(
                telegram_chat_id=chat_id, username=username, first_name=first_name,
                garmin_linked=False, access=access,
            )
            .on_conflict_do_nothing(index_elements=[AppUser.telegram_chat_id])
            .returning(AppUser.id)
        )
        created = inserted.scalar_one_or_none() is not None
        user = await self.get_by_chat_id(chat_id)
        assert user is not None
        # Актуализируем имя, если человек сменил его в Telegram
        if (username, first_name) != (None, None) and (user.username, user.first_name) != (username, first_name):
            user.username, user.first_name = username, first_name
            await self.session.flush()
        return user, created

    async def set_access(self, chat_id: int, access: str) -> Optional[AppUser]:
        """Новый статус доступа; None, если пользователя нет."""
        stmt = (
            update(AppUser).where(AppUser.telegram_chat_id == chat_id).values(access=access)
            .returning(AppUser).execution_options(populate_existing=True)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def approve_existing(self, chat_ids: Iterable[int]) -> None:
        """Одобряет уже существующие записи (админов из настроек); новых не создаёт."""
        ids = list(chat_ids)
        if ids:
            await self.session.execute(
                update(AppUser)
                .where(AppUser.telegram_chat_id.in_(ids), AppUser.access != ACCESS_APPROVED)
                .values(access=ACCESS_APPROVED)
            )

    async def approved_chat_ids(self) -> List[int]:
        stmt = select(AppUser.telegram_chat_id).where(AppUser.access == ACCESS_APPROVED)
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_all(self) -> List[AppUser]:
        return list((await self.session.execute(select(AppUser).order_by(AppUser.id))).scalars().all())

    async def set_garmin_linked(self, chat_id: int, linked: bool) -> None:
        await self.session.execute(
            update(AppUser).where(AppUser.telegram_chat_id == chat_id).values(garmin_linked=linked)
        )

    async def list_linked_with_profiles(self) -> List[Tuple[AppUser, Optional[AthleteProfile]]]:
        """Пользователи с привязанным Garmin и их профили (профиля может ещё не быть)."""
        stmt = (
            select(AppUser, AthleteProfile)
            .outerjoin(AthleteProfile, AthleteProfile.user_id == AppUser.id)
            .where(AppUser.garmin_linked.is_(True), AppUser.access == ACCESS_APPROVED)
            .order_by(AppUser.id)
        )
        return list((await self.session.execute(stmt)).tuples().all())

    async def delete_by_chat_id(self, chat_id: int) -> bool:
        """Удаляет пользователя; профиль, планы, недели, активности и сообщения удаляет ON DELETE CASCADE в БД.

        True, если пользователь был.
        """
        stmt = delete(AppUser).where(AppUser.telegram_chat_id == chat_id).returning(AppUser.id)
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def set_timezone(self, user_id: int, tz: Optional[str]) -> None:
        await self.session.execute(update(AppUser).where(AppUser.id == user_id).values(timezone=tz))

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

    async def set_race_result(
        self, user_id: int, distance_m: float, time_s: int, race_date: date, vdot: float,
    ) -> AthleteProfile:
        """Забег из /race: VDOT по нему, дата забега как дата последней оценки формы.
        Профиля может не быть (без /sync), поэтому upsert."""
        values = {
            "race_result_distance_m": distance_m,
            "race_result_time_s": time_s,
            "race_result_date": race_date,
            "vdot": vdot,
            "vdot_reviewed_on": race_date,
            "updated_at": utcnow(),
        }
        stmt = (
            pg_insert(AthleteProfile)
            .values(user_id=user_id, **values)
            .on_conflict_do_update(index_elements=[AthleteProfile.user_id], set_=values)
            .returning(AthleteProfile)
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def set_reviewed_vdot(self, user_id: int, vdot: float, reviewed_on: date) -> None:
        await self.session.execute(
            update(AthleteProfile)
            .where(AthleteProfile.user_id == user_id)
            .values(vdot=vdot, vdot_reviewed_on=reviewed_on, updated_at=utcnow())
        )
