from datetime import datetime, timezone

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Единые имена ограничений: без них Alembic генерирует нестабильные имена
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """Timezone-aware текущее время в UTC."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Базовый декларативный класс для всех моделей проекта."""
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
