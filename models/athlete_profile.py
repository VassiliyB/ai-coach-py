from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Date, DateTime, Float, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, utcnow

if TYPE_CHECKING:
    from models.user import AppUser


class AthleteProfile(Base):
    __tablename__ = "athlete_profiles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), unique=True, nullable=False)

    raw_summary_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    average_weekly_km: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_heart_rate: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    typical_easy_heart_rate: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    garmin_vo2_max: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    vdot: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    best_effort_distance_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    best_effort_time_s: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Дата последнего пересмотра VDOT раз в 4 недели (services.vdot_review); /sync её не трогает
    vdot_reviewed_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # Результат забега из /race (services.race_result): пока свежий, VDOT задаёт он, а не тренировки
    race_result_distance_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    race_result_time_s: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    race_result_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False
    )

    user: Mapped["AppUser"] = relationship("AppUser", back_populates="profile", lazy="raise")
