# services/user_locks.py
"""Не больше одной тяжёлой операции (Garmin, ИИ) на пользователя одновременно.

Реестр в памяти процесса: бот работает одним процессом (FSM тоже в MemoryStorage).
Операции не ждут друг друга: занятый пользователь получает ответ «подождите»,
фоновые задачи пропускают его до следующего круга. Проверка и захват идут без await,
поэтому в одном цикле событий они атомарны.
"""
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Optional


class UserLocks:
    def __init__(self) -> None:
        self._busy: Dict[int, str] = {}   # chat_id -> название операции, которая сейчас идёт

    def try_acquire(self, chat_id: int, operation: str) -> bool:
        if chat_id in self._busy:
            return False
        self._busy[chat_id] = operation
        return True

    def release(self, chat_id: int) -> None:
        self._busy.pop(chat_id, None)

    def current(self, chat_id: int) -> Optional[str]:
        """Какая операция сейчас идёт у пользователя (None, если свободен)."""
        return self._busy.get(chat_id)

    @asynccontextmanager
    async def hold(self, chat_id: int, operation: str) -> AsyncIterator[bool]:
        """async with locks.hold(chat_id, "опрос Garmin") as acquired: ... — освобождает при любом исходе."""
        acquired = self.try_acquire(chat_id, operation)
        try:
            yield acquired
        finally:
            if acquired:
                self.release(chat_id)
