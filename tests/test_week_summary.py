from datetime import date

from services.coach_service import calculate_zones
from services.week_adaptation import RunFact, WeekReview
from services.week_summary import (
    EasyHr,
    has_content,
    hr_trend_text,
    render_week_summary,
    summarize_week,
)

ZONES = calculate_zones(42.0)            # лёгкий примерно 5:35–6:40 /км
MAX_HR = 190
MONDAY = date(2026, 10, 5)
SUNDAY = date(2026, 10, 11)


def _run(day: int, km: float, pace: str, hr=None, monday=MONDAY):
    minutes, seconds = map(int, pace.split(":"))
    return RunFact(day=date.fromordinal(monday.toordinal() + day - 1), distance_km=km,
                   pace_sec=minutes * 60 + seconds, avg_hr=hr)


THIS_WEEK = [
    _run(2, 8.0, "6:05", 140),
    _run(4, 10.0, "4:50", 165),      # темповая: лучшая по усилию
    _run(6, 16.0, "6:10", 142),      # самая длинная
]
LAST_WEEK = [_run(d, 8.0, "6:08", 146, monday=date(2026, 9, 28)) for d in (2, 4, 6)]


def test_summary_counts_and_picks_runs():
    s = summarize_week(MONDAY, THIS_WEEK + LAST_WEEK, SUNDAY, zones=ZONES, hr_basis=MAX_HR)
    assert (s.runs, s.done_km) == (3, 34.0)
    assert s.minutes == round((8 * 365 + 10 * 290 + 16 * 370) / 60)
    assert s.best.distance_km == 10.0 and s.longest.distance_km == 16.0
    assert s.easy_now.runs == 2 and s.easy_before.runs == 3       # темповая не лёгкая
    assert round(s.easy_now.hr) == 141 and round(s.easy_before.hr) == 146


def test_best_needs_three_km_and_trend_needs_zones():
    s = summarize_week(MONDAY, [_run(3, 2.0, "4:00", 170)], SUNDAY)
    assert s.best is None and s.longest.distance_km == 2.0
    assert s.easy_now is None                                        # без VDOT лёгкость не определить


def test_review_only_when_compliance_known():
    early = WeekReview(10, 8, 0, 0, 0, days_covered=2)
    full = WeekReview(40, 34, 2, 1, 0, days_covered=7)
    assert summarize_week(MONDAY, THIS_WEEK, SUNDAY, early).review is None
    assert summarize_week(MONDAY, THIS_WEEK, SUNDAY, full).review is full


def test_hr_trend_verdicts():
    before = EasyHr(146, 368, 3)
    assert "форма растёт" in hr_trend_text(EasyHr(141, 366, 2), before)
    assert "усталость" in hr_trend_text(EasyHr(151, 372, 2), before)
    assert "без заметных изменений" in hr_trend_text(EasyHr(147, 365, 2), before)
    # Темп сильно быстрее: пульс выше ожидаем, вывода нет
    assert hr_trend_text(EasyHr(152, 340, 2), before).endswith("6:08 /км")
    assert hr_trend_text(EasyHr(141, 366, 2), None) == "141 уд/мин при 6:06 /км (2 проб.)"
    assert hr_trend_text(None, before) is None


def test_render_with_plan():
    review = WeekReview(40, 34, 2, 1, 0, days_covered=7)
    text = render_week_summary(summarize_week(MONDAY, THIS_WEEK + LAST_WEEK, SUNDAY, review, ZONES, MAX_HR))
    assert "📋 <b>Итог недели</b> 05.10–11.10" in text
    assert "Выполнено: <b>34 из 40 км</b> (85%), качественных 1 из 2" in text
    assert "Пробежек: 3, 3 ч 16 мин" in text          # 8×6:05 + 10×4:50 + 16×6:10 = 11740 с
    assert "Лучшая по усилию: Чт, 10 км в темпе 4:50 /км" in text
    assert "Самая длинная: Сб, 16 км в темпе 6:10 /км" in text
    assert "Пульс на лёгких: 141 уд/мин" in text and "форма растёт" in text


def test_render_without_plan_and_empty_week():
    text = render_week_summary(summarize_week(MONDAY, [_run(2, 5.0, "6:00")], SUNDAY))
    assert "Пробежано: <b>5 км</b>" in text and "Лучшая по усилию" in text
    assert "Самая длинная" not in text                               # та же пробежка
    empty = summarize_week(MONDAY, LAST_WEEK, SUNDAY)
    assert not has_content(empty)
    with_plan = summarize_week(MONDAY, [], SUNDAY, WeekReview(30, 0, 2, 0, 0, days_covered=7))
    assert has_content(with_plan)
    assert "Пробежек в Garmin за неделю нет." in render_week_summary(with_plan)
