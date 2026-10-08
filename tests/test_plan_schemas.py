import pytest
from pydantic import ValidationError

from schemas.plan import PHASE_NAMES, MacroPlan, WeekPlan, WorkoutType


def day(n, type_, distance=None, quality=None, **extra):
    d = {"day": n, "type": type_}
    if distance is not None:
        d["distance_km"] = distance
    if quality is not None:
        d["quality_km"] = quality
    d.update(extra)
    return d


def valid_week_days():
    return [
        day(1, "rest"),
        day(2, "threshold", 8, quality=3, description="3 км в темпе T"),
        day(3, "easy", 5),
        day(4, "easy", 6),
        day(5, "rest"),
        day(6, "long", 12),
        day(7, "easy", 5),
    ]


def valid_macro():
    return {
        "phases": [
            {"number": 1, "name": "Фундамент", "weeks": 3},
            {"number": 2, "name": "Раннее качество", "weeks": 4},
            {"number": 3, "name": "Пиковое качество", "weeks": 4},
            {"number": 4, "name": "Подводка", "weeks": 1},
        ],
        "weekly_km": [30, 32, 34, 36, 38, 40, 32, 42, 44, 46, 36, 25],
    }


# ---------- WeekPlan ----------

def test_valid_week_parses():
    week = WeekPlan.model_validate({"days": valid_week_days()})
    assert week.total_km == 36.0
    assert week.days[1].type == WorkoutType.THRESHOLD


def test_zone_is_derived_from_type():
    week = WeekPlan.model_validate({"days": valid_week_days()})
    assert week.days[0].zone is None      # отдых
    assert week.days[1].zone == "T"
    assert week.days[5].zone == "E"       # длительный


def test_unsorted_days_are_sorted():
    days = list(reversed(valid_week_days()))
    week = WeekPlan.model_validate({"days": days})
    assert [d.day for d in week.days] == [1, 2, 3, 4, 5, 6, 7]


def test_wrong_number_of_days_fails():
    with pytest.raises(ValidationError):
        WeekPlan.model_validate({"days": valid_week_days()[:6]})


def test_duplicate_days_fail():
    days = valid_week_days()
    days[6]["day"] = 6
    with pytest.raises(ValidationError):
        WeekPlan.model_validate({"days": days})


def test_running_day_without_distance_fails():
    days = valid_week_days()
    days[2] = day(3, "easy")
    with pytest.raises(ValidationError):
        WeekPlan.model_validate({"days": days})


def test_rest_day_with_distance_fails():
    days = valid_week_days()
    days[0] = day(1, "rest", 5)
    with pytest.raises(ValidationError):
        WeekPlan.model_validate({"days": days})


def test_quality_without_quality_km_fails():
    days = valid_week_days()
    days[1] = day(2, "threshold", 8)
    with pytest.raises(ValidationError):
        WeekPlan.model_validate({"days": days})


def test_quality_km_greater_than_distance_fails():
    days = valid_week_days()
    days[1] = day(2, "interval", 5, quality=6)
    with pytest.raises(ValidationError):
        WeekPlan.model_validate({"days": days})


def test_quality_km_on_easy_day_fails():
    days = valid_week_days()
    days[2] = day(3, "easy", 5, quality=2)
    with pytest.raises(ValidationError):
        WeekPlan.model_validate({"days": days})


def test_unknown_workout_type_fails():
    days = valid_week_days()
    days[2] = day(3, "sprint", 5)
    with pytest.raises(ValidationError):
        WeekPlan.model_validate({"days": days})


def test_extra_fields_are_ignored():
    days = valid_week_days()
    days[2]["pace"] = "5:30"      # модель не должна задавать темп, лишнее поле отбрасывается
    week = WeekPlan.model_validate({"days": days})
    assert not hasattr(week.days[2], "pace")


# ---------- MacroPlan ----------

def test_valid_macro_parses():
    macro = MacroPlan.model_validate(valid_macro())
    assert macro.total_weeks == 12


def test_phase_for_week():
    macro = MacroPlan.model_validate(valid_macro())
    assert macro.phase_for_week(1).number == 1
    assert macro.phase_for_week(3).number == 1
    assert macro.phase_for_week(4).number == 2
    assert macro.phase_for_week(12).number == 4
    assert macro.phase_for_week(99).number == 4   # за пределами плана - последняя фаза


def test_weekly_km_length_mismatch_fails():
    data = valid_macro()
    data["weekly_km"] = data["weekly_km"][:-1]
    with pytest.raises(ValidationError):
        MacroPlan.model_validate(data)


def test_phases_out_of_order_fail():
    data = valid_macro()
    data["phases"][0]["number"], data["phases"][1]["number"] = 2, 1
    with pytest.raises(ValidationError):
        MacroPlan.model_validate(data)


def test_non_positive_km_fails():
    data = valid_macro()
    data["weekly_km"][3] = 0
    with pytest.raises(ValidationError):
        MacroPlan.model_validate(data)


def test_zero_week_phase_is_allowed_for_short_plans():
    data = {
        "phases": [
            {"number": 1, "name": "Фундамент", "weeks": 0},
            {"number": 2, "name": "Раннее качество", "weeks": 0},
            {"number": 3, "name": "Пиковое качество", "weeks": 1},
            {"number": 4, "name": "Подводка", "weeks": 1},
        ],
        "weekly_km": [30, 20],
    }
    macro = MacroPlan.model_validate(data)
    assert macro.total_weeks == 2
    assert macro.phase_for_week(1).number == 3
    assert macro.week_phase_numbers() == [3, 4]


def test_phase_names_come_from_code():
    data = valid_macro()
    data["phases"][3]["name"] = "Тaper"          # латиница вперемешку с кириллицей, как у модели
    del data["phases"][0]["name"]                # название можно не передавать
    macro = MacroPlan.model_validate(data)
    assert [p.name for p in macro.phases] == [PHASE_NAMES[n] for n in (1, 2, 3, 4)]


def test_week_phase_numbers():
    macro = MacroPlan.model_validate(valid_macro())   # фазы по 3, 4, 4, 1 недели
    assert macro.week_phase_numbers() == [1] * 3 + [2] * 4 + [3] * 4 + [4]
