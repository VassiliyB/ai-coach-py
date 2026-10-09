from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, utcnow

if TYPE_CHECKING:
    from models.training_plan import TrainingPlan


class WeeklyPlan(Base):
    __tablename__ = "weekly_plans"
    __table_args__ = (
        UniqueConstraint("training_plan_id", "week_start_date", name="uq_weekly_plans_plan_week"),
        Index("ix_weekly_plans_user_week", "user_id", "week_start_date"),
        CheckConstraint("week_end_date >= week_start_date", name="week_range_valid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False)
    training_plan_id: Mapped[int] = mapped_column(ForeignKey("training_plans.id", ondelete="CASCADE"), nullable=False)

    week_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    week_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Структурный план (WeekPlan) или {"legacy_text": "..."} для старых текстовых планов
    plan_details: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Выгруженные в календарь Garmin тренировки: [{"date": "YYYY-MM-DD", "workout_id": int}]
    garmin_workouts: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )

    training_plan: Mapped["TrainingPlan"] = relationship(
        "TrainingPlan", back_populates="weekly_plans", lazy="raise"
    )
