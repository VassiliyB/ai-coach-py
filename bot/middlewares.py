# bot/middlewares.py
"""Middleware бота: доступ по одобрению админа и блокировка тяжёлых команд (флаг user_lock)."""
import html
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.dispatcher.flags import get_flag
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, TelegramObject, Update

from services.access_control import DENIED_TEXT, AccessControl, AccessDecision
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


class AccessMiddleware(BaseMiddleware):
    """Внешний middleware на dp.update: без доступа событие не доходит ни до одного роутера.

    Решение принимает AccessControl.decide (без I/O), здесь только ответы пользователю.
    """

    def __init__(self, access: AccessControl) -> None:
        self.access = access

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)
        chat = data.get("event_chat")
        if chat is None:
            return None   # обновления без чата (инлайн-запросы) бот не обрабатывает
        private = chat.type == ChatType.PRIVATE

        if event.message is not None:
            decision = self.access.decide(chat.id, private, text=event.message.text)
        elif event.callback_query is not None:
            decision = self.access.decide(chat.id, private, callback_data=event.callback_query.data or "")
        else:
            # Прочие обновления (правка сообщения, блокировка бота): только для допущенных, без ответов
            allowed = private and self.access.is_allowed(chat.id)
            return await handler(event, data) if allowed else None

        if decision == AccessDecision.PASS:
            return await handler(event, data)
        if decision == AccessDecision.REQUEST:
            from bot.handlers.access import handle_access_request  # хендлеры тянут БД, middleware тесты импортируют
            await handle_access_request(event.message, data["bot"], self.access)
        elif decision == AccessDecision.DENY_NOTIFY:
            await event.message.answer(DENIED_TEXT)
        elif decision == AccessDecision.DENY_CALLBACK:
            await event.callback_query.answer("🔒 Доступ к боту закрыт.", show_alert=True)
        return None
