# bot/middlewares.py
"""Блокировка тяжёлых команд: хендлер с флагом user_lock не запускается, пока у пользователя идёт другая."""
import html
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.dispatcher.flags import get_flag
from aiogram.types import CallbackQuery, TelegramObject

from services.user_locks import UserLocks

USER_LOCK_FLAG = "user_lock"   # значение флага: название операции для сообщения пользователю


class UserLockMiddleware(BaseMiddleware):
    """Внутренний middleware для dp.message и dp.callback_query: флаги хендлера доступны только после его выбора."""

    def __init__(self, locks: UserLocks) -> None:
        self.locks = locks

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        operation = get_flag(data, USER_LOCK_FLAG)
        chat = getattr(event, "chat", None)
        if chat is None and isinstance(event, CallbackQuery) and event.message is not None:
            chat = event.message.chat
        if not operation or chat is None:
            return await handler(event, data)

        async with self.locks.hold(chat.id, operation) as acquired:
            if not acquired:
                running = self.locks.current(chat.id) or "предыдущая команда"
                if isinstance(event, CallbackQuery):  # всплывающее окно, HTML там не поддерживается
                    await event.answer(f"⏳ Подождите: ещё выполняется {running}.", show_alert=True)
                    return None
                running = html.escape(running)
                await event.answer(
                    f"⏳ Подождите: ещё выполняется <b>{running}</b>. Повторите команду, когда придёт результат.",
                    parse_mode="HTML",
                )
                return None
            return await handler(event, data)
