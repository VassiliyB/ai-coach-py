from datetime import date, timedelta

import pytest

from services.coach_service import calculate_zones, predict_race_time
from services.plan_renderer import render_vdot_review
from services.race_goal import assess_goal, render_goal_after_review
from services.vdot_review import (
    MAX_RAISE,
    VdotChange,
    break_loss,
    is_review_week,
    longest_gap_days,
    review_due,
    review_vdot,
)

TODAY = date(2026, 11, 8)
HM = 21097.5


def run(days_ago, km=8.0, vdot=None, pace_sec=360):
    """Сырая пробежка Garmin. vdot: подобрать время так, чтобы пробежка давала этот VDOT."""
    meters = km * 1000
    duration = predict_race_time(vdot, meters) if vdot else pace_sec * km
    day = TODAY - timedelta(days=days_ago)
    return {"startTimeLocal": f"{day.isoformat()} 07:00:00", "distance": meters, "duration": duration}


def regular_runs(vdot=None, every=2, weeks=6):
    """Пробежки через день: перерыва нет."""
    return [run(d, vdot=vdot) for d in range(0, weeks * 7, every)]


# ---------- когда пересматривать ----------

@pytest.mark.parametrize("week,expected", [(1, False), (2, False), (4, False), (5, True), (9, True), (10, False)])
def test_review_weeks(week, expected):
    assert is_review_week(week) is expected


def test_review_not_repeated_for_same_week():
    assert review_due(5, None, TODAY)
    assert not review_due(5, TODAY - timedelta(days=3), TODAY)          # /test_week второй раз
    assert review_due(9, TODAY - timedelta(days=28), TODAY)


# ---------- перерыв ----------

def test_longest_gap():
    start, end = date(2026, 10, 1), date(2026, 10, 28)
    runs = [date(2026, 10, 1), date(2026, 10, 3), date(2026, 10, 15), date(2026, 10, 28)]
    assert longest_gap_days(runs, start, end) == 12                      # 16..27 октября
    assert longest_gap_days([], start, end) == 28
    assert longest_gap_days([date(2026, 10, 25)], start, end) == 24      # с начала окна


@pytest.mark.parametrize("gap,loss", [(3, 0), (5, 0), (6, 0.015), (28, 0.07), (60, 0.10)])
def test_break_loss_follows_daniels(gap, loss):
    assert break_loss(gap) == pytest.approx(loss)


# ---------- пересмотр ----------

def test_raise_confirmed_by_recent_run():
    runs = regular_runs() + [run(5, vdot=46)]
    review = review_vdot(45, runs, TODAY)
    assert review.change == VdotChange.RAISED and review.new == 46


def test_raise_is_capped():
    review = review_vdot(45, regular_runs() + [run(5, vdot=50)], TODAY)
    assert review.new == 45 + MAX_RAISE and "ограничено" in review.reason


def test_small_improvement_is_noise():
    review = review_vdot(45, regular_runs() + [run(5, vdot=45.3)], TODAY)
    assert review.change == VdotChange.KEPT and review.new == 45


def test_old_runs_do_not_count():
    review = review_vdot(45, regular_runs() + [run(50, vdot=50)], TODAY)    # 7 недель назад
    assert review.change == VdotChange.KEPT


def test_slow_easy_runs_do_not_lower_vdot():
    """Лёгкий бег медленнее VDOT: это не потеря формы."""
    review = review_vdot(45, regular_runs(), TODAY)
    assert review.change == VdotChange.KEPT


def test_break_lowers_vdot():
    runs = [run(d) for d in range(22, 42, 2)]                               # последние 3 недели без бега
    review = review_vdot(45, runs, TODAY)
    assert review.change == VdotChange.LOWERED
    assert review.new == pytest.approx(45 * (1 - break_loss(review.gap_days)), abs=0.1)
    assert "перерыв" in review.reason


def test_fresh_fast_run_after_break_softens_loss():
    runs = [run(d) for d in range(22, 42, 2)] + [run(1, vdot=44.5)]
    review = review_vdot(45, runs, TODAY)
    assert review.change == VdotChange.LOWERED and review.new == 44.5


def test_no_runs_at_all_is_a_break():
    review = review_vdot(45, [], TODAY)
    assert review.change == VdotChange.LOWERED and review.gap_days == 28


# ---------- сообщения ----------

def test_review_message_with_new_paces_and_goal():
    review = review_vdot(45, regular_runs() + [run(5, vdot=46)], TODAY)
    goal = render_goal_after_review(assess_goal(46, 6300, HM, 8), HM)
    text = render_vdot_review(review, calculate_zones(46), goal)
    assert "VDOT <b>45 → 46</b>" in text and "Темпы тренировок обновлены" in text
    assert "1:45:00" in text


def test_kept_review_has_no_paces():
    review = review_vdot(45, regular_runs(), TODAY)
    text = render_vdot_review(review, calculate_zones(45))
    assert "без изменений" in text and "Темпы" not in text


def test_goal_becomes_unrealistic_after_drop():
    note = render_goal_after_review(assess_goal(40, 4800, HM, 4), HM)
    assert "теперь нереалистична" in note and "/show_plan" in note


# ---------- /sync после пересмотров ----------

@pytest.mark.parametrize("sync_vdot,current,reviewed,expected", [
    (47.0, 44.0, None, 47.0),              # пересмотров не было: /sync задаёт VDOT как раньше
    (None, 44.0, None, None),
    (47.0, 44.0, TODAY, 44.0),             # после пересмотра старая пробежка VDOT не поднимает
    (43.0, 44.0, TODAY, 43.0),
    (None, 44.0, TODAY, 44.0),             # нет пробежек от 3 км: пересмотренный VDOT остаётся
    (47.0, None, TODAY, 47.0),
])
def test_vdot_after_sync(sync_vdot, current, reviewed, expected):
    from services.vdot_review import vdot_after_sync

    assert vdot_after_sync(sync_vdot, current, reviewed) == expected


def test_vdot_after_sync_keeps_race_vdot():
    from services.vdot_review import vdot_after_sync

    # Свежий забег из /race: тренировки VDOT не меняют ни вниз, ни вверх
    assert vdot_after_sync(42.0, 45.3, TODAY, race_active=True) == 45.3
    assert vdot_after_sync(47.0, 45.3, TODAY, race_active=True) == 45.3
    assert vdot_after_sync(None, 45.3, TODAY, race_active=True) == 45.3
