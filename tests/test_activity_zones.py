import pytest

from clients.garmin.analytics import parse_last_activity
from services.activity_zones import classify_activity, format_activity_zones, hr_zone, pace_zone
from services.coach_service import calculate_zones
from services.heart_rate import KIND_LTHR, HeartRateBasis

ZONES = calculate_zones(41.8)   # E 5:39–6:45, M 5:15, T 4:55, I 4:30, R 4:16
MAX_HR = 180


@pytest.mark.parametrize("pace,expected", [
    (420, "below_E"),   # 7:00 — медленнее лёгкого диапазона
    (344, "E"),         # 5:44
    (330, "E"),         # 5:30 — чуть быстрее E, но ближе к E, чем к M
    (316, "M"),         # 5:16
    (300, "T"),         # 5:00
    (268, "I"),         # 4:28
    (250, "R"),         # 4:10
])
def test_pace_zone(pace, expected):
    assert pace_zone(pace, ZONES) == expected


def test_pace_zone_needs_zones_and_pace():
    assert pace_zone(344, None) is None
    assert pace_zone(None, ZONES) is None
    assert pace_zone(0, ZONES) is None


@pytest.mark.parametrize("hr,expected", [
    (115, ("below_E", 64)),
    (140, ("E", 78)),
    (151, ("M", 84)),
    (155, ("M", 86)),   # промежуток 85–88% относится к зоне ниже
    (167, ("T", 93)),
    (172, ("I", 96)),
])
def test_hr_zone(hr, expected):
    assert hr_zone(hr, MAX_HR) == expected


def test_hr_zone_needs_hr_and_max():
    assert hr_zone(None, MAX_HR) is None
    assert hr_zone(150, None) is None


def test_easy_pace_with_gray_zone_pulse():
    # тренировка из живой проверки: лёгкий темп, но пульс 84% ЧССmax
    result = classify_activity(344, 151, ZONES, MAX_HR)
    assert (result.pace_zone, result.hr_zone, result.hr_pct) == ("E", "M", 84)
    assert result.gray_zone and result.hr_above_pace
    text = format_activity_zones(result, 344, 151, ZONES, MAX_HR)
    assert "Средний темп 5:44 /км: зона E" in text
    assert "151 уд/мин = 84% от ЧССmax 180: зона M (серая зона)" in text
    assert "Пульс лёгкого бега (зона E) для атлета: 117–142 уд/мин" in text
    assert "серую зону" in text and "Пульс выше зоны" in text


def test_peak_hr_line():
    result = classify_activity(344, 151, ZONES, MAX_HR)
    text = format_activity_zones(result, 344, 151, ZONES, MAX_HR, peak_hr=166)
    assert "Максимальный пульс за тренировку 166 уд/мин = 92% от ЧССmax 180: зона T (порог)" in text
    assert "Максимальный пульс" not in format_activity_zones(result, 344, 151, ZONES, MAX_HR)


def test_clean_easy_run():
    result = classify_activity(370, 135, ZONES, MAX_HR)
    assert not result.gray_zone and not result.hr_above_pace
    text = format_activity_zones(result, 370, 135, ZONES, MAX_HR)
    assert "серую зону" not in text and "Пульс выше зоны" not in text


def test_without_vdot_and_hr():
    result = classify_activity(344, None, None, None)
    text = format_activity_zones(result, 344, None, None, None)
    assert "по темпу не определена" in text and "по пульсу не определена" in text


def test_parse_last_activity_gives_pace_seconds():
    act = parse_last_activity({"activityType": {"typeKey": "running"}, "distance": 10000.0, "duration": 3440.0})
    assert act["avg_pace_sec"] == 344.0
    assert parse_last_activity({"distance": 0.0, "duration": 600.0})["avg_pace_sec"] is None


def test_slow_recovery_run_is_not_flagged():
    # живой опрос: 8 км в темпе 6:50 (медленнее E) при пульсе 125 (69%, зона E) — нормальный восстановительный бег
    result = classify_activity(410, 125, ZONES, MAX_HR)
    assert (result.pace_zone, result.hr_zone) == ("below_E", "E")
    assert not result.hr_above_pace and not result.gray_zone


def test_fast_pace_low_pulse_is_not_flagged():
    # интервалы со средним пульсом ниже зоны темпа (пульс не успевает подняться) — не тревога
    assert not classify_activity(270, 150, ZONES, MAX_HR).hr_above_pace


def test_slow_pace_with_gray_pulse_is_flagged():
    # медленнее E, но пульс 84%: тревожный признак (усталость, жара)
    assert classify_activity(420, 151, ZONES, MAX_HR).hr_above_pace


def test_zones_from_lthr():
    # Тот же пульс 151 от ПАНО 170 это 89%: верх лёгкой зоны, а не серая зона
    lthr = HeartRateBasis(170, KIND_LTHR, manual=True)
    result = classify_activity(344, 151, ZONES, lthr)
    assert (result.hr_zone, result.hr_pct) == ("E", 89)
    text = format_activity_zones(result, 344, 151, ZONES, lthr)
    assert "151 уд/мин = 89% от пульса ПАНО 170: зона E" in text
    assert "Пульс лёгкого бега (зона E) для атлета: 128–151 уд/мин" in text
