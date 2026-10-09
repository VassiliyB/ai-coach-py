import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

from aiogram.types import CallbackQuery, Chat, Message, Update, User

from bot.keyboards import (
    ACCESS_APPROVE_PREFIX,
    ACCESS_REJECT_PREFIX,
    DELETE_CANCEL,
    DELETE_CONFIRM,
    get_access_request_keyboard,
)
from bot.middlewares import AccessMiddleware
from services.access_control import DELETE_ME_CALLBACK_PREFIX, DENIED_TEXT, AccessControl, AccessDecision

ADMIN, FRIEND, STRANGER = 1, 2, 3


def make_access() -> AccessControl:
    access = AccessControl(admin_ids=[ADMIN])
    access.load([FRIEND])
    return access


def test_admin_and_approved_pass():
    access = make_access()
    assert access.decide(ADMIN, True, text="/sync") == AccessDecision.PASS
    assert access.decide(FRIEND, True, text="привет") == AccessDecision.PASS
    assert access.decide(FRIEND, True, callback_data="dist_5km") == AccessDecision.PASS


def test_stranger_start_is_request():
    access = make_access()
    assert access.decide(STRANGER, True, text="/start") == AccessDecision.REQUEST
    assert access.decide(STRANGER, True, text="/start@coach_bot invite") == AccessDecision.REQUEST
    assert access.decide(STRANGER, True, text="/START") == AccessDecision.REQUEST


def test_stranger_is_told_once_then_ignored():
    access = make_access()
    assert access.decide(STRANGER, True, text="/sync") == AccessDecision.DENY_NOTIFY
    assert access.decide(STRANGER, True, text="ну пусти") == AccessDecision.DENY_SILENT
    assert access.decide(STRANGER, True, text=None) == AccessDecision.DENY_SILENT   # стикер, фото
    assert access.decide(STRANGER, True, text="/start") == AccessDecision.REQUEST   # запрос всегда доступен


def test_stranger_can_delete_own_data():
    access = make_access()
    assert access.decide(STRANGER, True, text="/delete_me") == AccessDecision.PASS
    assert access.decide(STRANGER, True, callback_data=DELETE_CONFIRM) == AccessDecision.PASS
    assert access.decide(STRANGER, True, callback_data=DELETE_CANCEL) == AccessDecision.PASS
    assert access.decide(STRANGER, True, callback_data="dist_5km") == AccessDecision.DENY_CALLBACK


def test_delete_buttons_match_allowed_prefix():
    assert DELETE_CONFIRM.startswith(DELETE_ME_CALLBACK_PREFIX)
    assert DELETE_CANCEL.startswith(DELETE_ME_CALLBACK_PREFIX)


def test_group_chats_are_ignored_even_for_admin():
    access = make_access()
    assert access.decide(ADMIN, False, text="/sync") == AccessDecision.DENY_SILENT
    assert access.decide(STRANGER, False, text="/start") == AccessDecision.DENY_SILENT


def test_approve_and_revoke():
    access = make_access()
    access.decide(STRANGER, True, text="/sync")          # уже получил «доступ закрыт»
    access.approve(STRANGER)
    assert access.is_allowed(STRANGER)
    access.revoke(STRANGER)
    assert not access.is_allowed(STRANGER)
    # После одобрения и отзыва объясняем заново
    assert access.decide(STRANGER, True, text="/sync") == AccessDecision.DENY_NOTIFY


def test_admin_cannot_be_revoked_in_registry():
    access = make_access()
    access.revoke(ADMIN)
    assert access.is_allowed(ADMIN)


def test_access_request_keyboard():
    approve, reject = get_access_request_keyboard(STRANGER).inline_keyboard[0]
    assert approve.callback_data == f"{ACCESS_APPROVE_PREFIX}{STRANGER}"
    assert reject.callback_data == f"{ACCESS_REJECT_PREFIX}{STRANGER}"
    assert len(approve.callback_data.encode()) <= 64     # лимит callback_data в Telegram


# ---------------- AccessMiddleware на настоящих объектах aiogram ----------------

def _chat(chat_id: int, chat_type: str = "private") -> Chat:
    return Chat(id=chat_id, type=chat_type)


def _message_update(chat_id: int, text: str, chat_type: str = "private") -> Update:
    message = Message(
        message_id=1, date=datetime.now(), chat=_chat(chat_id, chat_type),
        from_user=User(id=chat_id, is_bot=False, first_name="Тест"), text=text,
    )
    return Update(update_id=1, message=message)


def _callback_update(chat_id: int, data: str) -> Update:
    message = Message(message_id=1, date=datetime.now(), chat=_chat(chat_id), text="план")
    callback = CallbackQuery(
        id="cb", from_user=User(id=chat_id, is_bot=False, first_name="Тест"),
        chat_instance="x", message=message, data=data,
    )
    return Update(update_id=1, callback_query=callback)


def _run(middleware: AccessMiddleware, update: Update, chat: Chat) -> AsyncMock:
    handler = AsyncMock(return_value="handled")
    asyncio.run(middleware(handler, update, {"event_chat": chat, "bot": None}))
    return handler


def test_middleware_passes_approved_user():
    handler = _run(AccessMiddleware(make_access()), _message_update(FRIEND, "/sync"), _chat(FRIEND))
    handler.assert_awaited_once()


def test_middleware_stops_stranger_and_answers_once(monkeypatch):
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    middleware = AccessMiddleware(make_access())

    first = _run(middleware, _message_update(STRANGER, "/sync"), _chat(STRANGER))
    second = _run(middleware, _message_update(STRANGER, "/plan"), _chat(STRANGER))

    first.assert_not_awaited()
    second.assert_not_awaited()
    answer.assert_awaited_once_with(DENIED_TEXT)


def test_middleware_answers_stranger_button_with_alert(monkeypatch):
    answer = AsyncMock()
    monkeypatch.setattr(CallbackQuery, "answer", answer)
    handler = _run(AccessMiddleware(make_access()), _callback_update(STRANGER, "dist_5km"), _chat(STRANGER))
    handler.assert_not_awaited()
    assert answer.await_args.kwargs["show_alert"] is True


def test_middleware_ignores_groups_silently(monkeypatch):
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    handler = _run(
        AccessMiddleware(make_access()), _message_update(-100, "/sync", "group"), _chat(-100, "group"),
    )
    handler.assert_not_awaited()
    answer.assert_not_awaited()
