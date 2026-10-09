from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from services.coach_service import MARATHON_M, calculate_vdot
from services.race_goal import VDOT_ESTIMATE_MARGIN
from services.race_result import (
    HALF_MARATHON_M,
    RACE_VALID_DAYS,
    RaceResult,
    active_race,
    check_race,
    describe_race,
    format_distance,
    goal_margin,
    parse_distance,
    parse_race,
    parse_race_date,
    race_from_profile,
    render_race_saved,
    render_race_status,
)

TODAY = date(2026, 10, 9)


@pytest.mark.parametrize("text, expected", [
    ("10к", 10000), ("10 км", 10000), ("10km", 10000), ("5К", 5000), ("10", 10000),
    ("3000 м", 3000), ("1500м", 1500), ("5000", 5000), ("3,2 км", 3200),
    ("21.1", HALF_MARATHON_M), ("21 км", HALF_MARATHON_M), ("21.0975", HALF_MARATHON_M),
    ("полумарафон", HALF_MARATHON_M), ("ПМ", HALF_MARATHON_M),
    ("42.2 км", MARATHON_M), ("42", MARATHON_M), ("марафон", MARATHON_M),
])
def test_parse_distance(text, expected):
    assert parse_distance(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", "800 м", "1 км", "50 км", "100", "сто", "10 миль"])
def test_parse_distance_rejects(text):
    assert parse_distance(text) is None


@pytest.mark.parametrize("text, expected", [
    ("14.09", date(2026, 9, 14)),
    ("9.10", date(2026, 10, 9)),            # сегодня
    ("20.12", date(2025, 12, 20)),          # в этом году такой даты ещё не было: прошлый год
    ("14.09.26", date(2026, 9, 14)),
    ("14.09.2026", date(2026, 9, 14)),
])
def test_parse_race_date(text, expected):
    assert parse_race_date(text, TODAY) == expected


@pytest.mark.parametrize("text", ["31.02", "45.30", "14/09", "1:45"])
def test_parse_race_date_rejects(text):
    assert parse_race_date(text, TODAY) is None


@pytest.mark.parametrize("text, distance, time_s, race_date", [
    ("10к 45:30", 10000, 45 * 60 + 30, TODAY),
    ("10 км 45:30", 10000, 45 * 60 + 30, TODAY),
    ("10к 45:30 14.09", 10000, 45 * 60 + 30, date(2026, 9, 14)),
    ("полумарафон 1:41:20 14.09.2026", HALF_MARATHON_M, 3600 + 41 * 60 + 20, date(2026, 9, 14)),
    ("21.1 1:41", HALF_MARATHON_M, 3600 + 41 * 60, TODAY),            # как мм:сс невозможно -> ч:мм
    ("марафон 3 ч 30 мин", MARATHON_M, 3 * 3600 + 30 * 60, TODAY),
    ("5 км 4:00/км", 5000, 20 * 60, TODAY),                          # темп
    ("3000 м 12:05", 3000, 12 * 60 + 5, TODAY),
])
def test_parse_race(text, distance, time_s, race_date):
    race = parse_race(text, TODAY)
    assert race is not None
    assert race.distance_m == pytest.approx(distance)
    assert (race.time_s, race.race_date) == (time_s, race_date)


@pytest.mark.parametrize("text", ["", "10к", "45:30", "10к 3:00", "10к 45:30 вчера", "1 км 3:30"])
def test_parse_race_rejects(text):
    assert parse_race(text, TODAY) is None


def test_race_vdot_matches_daniels_formula():
    race = RaceResult(10000, 45 * 60 + 30, TODAY)
    assert race.vdot == round(calculate_vdot(10000, 2730), 1)
    assert 44 < race.vdot < 46


def test_check_race_accepts_fresh_race():
    assert check_race(RaceResult(10000, 2730, date(2026, 9, 14)), TODAY, None) is None
    edge = TODAY - timedelta(days=RACE_VALID_DAYS)   # ровно на границе свежести ещё принимается
    assert check_race(RaceResult(10000, 2730, edge), TODAY, None) is None


def test_check_race_rejects_future_and_old():
    assert "будущем" in check_race(RaceResult(10000, 2730, TODAY + timedelta(days=1)), TODAY, None)
    old = TODAY - timedelta(days=RACE_VALID_DAYS + 1)
    assert "старше" in check_race(RaceResult(10000, 2730, old), TODAY, None)


def test_check_race_rejects_race_before_later_review():
    # После забега был пересмотр формы (например, снижение после перерыва): та оценка свежее
    race = RaceResult(10000, 2730, date(2026, 9, 14))
    assert "позже" in check_race(race, TODAY, date(2026, 10, 1))
    # Забег в день пересмотра или после него принимается
    assert check_race(race, TODAY, date(2026, 9, 14)) is None
    assert check_race(race, TODAY, date(2026, 9, 1)) is None


def test_check_race_allows_correcting_saved_race_date():
    # Сохранённый забег записал свою дату как дату оценки формы: её можно исправить на более раннюю
    saved = RaceResult(10000, 2730, date(2026, 10, 1))
    fixed = RaceResult(10000, 2730, date(2026, 9, 28))
    assert check_race(fixed, TODAY, date(2026, 10, 1), saved) is None


def test_check_race_rejects_vdot_out_of_range():
    assert "VDOT" in check_race(RaceResult(5000, 12 * 60, TODAY), TODAY, None)   # 2:24 /км на 5 км


def _profile(race_date=date(2026, 9, 14)):
    return SimpleNamespace(
        race_result_distance_m=10000.0, race_result_time_s=2730, race_result_date=race_date, vdot=45.3,
    )


def test_race_from_profile():
    assert race_from_profile(None) is None
    assert race_from_profile(SimpleNamespace(vdot=40.0)) is None
    assert race_from_profile(_profile()) == RaceResult(10000.0, 2730, date(2026, 9, 14))


def test_active_race_and_goal_margin_expire():
    fresh, stale = _profile(), _profile(TODAY - timedelta(days=RACE_VALID_DAYS + 1))
    assert active_race(fresh, TODAY) is not None
    assert active_race(stale, TODAY) is None
    assert goal_margin(fresh, TODAY) == 0.0
    assert goal_margin(stale, TODAY) == VDOT_ESTIMATE_MARGIN
    assert goal_margin(None, TODAY) == VDOT_ESTIMATE_MARGIN


def test_format_and_describe():
    assert format_distance(MARATHON_M) == "марафон"
    assert format_distance(HALF_MARATHON_M) == "полумарафон"
    assert format_distance(1500) == "1500 м"
    assert format_distance(10000) == "10 км"
    assert describe_race(RaceResult(10000, 2730, date(2026, 9, 14))) == "10 км за 45:30 (4:33 /км), 14.09.2026"


def test_render_race_saved():
    race = RaceResult(10000, 2730, date(2026, 9, 14))
    text = render_race_saved(race, 42.0, "ЗОНЫ", "ЦЕЛЬ")
    assert f"42 → {race.vdot:g}" in text
    assert "ЗОНЫ" in text and "ЦЕЛЬ" in text
    assert f"до {race.valid_until():%d.%m}" in text
    assert f"VDOT: <b>{race.vdot:g}</b>" in render_race_saved(race, None, "ЗОНЫ", None)


def test_render_race_status():
    race = RaceResult(10000, 2730, date(2026, 9, 14))
    assert "по забегу" in render_race_status(race, race.vdot, TODAY)
    assert "После пересмотра" in render_race_status(race, 43.0, TODAY)
    assert "по тренировкам" in render_race_status(None, 42.0, TODAY)
    assert "не рассчитан" in render_race_status(None, None, TODAY)
    # Устаревший забег уже не источник темпов
    stale = RaceResult(10000, 2730, TODAY - timedelta(days=RACE_VALID_DAYS + 1))
    assert "по тренировкам" in render_race_status(stale, 42.0, TODAY)
