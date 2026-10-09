# services/access_control.py
"""Закрытый доступ к боту: пользоваться им могут админы (ADMIN_CHAT_IDS) и одобренные ими пользователи.

Статус хранится в app_users.access, а здесь его копия в памяти процесса, чтобы не ходить в БД
на каждое сообщение: загружается при старте и меняется вместе с БД (одобрение, отзыв, /delete_me).
Как и UserLocks, реестр рассчитан на один процесс бота.

decide() решает, что делать с событием, без I/O: его выполняет AccessMiddleware.
"""
from enum import Enum
from typing import Iterable, Optional, Set

# Чем пользователь без доступа может распорядиться сам: удалить свои данные
DELETE_ME_COMMAND = "/delete_me"
DELETE_ME_CALLBACK_PREFIX = "delete_me:"

DENIED_TEXT = (
    "🔒 Бот работает по приглашению, доступ у вас пока не открыт.\n"
    "Отправьте /start, чтобы запросить доступ или узнать, что с запросом."
)


class AccessDecision(str, Enum):
    PASS = "pass"                  # доступ есть: событие идёт в хендлеры
    REQUEST = "request"            # /start без доступа: запрос админу или ответ о статусе запроса
    DENY_NOTIFY = "deny_notify"    # сообщение без доступа: один раз объяснить, как получить доступ
    DENY_SILENT = "deny_silent"    # уже объяснили или чат не личный: молча отбросить
    DENY_CALLBACK = "deny_callback"  # кнопка без доступа: всплывающее «доступ закрыт»


class AccessControl:
    def __init__(self, admin_ids: Iterable[int]) -> None:
        self.admin_ids: frozenset[int] = frozenset(admin_ids)
        self._approved: Set[int] = set()
        self._notified: Set[int] = set()    # кому уже ответили «доступ закрыт» (до перезапуска)

    def load(self, approved_ids: Iterable[int]) -> None:
        self._approved = set(approved_ids)

    def is_admin(self, chat_id: int) -> bool:
        return chat_id in self.admin_ids

    def is_allowed(self, chat_id: int) -> bool:
        return chat_id in self.admin_ids or chat_id in self._approved

    def approve(self, chat_id: int) -> None:
        self._approved.add(chat_id)
        self._notified.discard(chat_id)

    def revoke(self, chat_id: int) -> None:
        """Отзыв доступа, отклонение запроса или удаление пользователя (/delete_me)."""
        self._approved.discard(chat_id)

    def decide(
        self, chat_id: int, private_chat: bool,
        text: Optional[str] = None, callback_data: Optional[str] = None,
    ) -> AccessDecision:
        if not private_chat:
            return AccessDecision.DENY_SILENT   # бот работает только в личных чатах
        if self.is_allowed(chat_id):
            return AccessDecision.PASS
        if callback_data is not None:
            if callback_data.startswith(DELETE_ME_CALLBACK_PREFIX):
                return AccessDecision.PASS
            return AccessDecision.DENY_CALLBACK
        command = _command(text)
        if command == "/start":
            return AccessDecision.REQUEST
        if command == DELETE_ME_COMMAND:
            return AccessDecision.PASS
        if chat_id in self._notified:
            return AccessDecision.DENY_SILENT
        self._notified.add(chat_id)
        return AccessDecision.DENY_NOTIFY


def _command(text: Optional[str]) -> Optional[str]:
    """'/start@my_bot payload' -> '/start'; не команда -> None."""
    if not text or not text.startswith("/"):
        return None
    return text.split(maxsplit=1)[0].split("@", 1)[0].lower()
