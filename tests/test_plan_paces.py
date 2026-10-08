from schemas.plan import WeekPlan
from services.coach_service import calculate_zones
from services.plan_paces import DURATION_ROUND_MIN, estimate_duration_min, km_for_minutes, pace_text

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


# ---------- Пульс ----------

from services.plan_paces import hr_range_for_zone, hr_text  # noqa: E402


def test_hr_ranges_for_max_hr_200():
    assert hr_range_for_zone("E", 200) == (130, 158)
    assert hr_range_for_zone("T", 200) == (176, 184)
    assert hr_range_for_zone("I", 200) == (190, 200)


def test_hr_not_defined_for_repetition_and_unknown():
    assert hr_range_for_zone("R", 200) is None
    assert hr_range_for_zone(None, 200) is None
    assert hr_range_for_zone("E", None) is None


def test_hr_text_for_days():
    w = week()
    assert hr_text(w.days[1], 200) == "176–184 уд/мин"   # пороговая
    assert hr_text(w.days[0], 200) == ""                  # отдых
    assert hr_text(w.days[1], None) == ""


# ---------- оценка длительности ----------

def day_of(type_, distance, quality=None):
    d = {"day": 1, "type": type_, "distance_km": distance}
    if quality is not None:
        d["quality_km"] = quality
    return WeekPlan.model_validate({"days": [d] + [{"day": n, "type": "rest"} for n in range(2, 8)]}).days[0]


def test_easy_duration_between_fast_and_slow_easy_pace():
    minutes = estimate_duration_min(week().days[5], ZONES)          # длительный 10 км
    assert minutes % DURATION_ROUND_MIN == 0
    assert ZONES.easy.fast * 10 / 60 - DURATION_ROUND_MIN <= minutes <= ZONES.easy.slow * 10 / 60 + DURATION_ROUND_MIN


def test_quality_part_is_faster_than_easy():
    # 8 км с 3 км в пороговом темпе быстрее 8 км целиком в лёгком темпе
    threshold = estimate_duration_min(day_of("threshold", 8, quality=3), ZONES)
    assert threshold < estimate_duration_min(day_of("easy", 8), ZONES)


def test_duration_unknown_without_zones_or_for_rest():
    assert estimate_duration_min(week().days[2], None) is None
    assert estimate_duration_min(week().days[0], ZONES) is None     # отдых
    assert estimate_duration_min(week().days[4], ZONES) is None     # ОФП


def test_short_run_rounds_to_minimum():
    assert estimate_duration_min(day_of("easy", 0.5), ZONES) == DURATION_ROUND_MIN


def test_km_for_minutes_matches_easy_pace():
    mid = (ZONES.easy.fast + ZONES.easy.slow) / 2
    assert abs(km_for_minutes(60, ZONES) - 3600 / mid) < 1e-9
