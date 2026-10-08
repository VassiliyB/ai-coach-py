from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, utcnow


class ProcessedActivity(Base):
    __tablename__ = "processed_activities"
    # Уникальность заодно даёт индекс по (user_id, garmin_activity_id)
    __table_args__ = (
        UniqueConstraint("user_id", "garmin_activity_id", name="uq_processed_activities_user_activity"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False)
    garmin_activity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
