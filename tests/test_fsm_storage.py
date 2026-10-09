import asyncio
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage

from bot.fsm_storage import FSM_TTL, check_fsm_storage, create_fsm_storage


def test_without_url_memory_storage():
    assert isinstance(create_fsm_storage(None), MemoryStorage)
    assert isinstance(create_fsm_storage(""), MemoryStorage)


def test_with_url_redis_storage_with_ttl():
    # from_url не подключается к Redis: тест работает без сети
    storage = create_fsm_storage("redis://localhost:6379/0")
    assert isinstance(storage, RedisStorage)
    assert storage.state_ttl == FSM_TTL and storage.data_ttl == FSM_TTL
    asyncio.run(storage.close())


def test_check_skips_memory_and_pings_redis():
    asyncio.run(check_fsm_storage(MemoryStorage()))      # без исключения и без сети

    storage = create_fsm_storage("redis://localhost:6379/0")
    storage.redis.ping = AsyncMock(return_value=True)
    asyncio.run(check_fsm_storage(storage))
    storage.redis.ping.assert_awaited_once()


def test_check_fails_when_redis_unavailable():
    storage = create_fsm_storage("redis://localhost:6379/0")
    storage.redis.ping = AsyncMock(side_effect=ConnectionError("нет Redis"))
    with pytest.raises(ConnectionError):
        asyncio.run(check_fsm_storage(storage))
