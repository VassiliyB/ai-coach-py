import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import BASE_DIR, settings


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


# 4. Миграции при старте приложения: схему меняет только Alembic
def run_migrations() -> None:
    """alembic upgrade head. Синхронная: env.py сам запускает asyncio.run, поэтому вызывать
    через asyncio.to_thread, а не из работающего event loop."""
    # Alembic пишет по строке на каждый плагин уже при импорте: приглушаем до импорта
    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.attributes["configure_logger"] = False  # логи уже настроены приложением
    command.upgrade(cfg, "head")