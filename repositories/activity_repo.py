from typing import Iterable

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import ProcessedActivity

_UQ = "uq_processed_activities_user_activity"


class ActivityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def mark_processed(self, user_id: int, garmin_activity_id: str) -> bool:
        """True, если активность новая; False, если уже была обработана. Атомарно."""
        stmt = (
            pg_insert(ProcessedActivity)
            .values(user_id=user_id, garmin_activity_id=garmin_activity_id)
            .on_conflict_do_nothing(constraint=_UQ)
            .returning(ProcessedActivity.id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def mark_many(self, user_id: int, garmin_activity_ids: Iterable[str]) -> None:
        """Помечает активности обработанными одним запросом (уже помеченные пропускаются)."""
        rows = [{"user_id": user_id, "garmin_activity_id": aid} for aid in garmin_activity_ids]
        if rows:
            await self.session.execute(pg_insert(ProcessedActivity).values(rows).on_conflict_do_nothing(constraint=_UQ))

    async def has_any(self, user_id: int) -> bool:
        """Есть ли у пользователя хотя бы одна обработанная активность (был ли первый опрос)."""
        stmt = select(exists().where(ProcessedActivity.user_id == user_id))
        return bool((await self.session.execute(stmt)).scalar())
