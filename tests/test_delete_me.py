import asyncio

from bot.keyboards import DELETE_CANCEL, DELETE_CONFIRM, get_delete_confirm_keyboard
from clients.garmin import GarminClient, GarminTokenStorage


def test_clear_session_removes_tokens_and_pending_mfa(tmp_path):
    storage = GarminTokenStorage(base_dir=tmp_path)
    token_file = storage.get_token_file(42)
    storage.get_user_dir(42)
    token_file.write_text('{"token": "x"}', encoding="utf-8")
    other = storage.get_token_file(7)
    storage.get_user_dir(7)
    other.write_text('{"token": "y"}', encoding="utf-8")

    client = GarminClient(storage=storage)
    client._pending_auth[42] = ("garmin-client", "state")   # незавершённый вход по MFA
    assert client.has_saved_tokens(42)

    asyncio.run(client.clear_session(42))

    assert not client.has_saved_tokens(42)
    assert not (tmp_path / "42").exists()
    assert 42 not in client._pending_auth
    assert client.has_saved_tokens(7)          # чужие токены не тронуты


def test_clear_session_for_unknown_user_is_noop(tmp_path):
    client = GarminClient(storage=GarminTokenStorage(base_dir=tmp_path))
    asyncio.run(client.clear_session(999))   # не падает, если удалять нечего


def test_delete_keyboard_has_confirm_and_cancel():
    buttons = get_delete_confirm_keyboard().inline_keyboard[0]
    assert [b.callback_data for b in buttons] == [DELETE_CONFIRM, DELETE_CANCEL]
