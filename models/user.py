from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, utcnow

if TYPE_CHECKING:
    from models.athlete_profile import AthleteProfile
    from models.message import ChatMessage
    from models.training_plan import TrainingPlan

# Доступ к боту (services.access_control): новый пользователь ждёт одобрения админа
ACCESS_PENDING = "pending"
ACCESS_APPROVED = "approved"
ACCESS_BLOCKED = "blocked"     # запрос отклонён или доступ отозван


class AppUser(Base):
    __tablename__ = "app_users"
    __table_args__ = (
        CheckConstraint(
            f"access IN ('{ACCESS_PENDING}', '{ACCESS_APPROVED}', '{ACCESS_BLOCKED}')", name="access",
        ),
        CheckConstraint("reminder_hour BETWEEN -1 AND 23", name="reminder_hour"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    garmin_linked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    # Имя IANA ('Asia/Almaty') или смещение ('UTC+05:00'); NULL = settings.DEFAULT_TIMEZONE
    timezone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # Час утреннего напоминания по поясу пользователя (/reminder): NULL = settings.REMINDER_HOUR, -1 = выключено
    reminder_hour: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    access: Mapped[str] = mapped_column(
        String(16), default=ACCESS_PENDING, server_default=ACCESS_PENDING, nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )

    # lazy="raise": любое неявное обращение к связи падает сразу, а не тихо делает запрос.
    # passive_deletes=True: удаление делегируется ON DELETE CASCADE в БД,
    # ORM не загружает дочерние строки при session.delete(user).
    profile: Mapped[Optional["AthleteProfile"]] = relationship(
        "AthleteProfile", back_populates="user", uselist=False,
        cascade="all, delete-orphan", passive_deletes=True, lazy="raise",
    )
    training_plans: Mapped[List["TrainingPlan"]] = relationship(
        "TrainingPlan", back_populates="user",
        cascade="all, delete-orphan", passive_deletes=True, lazy="raise",
    )
    chat_messages: Mapped[List["ChatMessage"]] = relationship(
        "ChatMessage", back_populates="user",
        cascade="all, delete-orphan", passive_deletes=True, lazy="raise",
    )
