from datetime import date, datetime
from typing import TYPE_CHECKING, List
from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base

if TYPE_CHECKING:
    from models.user import AppUser
    from models.weekly_plan import WeeklyPlan


class TrainingPlan(Base):
    __tablename__ = "training_plans"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False)

    target_race: Mapped[str] = mapped_column(String(255), nullable=False)
    race_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_weeks: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_details: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["AppUser"] = relationship("AppUser", back_populates="training_plans")
    weekly_plans: Mapped[List["WeeklyPlan"]] = relationship(
        "WeeklyPlan",
        back_populates="training_plan",
        cascade="all, delete-orphan",
        lazy="selectin",
    )