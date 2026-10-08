from schemas.plan import MacroPlan, WeekPlan
from services.plan_validator import format_problems, validate_macro, validate_week


def day(n, type_, distance=None, quality=None, **extra):
    d = {"day": n, "type": type_}
    if distance is not None:
        d["distance_km"] = distance
    if quality is not None:
        d["quality_km"] = quality
    d.update(extra)
    return d


def week(days):
    return WeekPlan.model_validate({"days": days})


def good_week():
    # 8 + 5 + 6 + 10 + 7 = 36 км; T: 3 км при лимите 3.6; длительный 10/36 = 28%
    return week([
        day(1, "rest"),
        day(2, "threshold", 8, quality=3),
        day(3, "easy", 5),
        day(4, "easy", 6),
        day(5, "rest"),
        day(6, "long", 10),
        day(7, "easy", 7),
    ])


def has(problems, text):
    return any(text in p for p in problems)


# ---------- validate_week ----------

def test_good_week_has_no_problems():
    assert validate_week(good_week()) == []


def test_two_quality_days_in_a_row():
    w = week([
        day(1, "rest"),
        day(2, "threshold", 8, quality=3),
        day(3, "interval", 8, quality=2),
        day(4, "easy", 5),
        day(5, "rest"),
        day(6, "long", 8),
        day(7, "easy", 5),
    ])
    assert has(validate_week(w), "подряд")


def test_long_run_right_after_quality_day():
    w = week([
        day(1, "rest"),
        day(2, "easy", 6),
        day(3, "easy", 6),
        day(4, "threshold", 8, quality=3),
        day(5, "long", 10),
        day(6, "easy", 5),
        day(7, "rest"),
    ])
    assert has(validate_week(w), "подряд")


def test_threshold_volume_over_limit():
    w = week([
        day(1, "rest"),
        day(2, "threshold", 8, quality=5),   # лимит 3.6 км
        day(3, "easy", 5),
        day(4, "easy", 6),
        day(5, "rest"),
        day(6, "long", 10),
        day(7, "easy", 7),
    ])
    assert has(validate_week(w), "лимит")


def test_long_run_share_too_high():
    w = week([
        day(1, "rest"),
        day(2, "easy", 5),
        day(3, "easy", 5),
        day(4, "easy", 5),
        day(5, "rest"),
        day(6, "long", 14),                  # 14 из 34 км = 41%
        day(7, "easy", 5),
    ])
    assert has(validate_week(w), "длительный бег")


def test_long_run_too_many_minutes():
    days = [
        day(1, "rest"),
        day(2, "threshold", 8, quality=3),
        day(3, "easy", 5),
        day(4, "easy", 6),
        day(5, "rest"),
        day(6, "long", 10, duration_min=160),
        day(7, "easy", 7),
    ]
    assert has(validate_week(week(days)), "мин")


def test_no_rest_day():
    w = week([
        day(1, "easy", 4),
        day(2, "threshold", 8, quality=3),
        day(3, "easy", 5),
        day(4, "easy", 5),
        day(5, "easy", 4),
        day(6, "long", 10),
        day(7, "easy", 4),
    ])
    assert has(validate_week(w), "отдых")


def test_too_many_quality_sessions():
    w = week([
        day(1, "threshold", 8, quality=3),
        day(2, "easy", 5),
        day(3, "interval", 8, quality=2),
        day(4, "easy", 5),
        day(5, "repetition", 6, quality=1),
        day(6, "easy", 5),
        day(7, "marathon", 8, quality=5),
    ])
    assert has(validate_week(w), "качественных")


def test_target_km_far_off():
    assert has(validate_week(good_week(), target_km=50), "планового")


def test_target_km_within_tolerance():
    assert validate_week(good_week(), target_km=38) == []


# ---------- validate_macro ----------

def macro_data():
    return {
        "phases": [
            {"number": 1, "name": "Фундамент", "weeks": 3},
            {"number": 2, "name": "Раннее качество", "weeks": 4},
            {"number": 3, "name": "Пиковое качество", "weeks": 4},
            {"number": 4, "name": "Подводка", "weeks": 1},
        ],
        "weekly_km": [30, 32, 34, 36, 38, 40, 32, 42, 44, 46, 36, 25],
    }


def test_good_macro_has_no_problems():
    macro = MacroPlan.model_validate(macro_data())
    assert validate_macro(macro, total_weeks=12) == []


def test_macro_total_weeks_mismatch():
    macro = MacroPlan.model_validate(macro_data())
    assert has(validate_macro(macro, total_weeks=10), "недель")


def test_macro_growth_too_fast():
    data = macro_data()
    data["weekly_km"][3] = 50                # 34 -> 50
    macro = MacroPlan.model_validate(data)
    assert has(validate_macro(macro, total_weeks=12), "неделя 4")


def test_macro_return_after_step_back_is_ok():
    # после разгрузочной недели (32) возврат к 42 считается от лучшей из двух предыдущих (40)
    macro = MacroPlan.model_validate(macro_data())
    assert not has(validate_macro(macro, total_weeks=12), "неделя 8")


def test_format_problems():
    assert format_problems(["а", "б"]) == "- а\n- б"