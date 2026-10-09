from datetime import date

import pytest

from schemas.plan import PlannedDay, WeekPlan
from services.coach_service import calculate_zones
from services.garmin_workouts import (
    MIN_STEP_KM,
    PACE_TOLERANCE_SEC,
    build_workout,
    export_days,
    pace_target_mps,
    parse_repeats,
    recovery_seconds,
    split_previous,
)
from services.heart_rate import KIND_LTHR, HeartRateBasis
from services.plan_paces import hr_range_for_zone

ZONES = calculate_zones(50)
MAX_HR = 190


def steps(workout):
    return workout["workoutSegments"][0]["workoutSteps"]


def total_m(step_list):
    """Дистанция шагов по дистанции; восстановление по времени не входит."""
    total = 0.0
    for st in step_list:
        if st["type"] == "RepeatGroupDTO":
            total += st["numberOfIterations"] * total_m(st["workoutSteps"])
        elif st["endCondition"]["conditionTypeKey"] == "distance":
            total += st["endConditionValue"]
    return total


# ---------- повторы из описания ----------

@pytest.mark.parametrize("text,quality,expected", [
    ("5 × 1000 м, отдых 2 мин", 5, (5, 1000, 120)),
    ("6x400m трусцой 90 с", 2.4, (6, 400, 90)),
    ("3 х 1,6 км", 4.8, (3, 1600, None)),            # кириллическая «х» и запятая
    ("2 × 3 км, пауза 1 мин", 6, (2, 3000, 60)),
])
def test_parse_repeats(text, quality, expected):
    rep = parse_repeats(text, quality)
    assert (rep.count, rep.distance_m, rep.recovery_sec) == expected


@pytest.mark.parametrize("text,quality", [
    ("Темповый бег 20 минут", 5),
    ("5 × 1000 м", 8),        # объём не сходится с рабочей частью: модели не верим
    ("1 × 5000 м", 5),        # один отрезок это не повторы
    ("5 × 1000 м", None),
])
def test_parse_repeats_rejects(text, quality):
    assert parse_repeats(text, quality) is None


def test_recovery_follows_daniels_rules():
    rep_sec = ZONES.interval.fast            # 1000 м в темпе I
    assert recovery_seconds("I", 1000, ZONES) == pytest.approx(rep_sec, abs=8)
    assert recovery_seconds("T", 1600, ZONES) == 75          # 1 мин на 5 мин работы (6.7 мин -> 80 с -> 75)
    assert recovery_seconds("T", 1000, ZONES) == 60          # но не меньше минуты
    assert recovery_seconds("R", 200, ZONES) >= recovery_seconds("I", 200, ZONES)
    assert recovery_seconds("I", 1000, None) == 60           # без зон нижняя граница
    assert recovery_seconds("I", 1000, ZONES) % 15 == 0


# ---------- целевой темп ----------

def test_single_pace_gets_tolerance_corridor():
    low, high = pace_target_mps(ZONES.threshold)
    assert low < high                                         # нижняя граница медленнее
    assert 1000 / low - 1000 / high == pytest.approx(2 * PACE_TOLERANCE_SEC, abs=0.1)


def test_easy_range_kept_as_is():
    low, high = pace_target_mps(ZONES.easy)
    assert 1000 / low == pytest.approx(ZONES.easy.slow, abs=0.1)
    assert 1000 / high == pytest.approx(ZONES.easy.fast, abs=0.1)


# ---------- сборка тренировки ----------

def test_easy_run_is_one_distance_step_with_pace():
    w = build_workout(PlannedDay(day=1, type="easy", distance_km=8), ZONES, MAX_HR)
    (step,) = steps(w)
    assert step["endConditionValue"] == 8000
    assert step["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    assert w["sportType"]["sportTypeKey"] == "running"
    assert w["workoutName"].startswith("AI Coach")
    assert w["estimatedDurationInSecs"] > 0


def test_without_zones_target_is_heart_rate():
    w = build_workout(PlannedDay(day=1, type="easy", distance_km=8), None, MAX_HR)
    (step,) = steps(w)
    assert step["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert (step["targetValueOne"], step["targetValueTwo"]) == hr_range_for_zone("E", MAX_HR)
    assert w["estimatedDurationInSecs"] == 0


def test_without_zones_target_from_lthr():
    lthr = HeartRateBasis(170, KIND_LTHR, manual=True)
    (step,) = steps(build_workout(PlannedDay(day=1, type="easy", distance_km=8), None, lthr))
    assert (step["targetValueOne"], step["targetValueTwo"]) == (128, 151)


def test_without_zones_and_hr_no_target():
    (step,) = steps(build_workout(PlannedDay(day=1, type="easy", distance_km=8), None, None))
    assert step["targetType"]["workoutTargetTypeKey"] == "no.target"


def test_threshold_continuous_warmup_work_cooldown():
    day = PlannedDay(day=2, type="threshold", distance_km=10, quality_km=5, description="Темповый бег")
    warm, work, cool = steps(build_workout(day, ZONES, MAX_HR))
    assert warm["stepType"]["stepTypeKey"] == "warmup" and cool["stepType"]["stepTypeKey"] == "cooldown"
    assert warm["endConditionValue"] == cool["endConditionValue"] == 2500
    assert work["endConditionValue"] == 5000
    assert work["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    assert [s["stepOrder"] for s in (warm, work, cool)] == [1, 2, 3]


def test_intervals_become_repeat_group_and_distance_fits():
    day = PlannedDay(day=4, type="interval", distance_km=10, quality_km=5, description="5 × 1000 м, отдых 3 мин")
    w_steps = steps(build_workout(day, ZONES, MAX_HR))
    group = w_steps[1]
    assert group["type"] == "RepeatGroupDTO" and group["numberOfIterations"] == 5
    rep, rest = group["workoutSteps"]
    assert rep["endConditionValue"] == 1000 and rest["endConditionValue"] == 180
    # разминка + повторы + заминка + трусца на восстановлении ≈ дистанция дня
    recovery_m = 5 * 180 / ZONES.easy.slow * 1000
    assert total_m(w_steps) + recovery_m == pytest.approx(10000, abs=150)
    orders = [w_steps[0]["stepOrder"], group["stepOrder"], rep["stepOrder"], rest["stepOrder"], w_steps[2]["stepOrder"]]
    assert len(set(orders)) == len(orders)


def test_catalog_day_built_from_template():
    # описание дня не разбирается: структура из каталога (5 × 3 мин И по времени, 2 мин трусцой)
    day = PlannedDay(day=4, type="interval", distance_km=10, quality_km=3.5, workout_id="I-3min", reps=5,
                     description="что угодно")
    warm, group, cool = steps(build_workout(day, ZONES, MAX_HR))
    assert warm["stepType"]["stepTypeKey"] == "warmup" and cool["stepType"]["stepTypeKey"] == "cooldown"
    assert group["numberOfIterations"] == 5
    rep, rest = group["workoutSteps"]
    assert rep["endCondition"]["conditionTypeKey"] == "time" and rep["endConditionValue"] == 180
    assert rep["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    assert rest["stepType"]["stepTypeKey"] == "recovery" and rest["endConditionValue"] == 120


def test_catalog_repetitions_with_distance_recovery_and_hills_without_target():
    day = PlannedDay(day=2, type="repetition", distance_km=8, workout_id="R-400", reps=5)
    _, group, _ = steps(build_workout(day, ZONES, MAX_HR))
    rep, rest = group["workoutSteps"]
    assert rest["endCondition"]["conditionTypeKey"] == "distance" and rest["endConditionValue"] == 400
    # разминка + повторы + трусца по 400 м + заминка = дистанция дня
    assert total_m(steps(build_workout(day, ZONES, MAX_HR))) == pytest.approx(8000, abs=10)

    hills = PlannedDay(day=2, type="repetition", distance_km=9, workout_id="R-hills-60s", reps=8)
    _, group, _ = steps(build_workout(hills, ZONES, MAX_HR))
    assert group["workoutSteps"][0]["targetType"]["workoutTargetTypeKey"] == "no.target"


def test_short_warmup_is_skipped():
    day = PlannedDay(day=2, type="threshold", distance_km=5.2, quality_km=5)
    w_steps = steps(build_workout(day, ZONES, MAX_HR))
    assert len(w_steps) == 1 and (5.2 - 5) / 2 < MIN_STEP_KM


@pytest.mark.parametrize("kind", ["rest", "cross"])
def test_non_running_days_are_not_exported(kind):
    assert build_workout(PlannedDay(day=1, type=kind), ZONES, MAX_HR) is None


# ---------- даты и повторная выгрузка ----------

def week():
    return WeekPlan.model_validate({"days": [
        {"day": 1, "type": "easy", "distance_km": 6},
        {"day": 2, "type": "threshold", "distance_km": 10, "quality_km": 5},
        {"day": 3, "type": "rest"},
        {"day": 4, "type": "easy", "distance_km": 6},
        {"day": 5, "type": "cross"},
        {"day": 6, "type": "long", "distance_km": 14},
        {"day": 7, "type": "rest"},
    ]})


def test_export_days_skips_past_and_non_running():
    monday = date(2026, 10, 12)
    days = export_days(week(), monday, date(2026, 10, 18), today=date(2026, 10, 13))
    assert [(d.isoformat(), day.day) for d, day in days] == [
        ("2026-10-13", 2), ("2026-10-15", 4), ("2026-10-17", 6),
    ]


def test_export_days_respects_week_end():
    # вводные дни: неделя сохранена до среды
    days = export_days(week(), date(2026, 10, 12), date(2026, 10, 14), today=date(2026, 10, 12))
    assert [day.day for _, day in days] == [1, 2]


def test_split_previous_keeps_past_days():
    previous = [
        {"date": "2026-10-12", "workout_id": 1},
        {"date": "2026-10-13", "workout_id": 2},
        {"date": "2026-10-15", "workout_id": 3},
        {"bad": "record"},
    ]
    replace, keep = split_previous(previous, date(2026, 10, 13))
    assert replace == [2, 3]
    assert keep == [{"date": "2026-10-12", "workout_id": 1}]
    assert split_previous(None, date(2026, 10, 13)) == ([], [])
