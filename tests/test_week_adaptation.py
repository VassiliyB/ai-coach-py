from datetime import date

import pytest

from clients.garmin.analytics import parse_hrv_status, parse_training_readiness
from schemas.plan import WeekPlan
from services.coach_service import calculate_zones
from services.plan_validator import validate_week
from services.week_adaptation import (
    FATIGUE_SHARE,
    RESTART_SHARE,
    RecoverySignals,
    RunFact,
    WeekReview,
    adjust_next_week,
    fatigue_reasons,
    review_week,
    run_facts,
)

ZONES = calculate_zones(45)
MAX_HR = 190
MONDAY = date(2026, 10, 12)
SUNDAY = date(2026, 10, 18)
EASY_PACE = (ZONES.easy.fast + ZONES.easy.slow) / 2


def plan_week():
    """40 км: 2 качественных (Вт, Чт), длительный в воскресенье."""
    return WeekPlan.model_validate({"days": [
        {"day": 1, "type": "rest"},
        {"day": 2, "type": "threshold", "distance_km": 8, "quality_km": 4},
        {"day": 3, "type": "easy", "distance_km": 6},
        {"day": 4, "type": "interval", "distance_km": 8, "quality_km": 3},
        {"day": 5, "type": "rest"},
        {"day": 6, "type": "easy", "distance_km": 6},
        {"day": 7, "type": "long", "distance_km": 12},
    ]})


def easy(day, km, hr=130):
    return RunFact(day=date(2026, 10, day), distance_km=km, pace_sec=EASY_PACE, avg_hr=hr)


def quality(day, km):
    return RunFact(day=date(2026, 10, day), distance_km=km, pace_sec=EASY_PACE, avg_hr=150, anaerobic_te=2.8)


def review(planned_km=40, done_km=40, planned_q=2, done_q=2, easy_hr_high=0, days=7):
    return WeekReview(planned_km, done_km, planned_q, done_q, easy_hr_high, days)


# ---------- факт недели ----------

def test_full_week_review():
    runs = [quality(13, 8), easy(14, 6), quality(15, 8), easy(17, 6), easy(18, 12)]
    r = review_week(plan_week(), MONDAY, runs, SUNDAY, ZONES, MAX_HR)
    assert (r.planned_km, r.done_km) == (40, 40)
    assert (r.planned_quality, r.done_quality) == (2, 2)
    assert r.compliance == 1


def test_sunday_long_run_not_yet_done_is_not_counted():
    """Рассылка в воскресенье в 15:00: длительный может быть ещё впереди."""
    runs = [quality(13, 8), easy(14, 6), quality(15, 8), easy(17, 6)]
    r = review_week(plan_week(), MONDAY, runs, SUNDAY, ZONES, MAX_HR)
    assert r.planned_km == 28 and r.compliance == 1


def test_runs_outside_week_ignored():
    runs = [easy(11, 10), easy(14, 6), easy(19, 10)]
    assert review_week(plan_week(), MONDAY, runs, SUNDAY, ZONES, MAX_HR).done_km == 6


def test_too_early_in_week_no_compliance():
    """/test_week во вторник: оценивать выполнение рано."""
    r = review_week(plan_week(), MONDAY, [], date(2026, 10, 13), ZONES, MAX_HR)
    assert r.compliance is None


def test_easy_runs_with_high_hr_counted():
    runs = [easy(14, 6, hr=165), easy(17, 6, hr=168), easy(18, 12, hr=130)]
    assert review_week(plan_week(), MONDAY, runs, SUNDAY, ZONES, MAX_HR).easy_hr_high == 2


def test_quality_detected_by_pace_without_training_effect():
    fast = RunFact(day=date(2026, 10, 13), distance_km=8, pace_sec=ZONES.threshold.fast, avg_hr=170)
    assert review_week(plan_week(), MONDAY, [fast], SUNDAY, ZONES, MAX_HR).done_quality == 1


def test_run_facts_from_garmin_format():
    raw = [
        {"is_running": True, "distance_km": 8.2, "start_time": "2026-10-13 07:15:00", "avg_pace_sec": 330,
         "avg_heart_rate": 140, "anaerobic_te": 0.5},
        {"is_running": True, "distance_km": 0, "start_time": "2026-10-14 07:15:00"},     # без дистанции
        {"is_running": True, "distance_km": 5, "start_time": None},                      # без даты
    ]
    (fact,) = run_facts(raw)
    assert fact.day == date(2026, 10, 13) and fact.distance_km == 8.2 and fact.avg_hr == 140


# ---------- поправка ----------

def test_no_data_no_change():
    adj = adjust_next_week(44, None)
    assert adj.target_km == 44 and adj.max_quality is None and not adj.changed


def test_normal_week_no_change_but_summary():
    adj = adjust_next_week(44, review())
    assert adj.target_km == 44 and not adj.changed
    assert "40 из 40 км (100%)" in adj.summary


def test_low_compliance_limits_growth_from_actual():
    adj = adjust_next_week(44, review(done_km=26, done_q=1))      # 65%
    assert adj.target_km == 28.5                                   # 26 × 1.1 = 28.6 → 28.5
    assert adj.max_quality == 1 and adj.changed


def test_break_week_restarts_from_floor_without_quality():
    adj = adjust_next_week(44, review(done_km=0, done_q=0))
    assert adj.target_km == pytest.approx(44 * RESTART_SHARE, abs=0.5)
    assert adj.max_quality == 0


def test_never_above_macro_plan():
    adj = adjust_next_week(30, review(done_km=55))                 # перевыполнение
    assert adj.target_km == 30 and adj.changed
    assert "перевыполнена" in adj.reasons[0]


def test_two_missed_quality_sessions_limit_quality():
    adj = adjust_next_week(44, review(done_q=0))
    assert adj.max_quality == 1 and adj.target_km == 44


def test_one_missed_quality_only_note():
    adj = adjust_next_week(44, review(done_q=1))
    assert adj.max_quality is None and "не переносится" in adj.reasons[0]


@pytest.mark.parametrize("signals,rev,tired", [
    (RecoverySignals(hrv_status="LOW"), None, True),                            # сильный
    (RecoverySignals(readiness=20), None, True),                                # сильный
    (RecoverySignals(hrv_status="UNBALANCED"), None, False),                    # один слабый
    (RecoverySignals(hrv_status="UNBALANCED", readiness=40), None, True),       # два слабых
    (RecoverySignals(readiness=40), review(easy_hr_high=2), True),
    (RecoverySignals(hrv_status="BALANCED", readiness=80), review(easy_hr_high=1), False),
])
def test_fatigue_detection(signals, rev, tired):
    assert bool(fatigue_reasons(rev, signals)) is tired


def test_fatigue_lightens_week():
    adj = adjust_next_week(44, review(), RecoverySignals(hrv_status="LOW"))
    assert adj.target_km == pytest.approx(44 * FATIGUE_SHARE, abs=0.5)
    assert adj.max_quality == 1
    assert "усталости" in adj.reasons[-1] and "HRV" in adj.reasons[-1]


def test_strongest_limits_combine():
    adj = adjust_next_week(44, review(done_km=5, done_q=0), RecoverySignals(readiness=10))
    assert adj.max_quality == 0
    assert adj.target_km <= 44 * FATIGUE_SHARE


# ---------- валидатор с пределом качественных ----------

def test_validator_respects_max_quality():
    week = plan_week()
    assert not any("допустимо не больше" in p for p in validate_week(week, max_quality=2))
    assert any("допустимо не больше 1" in p for p in validate_week(week, max_quality=1))


# ---------- данные Garmin о восстановлении ----------

@pytest.mark.parametrize("raw,expected", [
    ({"hrvSummary": {"status": "BALANCED", "weeklyAvg": 60}}, "BALANCED"),
    ({"hrvSummary": {"status": "low"}}, "LOW"),
    ({"hrvSummary": {"status": "NONE"}}, None),
    ({}, None),
    (None, None),
])
def test_parse_hrv_status(raw, expected):
    assert parse_hrv_status(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ([{"score": 40, "timestamp": "2026-10-18T06:00:00"}, {"score": 72, "timestamp": "2026-10-18T09:00:00"}], 72),
    ({"score": 55}, 55),
    ([{"level": "LOW"}], None),
    ([], None),
    (None, None),
])
def test_parse_training_readiness(raw, expected):
    assert parse_training_readiness(raw) == expected
