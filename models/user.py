from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, utcnow

if TYPE_CHECKING:
    from models.athlete_profile import AthleteProfile
    from models.training_plan import TrainingPlan
    from models.message import ChatMessage


class AppUser(Base):
    __tablename__ = "app_users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    garmin_linked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
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