from datetime import datetime
from typing import TYPE_CHECKING, Optional
from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base

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
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["AppUser"] = relationship("AppUser", back_populates="profile")