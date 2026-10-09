# bot/fsm_storage.py
"""Хранилище состояний диалогов (FSM): Redis, если задан REDIS_URL, иначе память процесса.

В памяти любой начатый диалог (мастер /plan, /ask без текста, новая цель в /show_plan) обрывается при
перезапуске бота, в том числе после каждого docker compose up --build. В Redis состояние переживает
перезапуск. Исключение: ввод кода MFA. Незавершённый вход Garmin живёт в памяти GarminClient и не
сериализуется, поэтому после перезапуска код не примут и попросят войти заново через /start.
"""
import logging
from datetime import timedelta
from typing import Optional

from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage

logger = logging.getLogger(__name__)

# Диалоги короткие: брошенный через сутки сбрасывается, а не ловит случайный текст через неделю
FSM_TTL = timedelta(hours=24)


def create_fsm_storage(redis_url: Optional[str]) -> BaseStorage:
    """RedisStorage по URL или MemoryStorage без него. Подключение к Redis ленивое: проверяет check_fsm_storage."""
    if not redis_url:
        logger.warning("REDIS_URL не задан: состояния диалогов в памяти и теряются при перезапуске")
        return MemoryStorage()
    from aiogram.fsm.storage.redis import RedisStorage  # пакет redis нужен только с REDIS_URL

    return RedisStorage.from_url(redis_url, state_ttl=FSM_TTL, data_ttl=FSM_TTL)


async def check_fsm_storage(storage: BaseStorage) -> None:
    """При запуске: Redis должен отвечать, иначе бот упал бы на первом же диалоге. Память не проверяется."""
    redis = getattr(storage, "redis", None)
    if redis is None:
        return
    await redis.ping()
    logger.info("Состояния диалогов хранятся в Redis.")
