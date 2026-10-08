from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Dict, List

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey,
    Index, Integer, String, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, utcnow

if TYPE_CHECKING:
    from models.user import AppUser
    from models.weekly_plan import WeeklyPlan


class TrainingPlan(Base):
    __tablename__ = "training_plans"
    __table_args__ = (
        Index("ix_training_plans_user_id", "user_id"),
        # Не более одного активного плана на пользователя
        Index("uq_training_plans_one_active_per_user", "user_id",
              unique=True, postgresql_where=text("active")),
        CheckConstraint("total_weeks > 0", name="total_weeks_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False)

    target_race: Mapped[str] = mapped_column(String(255), nullable=False)
    race_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_weeks: Mapped[int] = mapped_column(Integer, nullable=False)
    # Структурный план (MacroPlan) или {"legacy_text": "..."} для старых текстовых планов
    plan_details: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )

    user: Mapped["AppUser"] = relationship("AppUser", back_populates="training_plans", lazy="raise")
    weekly_plans: Mapped[List["WeeklyPlan"]] = relationship(
        "WeeklyPlan", back_populates="training_plan",
        cascade="all, delete-orphan", passive_deletes=True, lazy="raise",
    )