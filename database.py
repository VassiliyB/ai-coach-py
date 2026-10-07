from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import settings
from models import Base  # Импортируем из __init__.py, чтобы подтянулись ВСЕ модели!


# 1. Асинхронный движок (Engine)
engine = create_async_engine(
    url=settings.DATABASE_URL.get_secret_value(),
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)

# 2. Фабрика асинхронных сессий
async_session_maker = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


# 3. Асинхронный контекстный менеджер сессий
@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, Any]:
    """Предоставляет сессию БД с безопасным коммитом и откатом."""
    async with async_session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# 4. Функция автосоздания таблиц при старте приложения
async def init_models() -> None:
    """Создает таблицы в БД, если их еще нет."""
    async with engine.begin() as conn:
        # Здесь Base активно используется, и импорт сверху перестанет быть серым!
        await conn.run_sync(Base.metadata.create_all)