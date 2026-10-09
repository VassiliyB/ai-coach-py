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


# ---------- выгрузка тренировок в календарь ----------

class FakeApi:
    def __init__(self, fail_on_schedule=None, delete_error=None):
        self.uploaded, self.scheduled, self.deleted = [], [], []
        self.fail_on_schedule = fail_on_schedule
        self.delete_error = delete_error
        self._next_id = 100

    def upload_workout(self, workout):
        self._next_id += 1
        self.uploaded.append(workout)
        return {"workoutId": self._next_id}

    def schedule_workout(self, workout_id, day):
        if self.fail_on_schedule is not None and len(self.scheduled) == self.fail_on_schedule:
            raise GarminConnectConnectionError("500")
        self.scheduled.append((workout_id, day))

    def delete_workout(self, workout_id):
        if self.delete_error:
            raise self.delete_error
        self.deleted.append(workout_id)


def with_api(tmp_path, monkeypatch, api):
    client = client_with_tokens(tmp_path)
    monkeypatch.setattr(client, "_init_session_sync", lambda chat_id: api)
    return client


def test_schedule_uploads_and_replaces_old(tmp_path, monkeypatch):
    api = FakeApi()
    created = with_api(tmp_path, monkeypatch, api)._schedule_workouts_sync(
        1, [("2026-10-13", {"a": 1}), ("2026-10-15", {"b": 2})], [7, 8],
    )
    assert api.deleted == [7, 8]
    assert api.scheduled == [(101, "2026-10-13"), (102, "2026-10-15")]
    assert created == [{"date": "2026-10-13", "workout_id": 101}, {"date": "2026-10-15", "workout_id": 102}]


def test_schedule_rolls_back_on_failure(tmp_path, monkeypatch):
    api = FakeApi(fail_on_schedule=1)
    with pytest.raises(GarminClientError):
        with_api(tmp_path, monkeypatch, api)._schedule_workouts_sync(
            1, [("2026-10-13", {}), ("2026-10-15", {})], [],
        )
    assert api.deleted == [101, 102]      # при повторной выгрузке дублей не будет


def test_missing_old_workout_does_not_stop_export(tmp_path, monkeypatch):
    api = FakeApi(delete_error=GarminConnectConnectionError("404"))
    created = with_api(tmp_path, monkeypatch, api)._schedule_workouts_sync(1, [("2026-10-13", {})], [7])
    assert len(created) == 1


def test_rate_limit_on_delete_stops_export(tmp_path, monkeypatch):
    api = FakeApi(delete_error=GarminConnectTooManyRequestsError("429"))
    with pytest.raises(GarminRateLimitError):
        with_api(tmp_path, monkeypatch, api)._schedule_workouts_sync(1, [("2026-10-13", {})], [7])
    assert api.uploaded == []


# ---------- факт недели и восстановление ----------

class FactsApi:
    def __init__(self, hrv_error=None):
        self.hrv_error = hrv_error

    def get_activities_by_date(self, start, end, activitytype):
        assert activitytype == "running"
        return [{"activityId": 1, "activityType": {"typeKey": "running"}, "distance": 8000, "duration": 2640,
                 "startTimeLocal": "2026-10-13 07:00:00"}]

    def get_hrv_data(self, day):
        if self.hrv_error:
            raise self.hrv_error
        return {"hrvSummary": {"status": "LOW"}}

    def get_training_readiness(self, day):
        return [{"score": 45}]


def test_week_facts(tmp_path, monkeypatch):
    from datetime import date

    client = with_api(tmp_path, monkeypatch, FactsApi())
    facts = client._fetch_week_facts_sync(1, date(2026, 10, 12), date(2026, 10, 18))
    assert facts["runs"][0]["distance_km"] == 8.0
    assert facts["hrv_status"] == "LOW" and facts["readiness"] == 45


def test_week_facts_without_hrv_device(tmp_path, monkeypatch):
    from datetime import date

    api = FactsApi(hrv_error=GarminConnectConnectionError("404"))
    facts = with_api(tmp_path, monkeypatch, api)._fetch_week_facts_sync(1, date(2026, 10, 12), date(2026, 10, 18))
    assert facts["hrv_status"] is None and facts["readiness"] == 45


def test_week_facts_rate_limit_propagates(tmp_path, monkeypatch):
    from datetime import date

    api = FactsApi(hrv_error=GarminConnectTooManyRequestsError("429"))
    with pytest.raises(GarminRateLimitError):
        with_api(tmp_path, monkeypatch, api)._fetch_week_facts_sync(1, date(2026, 10, 12), date(2026, 10, 18))
