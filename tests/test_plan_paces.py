from schemas.plan import WeekPlan
from services.coach_service import calculate_zones
from services.plan_paces import pace_text

ZONES = calculate_zones(50)


def week():
    return WeekPlan.model_validate({
        "days": [
            {"day": 1, "type": "rest"},
            {"day": 2, "type": "threshold", "distance_km": 8, "quality_km": 3},
            {"day": 3, "type": "easy", "distance_km": 5},
            {"day": 4, "type": "interval", "distance_km": 8, "quality_km": 2},
            {"day": 5, "type": "cross"},
            {"day": 6, "type": "long", "distance_km": 10},
            {"day": 7, "type": "easy", "distance_km": 6},
        ]
    })


def test_threshold_day_gets_threshold_pace():
    assert pace_text(week().days[1], ZONES) == str(ZONES.threshold)


def test_interval_day_gets_interval_pace():
    assert pace_text(week().days[3], ZONES) == str(ZONES.interval)


def test_easy_and_long_use_easy_range():
    w = week()
    assert pace_text(w.days[2], ZONES) == str(ZONES.easy)
    assert pace_text(w.days[5], ZONES) == str(ZONES.easy)
    assert "–" in pace_text(w.days[2], ZONES)      # лёгкий темп задаётся диапазоном


def test_rest_and_cross_have_no_pace():
    w = week()
    assert pace_text(w.days[0], ZONES) == ""
    assert pace_text(w.days[4], ZONES) == ""


def test_missing_zones_gives_empty_string():
    assert pace_text(week().days[1], None) == ""