import math

from schemas.plan import MacroPlan, WeekPlan
from services.coach_service import calculate_zones
from services.plan_validator import (
    LONG_MAX_MINUTES,
    format_problems,
    long_run_max_km,
    validate_intro_days,
    validate_macro,
    validate_week,
)


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


def long_week(long_km):
    # лёгкие дни подобраны так, чтобы длительный укладывался в 30% недели и был самым длинным
    easy_km = math.ceil(long_km * 0.6)
    return week([
        day(1, "rest"),
        day(2, "easy", easy_km),
        day(3, "easy", easy_km),
        day(4, "easy", easy_km),
        day(5, "rest"),
        day(6, "long", long_km),
        day(7, "easy", easy_km),
    ])


def test_long_run_over_time_cap_for_slow_runner():
    zones = calculate_zones(30)
    cap = long_run_max_km(zones)            # 150 мин в лёгком темпе медленного бегуна
    problems = validate_week(long_week(math.ceil(cap) + 1), zones=zones)
    assert has(problems, f"дольше потолка {LONG_MAX_MINUTES} мин")


def test_long_run_within_time_cap():
    zones = calculate_zones(30)
    assert validate_week(long_week(math.floor(long_run_max_km(zones))), zones=zones) == []


def test_long_run_time_cap_needs_zones():
    # без VDOT темп неизвестен: потолок по времени не проверяется
    assert validate_week(long_week(30)) == []


def test_faster_runner_has_longer_cap():
    assert long_run_max_km(calculate_zones(55)) > long_run_max_km(calculate_zones(35))


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


def test_macro_taper_too_small():
    data = macro_data()
    data["weekly_km"][-1] = 40               # пик 46, допустимо не более 34.5
    macro = MacroPlan.model_validate(data)
    assert has(validate_macro(macro, total_weeks=12), "последняя неделя")


def test_macro_taper_on_boundary_is_ok():
    data = macro_data()
    data["weekly_km"][-1] = 34.5             # ровно 75% от пика 46
    macro = MacroPlan.model_validate(data)
    assert validate_macro(macro, total_weeks=12) == []


def macro_with(weeks, km):
    data = macro_data()
    for phase, n in zip(data["phases"], weeks, strict=True):
        phase["weeks"] = n
    data["weekly_km"] = km
    return MacroPlan.model_validate(data)


def test_macro_peak_in_base_phase_and_volume_collapse():
    # живой прогон: пик на 2-й неделе, затем объём проваливается ниже текущего уровня атлета
    macro = macro_with([3, 3, 3, 3], [38, 41, 32, 24, 35, 28, 22, 30, 24, 19, 18, 14])
    problems = validate_macro(macro, total_weeks=12)
    assert has(problems, "пик километража 41 км приходится на неделю 2 (фаза 1)")
    assert has(problems, "неделя 4: 24 км, ниже 70%")
    assert has(problems, "неделя 7: 22 км")
    assert not has(problems, "неделя 10")      # фаза подводки: снижение разрешено


def test_macro_peak_in_taper_phase():
    macro = macro_with([3, 3, 3, 3], [30, 32, 26, 33, 35, 28, 36, 38, 30, 40, 36, 28])
    assert has(validate_macro(macro, total_weeks=12), "приходится на неделю 10 (фаза 4)")


def test_macro_peak_tie_with_base_phase_is_ok():
    # атлет держит высокий текущий объём: пик в фазе I не выше пика фаз II-III
    macro = macro_with([3, 4, 4, 1], [40, 40, 32, 36, 39, 40, 32, 40, 40, 32, 30, 28])
    assert validate_macro(macro, total_weeks=12) == []


def test_macro_low_volume_allowed_in_taper():
    data = macro_data()
    data["weekly_km"][-1] = 20               # ниже 70% первой недели (21), но это фаза IV
    macro = MacroPlan.model_validate(data)
    assert validate_macro(macro, total_weeks=12) == []


def test_macro_volume_grows_inside_taper():
    # живой прогон: фаза IV 38 -> 40 -> 30, подводка фактически только в последнюю неделю
    macro = macro_with([2, 4, 3, 3], [38, 40, 32, 42, 46, 37, 40, 44, 35, 38, 40, 30])
    problems = validate_macro(macro, total_weeks=12)
    assert has(problems, "неделя 11: 40 км больше предыдущей (38 км) внутри фазы подводки")
    assert len(problems) == 1


def test_macro_taper_entry_after_deload_is_ok():
    # первая неделя подводки (38) выше разгрузочной недели фазы III (35): переход не проверяется
    macro = macro_with([2, 4, 3, 3], [38, 40, 32, 42, 46, 37, 40, 44, 35, 38, 34, 30])
    assert validate_macro(macro, total_weeks=12) == []


def test_macro_flat_taper_is_ok():
    macro = macro_with([3, 3, 3, 3], [30, 32, 26, 33, 35, 28, 36, 38, 30, 28, 28, 25])
    assert validate_macro(macro, total_weeks=12) == []


def test_macro_short_plan_without_peak_phases():
    macro = macro_with([1, 0, 0, 1], [30, 20])
    assert validate_macro(macro, total_weeks=2) == []


# ---------- длительный бег и правила фазы ----------

def base_week():
    # 5 + 6 + 5 + 9 + 5 = 30 км, только лёгкий бег и длительный: подходит для фазы I
    return week([
        day(1, "rest"),
        day(2, "easy", 5, description="Лёгкий бег + 4 ускорения по 20 с"),
        day(3, "easy", 6),
        day(4, "cross"),
        day(5, "easy", 5),
        day(6, "long", 9),
        day(7, "easy", 5),
    ])


def test_long_run_shorter_than_other_day():
    w = week([
        day(1, "rest"),
        day(2, "easy", 8),
        day(3, "easy", 5),
        day(4, "easy", 6),
        day(5, "rest"),
        day(6, "long", 7),
        day(7, "easy", 5),
    ])
    assert has(validate_week(w), "длительный бег 7 км короче")


def test_long_run_equal_to_other_day_is_ok():
    w = week([
        day(1, "rest"),
        day(2, "threshold", 8, quality=3),
        day(3, "easy", 5),
        day(4, "easy", 6),
        day(5, "rest"),
        day(6, "long", 8),
        day(7, "easy", 5),
    ])
    assert not has(validate_week(w), "короче")


def test_phase_one_forbids_quality():
    problems = validate_week(good_week(), phase_number=1)
    assert has(problems, "недопустима в фазе 1")


def test_phase_one_allows_easy_long_cross():
    assert validate_week(base_week(), phase_number=1) == []


def test_quality_allowed_in_later_phases_and_without_phase():
    assert validate_week(good_week(), phase_number=2) == []
    assert validate_week(good_week()) == []


def test_format_problems():
    assert format_problems(["а", "б"]) == "- а\n- б"


# ---------- вводные дни до старта плана ----------

def intro_week(days_by_number, active):
    """Неделя, где дни вне active — отдых, а active заполнены по days_by_number."""
    return week([days_by_number.get(n, day(n, "rest")) for n in range(1, 8)])


INTRO_ACTIVE = {4, 5, 6, 7}   # план составлен в среду: четверг–воскресенье


def good_intro():
    # 20 км за 4 дня при обычных 37.8 км в неделю: цель 37.8 * 4 / 7 = 21.6, допуск ±15%
    days = {4: day(4, "easy", 5), 5: day(5, "easy", 6), 6: day(6, "long", 9), 7: day(7, "rest")}
    return intro_week(days, INTRO_ACTIVE)


def test_good_intro_days():
    assert validate_intro_days(good_intro(), INTRO_ACTIVE, target_km=21.6, weekly_km=37.8) == []


def test_intro_past_day_must_be_rest():
    days = {2: day(2, "easy", 4), 4: day(4, "easy", 5), 5: day(5, "easy", 5), 6: day(6, "long", 8)}
    w = intro_week(days, INTRO_ACTIVE)
    assert has(validate_intro_days(w, INTRO_ACTIVE, target_km=21.6, weekly_km=37.8), "Вт: этот день уже прошёл")


def test_intro_forbids_quality():
    w = intro_week({4: day(4, "threshold", 7, quality=2), 5: day(5, "easy", 5), 6: day(6, "long", 9)}, INTRO_ACTIVE)
    assert has(validate_intro_days(w, INTRO_ACTIVE, target_km=21.6, weekly_km=37.8), "недопустима во вводные дни")


def test_intro_long_run_capped_by_usual_weekly_volume():
    # 12 км из 21: 57% вводных дней, но лимит считается от обычной недели 37.8 км -> 11.3 км
    w = intro_week({4: day(4, "easy", 5), 5: day(5, "easy", 4), 6: day(6, "long", 12)}, INTRO_ACTIVE)
    problems = validate_intro_days(w, INTRO_ACTIVE, target_km=21.6, weekly_km=37.8)
    assert has(problems, "длительный бег 12 км больше 30% обычного недельного объёма")
    w_ok = intro_week({4: day(4, "easy", 6), 5: day(5, "easy", 5), 6: day(6, "long", 11)}, INTRO_ACTIVE)
    assert validate_intro_days(w_ok, INTRO_ACTIVE, target_km=21.6, weekly_km=37.8) == []


def test_intro_volume_far_off():
    assert has(validate_intro_days(good_intro(), INTRO_ACTIVE, target_km=30, weekly_km=50), "объём вводных дней")


def test_intro_long_week_needs_rest_day():
    active = {2, 3, 4, 5, 6, 7}   # план составлен в понедельник: 6 вводных дней
    w = intro_week({n: day(n, "easy", 5) for n in range(2, 7)} | {7: day(7, "long", 6)}, active)
    assert has(validate_intro_days(w, active, target_km=31, weekly_km=36), "нет ни одного дня отдыха")


def test_intro_without_volume_skips_target_check():
    assert validate_intro_days(good_intro(), INTRO_ACTIVE, target_km=0, weekly_km=37.8) == []
