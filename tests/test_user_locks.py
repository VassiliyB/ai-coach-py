import asyncio
from types import SimpleNamespace

import pytest

from bot.middlewares import UserLockMiddleware
from services.user_locks import UserLocks


def test_second_acquire_is_refused_until_release():
    locks = UserLocks()
    assert locks.try_acquire(1, "/sync")
    assert not locks.try_acquire(1, "/plan")
    assert locks.current(1) == "/sync"
    assert locks.try_acquire(2, "/plan")          # другой пользователь не блокируется
    locks.release(1)
    assert locks.current(1) is None
    assert locks.try_acquire(1, "/plan")


def test_hold_releases_on_exception():
    locks = UserLocks()

    async def run():
        with pytest.raises(RuntimeError):
            async with locks.hold(1, "/sync") as acquired:
                assert acquired
                raise RuntimeError("сбой Garmin")
        async with locks.hold(1, "/analyze") as acquired:
            assert acquired

    asyncio.run(run())
    assert locks.current(1) is None


def test_hold_when_busy_does_not_release_owner():
    locks = UserLocks()
    locks.try_acquire(1, "/sync")

    async def run():
        async with locks.hold(1, "опрос Garmin") as acquired:
            assert not acquired

    asyncio.run(run())
    assert locks.current(1) == "/sync"            # чужую блокировку не сняли


# ---------- middleware ----------

class FakeMessage:
    def __init__(self, chat_id=1):
        self.chat = SimpleNamespace(id=chat_id)
        self.answers = []

    async def answer(self, text, parse_mode=None):
        self.answers.append(text)


def data_with_flag(operation):
    return {"handler": SimpleNamespace(flags={"user_lock": operation} if operation else {})}


def test_middleware_runs_handler_and_blocks_parallel_call():
    locks = UserLocks()
    middleware = UserLockMiddleware(locks)
    started, finish = asyncio.Event(), asyncio.Event()
    calls = []

    async def slow_handler(event, data):
        calls.append("sync")
        started.set()
        await finish.wait()
        return "ok"

    async def run():
        first_msg, second_msg = FakeMessage(), FakeMessage()
        first = asyncio.create_task(middleware(slow_handler, first_msg, data_with_flag("/sync")))
        await started.wait()
        # пока первый /sync идёт, второй не запускает хендлер и получает «подождите»
        assert await middleware(slow_handler, second_msg, data_with_flag("/sync")) is None
        assert "ещё выполняется <b>/sync</b>" in second_msg.answers[0]
        finish.set()
        assert await first == "ok"

    asyncio.run(run())
    assert calls == ["sync"]
    assert locks.current(1) is None


def test_middleware_ignores_handlers_without_flag():
    locks = UserLocks()
    locks.try_acquire(1, "/sync")
    middleware = UserLockMiddleware(locks)

    async def handler(event, data):
        return "timezone"

    # лёгкие команды (например, /timezone) работают и во время тяжёлой операции
    assert asyncio.run(middleware(handler, FakeMessage(), data_with_flag(None))) == "timezone"


def test_flag_from_real_router_decorator_reaches_middleware():
    from aiogram import Router
    from aiogram.filters import Command

    router = Router()

    @router.message(Command("sync"), flags={"user_lock": "/sync"})
    async def handle(message):
        return "handled"

    handler_object = router.message.handlers[0]
    locks = UserLocks()
    locks.try_acquire(1, "/analyze")
    msg = FakeMessage()

    async def call(event, data):
        return "handled"

    result = asyncio.run(UserLockMiddleware(locks)(call, msg, {"handler": handler_object}))
    assert result is None and "ещё выполняется <b>/analyze</b>" in msg.answers[0]
