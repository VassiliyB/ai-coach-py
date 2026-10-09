import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

import clients.garmin.client as garmin_module
from clients.garmin import GarminAuthError, GarminClient, GarminClientError, GarminRateLimitError, GarminTokenStorage


def client_with_tokens(tmp_path):
    storage = GarminTokenStorage(base_dir=tmp_path)
    storage.get_user_dir(1)
    storage.get_token_file(1).write_text("{}", encoding="utf-8")
    return GarminClient(storage=storage)


def fake_garmin(error):
    class FakeGarmin:
        def login(self, tokenstore=None):
            raise error
    return FakeGarmin


@pytest.mark.parametrize("error,expected", [
    (GarminConnectAuthenticationError("токены недействительны"), GarminAuthError),     # сброс привязки
    (GarminConnectConnectionError("нет сети"), GarminClientError),                     # токены не трогаем
    (GarminConnectTooManyRequestsError("429"), GarminRateLimitError),
    (RuntimeError("что-то странное"), GarminClientError),                              # неизвестное не повод сбрасывать
])
def test_session_errors_are_classified(tmp_path, monkeypatch, error, expected):
    monkeypatch.setattr(garmin_module, "Garmin", fake_garmin(error))
    with pytest.raises(expected) as exc_info:
        client_with_tokens(tmp_path)._init_session_sync(1)
    # GarminAuthError наследует GarminClientError: проверяем точный тип, иначе тест прошёл бы и для сетевой ошибки
    assert type(exc_info.value) is expected


def test_missing_tokens_is_auth_error(tmp_path):
    with pytest.raises(GarminAuthError):
        GarminClient(storage=GarminTokenStorage(base_dir=tmp_path))._init_session_sync(1)
