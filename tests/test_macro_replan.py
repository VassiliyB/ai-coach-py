import pytest

from schemas.plan import MacroPlan
from services.macro_replan import (
    BREAK_START_SHARE,
    adjustment_after_replan,
    replan_macro,
    replan_reasons,
    replan_weekly_km,
    start_km_after,
)
from services.plan_renderer import render_replan
from services.plan_validator import MAX_WEEKLY_GROWTH, TAPER_PHASE
from services.week_adaptation import WeekAdjustment, WeekReview, review_to_dict

OLD = [30, 32, 34, 36, 38, 40, 32, 42, 44, 46, 36, 25]
PHASES = [1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4]


def macro():
    return MacroPlan.model_validate({
        "phases": [
            {"number": 1, "weeks": 3, "focus": "База"},
            {"number": 2, "weeks": 4},
            {"number": 3, "weeks": 4},
            {"number": 4, "weeks": 1},
        ],
        "weekly_km": OLD,
    })


def assert_rules(new, from_week):
    """Свойства пересчёта: прошлое не трогаем, выше плана не поднимаем, рост ≤10%, подводка не растёт."""
    first = from_week - 1
    assert new[:first] == OLD[:first]
    assert len(new) == len(OLD)
    for i in range(first, len(new)):
        assert new[i] <= OLD[i]
        assert new[i] > 0
        if i > first:
            reference = max(new[max(first, i - 2):i])
            assert new[i] <= reference * (1 + MAX_WEEKLY_GROWTH) + 1e-6, i
        if i > 0 and PHASES[i] == TAPER_PHASE and PHASES[i - 1] == TAPER_PHASE:
            assert new[i] <= new[i - 1]


# ---------- поводы ----------

@pytest.mark.parametrize("gap,compliances,expected", [
    (3, [0.9, 0.95], 0),
    (7, [0.9, 0.95], 1),                     # перерыв неделю
    (2, [0.6, 0.5], 1),                      # две слабые недели подряд
    (2, [None, 0.5], 0),                     # про прошлую неделю ничего не известно
    (2, [0.9, 0.5], 0),                      # одна слабая неделя: хватает уровня 1
    (10, [0.4, 0.2], 2),
])
def test_replan_reasons(gap, compliances, expected):
    assert len(replan_reasons(gap, compliances)) == expected


# ---------- пересчёт километража ----------

@pytest.mark.parametrize("from_week,start", [(6, 22), (3, 15), (2, 31), (11, 20), (12, 10), (1, 18)])
def test_replan_keeps_rules(from_week, start):
    new = replan_weekly_km(OLD, PHASES, from_week, start)
    assert_rules(new, from_week)
    assert new[from_week - 1] == min(start, OLD[from_week - 1])


def test_caught_up_follows_original_plan():
    """Небольшой недобор быстро догоняется, дальше план как был, со своей разгрузкой."""
    new = replan_weekly_km(OLD, PHASES, 2, 31)
    assert new[2:] == OLD[2:]


def test_long_rebuild_has_own_deload():
    new = replan_weekly_km(OLD, PHASES, 3, 15)
    rebuild = new[2:11]
    assert any(b < a for a, b in zip(rebuild, rebuild[1:], strict=False))     # есть снижение
    assert new[-1] < new[-2]                                                   # подводка ниже


def test_taper_scaled_to_new_volume():
    new = replan_weekly_km(OLD, PHASES, 11, 18)
    assert new[11] == pytest.approx(25 * 18 / 36, abs=0.5)


def test_out_of_range_week_returns_copy():
    assert replan_weekly_km(OLD, PHASES, 13, 10) == OLD


def test_replan_macro_keeps_phases():
    m = macro()
    replan = replan_macro(m, 6, 22, ["перерыв в беге 9 дн."])
    assert [p.weeks for p in replan.macro.phases] == [3, 4, 4, 1]
    assert replan.macro.phases[0].focus == "База"
    assert replan.changes()[0] == (6, 40, 22)
    assert m.weekly_km == OLD                                                  # исходный не изменился


# ---------- перерыв и поправка недели ----------

def test_break_start_capped():
    assert start_km_after(40, 38, gap_days=9) == 40 * BREAK_START_SHARE
    assert start_km_after(40, 24, gap_days=9) == 24
    assert start_km_after(40, 38, gap_days=2) == 38


def test_adjustment_follows_replan():
    replan = replan_macro(macro(), 6, 22, ["перерыв"])
    adj = adjustment_after_replan(WeekAdjustment(target_km=40), replan, gap_days=9)
    assert adj.target_km == 22 and adj.max_quality == 1 and adj.changed
    adj2 = adjustment_after_replan(WeekAdjustment(target_km=26, max_quality=0, reasons=["x"]), replan, gap_days=0)
    assert adj2.max_quality == 0 and adj2.reasons == ["x"]


# ---------- сообщение и хранение ----------

def test_replan_message():
    text = render_replan(replan_macro(macro(), 6, 22, ["перерыв в беге 9 дн."]))
    assert "План пересчитан" in text and "перерыв в беге 9 дн." in text
    assert "нед. 6–12" in text and "было: 40 · 32" in text and "стало: <b>22" in text


def test_review_to_dict():
    data = review_to_dict(WeekReview(40, 26, 2, 1, 0, 7))
    assert data == {"planned_km": 40, "done_km": 26, "compliance": 0.65}
    assert review_to_dict(WeekReview(40, 26, 2, 1, 0, 2))["compliance"] is None
