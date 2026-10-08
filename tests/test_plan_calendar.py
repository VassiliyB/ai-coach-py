from datetime import date

import pytest

from services.plan_calendar import monday_of, next_week_dates, plan_total_weeks, plan_week_number

# План из живой проверки: создан в четверг 08.10.2026, забег в воскресенье 03.01.2027, 12 недель
RACE = date(2027, 1, 3)


def test_monday_of():
    assert monday_of(date(2026, 10, 8)) == date(2026, 10, 5)     # четверг
    assert monday_of(date(2026, 10, 12)) == date(2026, 10, 12)   # понедельник


@pytest.mark.parametrize("today,expected", [
    (date(2026, 10, 8), (date(2026, 10, 12), date(2026, 10, 18))),   # четверг
    (date(2026, 10, 11), (date(2026, 10, 12), date(2026, 10, 18))),  # воскресенье: рассылка на завтра
    (date(2026, 10, 12), (date(2026, 10, 19), date(2026, 10, 25))),  # понедельник
])
def test_next_week_dates(today, expected):
    assert next_week_dates(today) == expected


def test_first_week_is_next_monday_after_creation():
    assert plan_week_number(RACE, 12, date(2026, 10, 12)) == 1


def test_race_week_is_last():
    assert plan_week_number(RACE, 12, date(2026, 12, 28)) == 12
    assert plan_week_number(RACE, 12, date(2026, 12, 21)) == 11


def test_week_before_plan_start_counts_as_first():
    assert plan_week_number(RACE, 12, date(2026, 10, 5)) == 1


def test_after_race_week_plan_is_finished():
    assert plan_week_number(RACE, 12, date(2027, 1, 4)) is None


def test_race_midweek_belongs_to_its_week():
    # забег в среду 30.12.2026: неделя 28.12–03.01 всё равно последняя
    assert plan_week_number(date(2026, 12, 30), 12, date(2026, 12, 28)) == 12
    assert plan_week_number(date(2026, 12, 30), 12, date(2027, 1, 4)) is None


@pytest.mark.parametrize("today,race,expected", [
    (date(2026, 10, 8), date(2027, 1, 3), 12),    # четверг -> воскресенье: как days_left // 7
    (date(2026, 10, 8), date(2027, 1, 4), 13),    # забег в понедельник: days_left // 7 дал бы 12
    (date(2026, 10, 12), date(2027, 1, 3), 11),   # создан в понедельник: текущая неделя в план не входит
    (date(2026, 10, 11), date(2026, 10, 25), 2),  # минимальный план: 2 недели
])
def test_plan_total_weeks(today, race, expected):
    assert plan_total_weeks(today, race) == expected


def test_total_weeks_consistent_with_week_numbers():
    today, race = date(2026, 10, 8), date(2027, 1, 4)
    total = plan_total_weeks(today, race)
    first_monday, _ = next_week_dates(today)
    assert plan_week_number(race, total, first_monday) == 1
    assert plan_week_number(race, total, monday_of(race)) == total
