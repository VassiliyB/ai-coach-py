
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import ProcessedActivity


class ActivityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def mark_processed(self, user_id: int, garmin_activity_id: str) -> bool:
        """True, если активность новая; False, если уже была обработана. Атомарно."""
        stmt = (
            pg_insert(ProcessedActivity)
            .values(user_id=user_id, garmin_activity_id=garmin_activity_id)
            .on_conflict_do_nothing(constraint="uq_processed_activities_user_activity")
            .returning(ProcessedActivity.id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None