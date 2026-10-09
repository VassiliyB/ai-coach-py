import pytest

from services.coach_service import MAX_VDOT, calculate_vdot, predict_race_time
from services.race_goal import (
    MAX_GAIN_SHARE,
    VDOT_ESTIMATE_MARGIN,
    GoalVerdict,
    assess_goal,
    fastest_realistic_s,
    format_duration,
    goal_pace_text,
    goal_prompt_text,
    max_vdot_gain,
    parse_goal,
    race_distance_m,
    realistic_goal_s,
    render_forecast_line,
    render_goal_line,
)

HM = 21097.5
M = 42195.0


# ---------- разбор ввода ----------

@pytest.mark.parametrize("text,distance,expected", [
    ("1:45:00", HM, 6300),
    ("1:45", HM, 6300),              # мм:сс дал бы темп 0:05/км: читаем как ч:мм
    ("45:30", 10000, 2730),          # для 10 км это мм:сс
    ("25:00", 5000, 1500),
    ("3:30", M, 12600),
    ("1 ч 45 мин", HM, 6300),
    ("1ч45м", HM, 6300),
    ("95 мин", HM, 5700),
    ("4:58/км", HM, round(298 * 21.0975)),
    ("4.58 /km", 10000, 2980),
    ("темп 5:10", 10000, 3100),
    ("5:10 мин/км", 10000, 3100),
])
def test_parse_goal(text, distance, expected):
    assert parse_goal(text, distance) == expected


@pytest.mark.parametrize("text,distance", [
    ("", HM),
    ("быстро", HM),
    ("45:30", HM),                   # ни мм:сс, ни ч:мм не дают реального темпа
    ("1:75:00", HM),
    ("2:00/км", 10000),              # быстрее мирового рекорда
    ("15:00/км", 10000),             # это ходьба, не бег
    ("10 мин", M),
])
def test_parse_goal_rejects(text, distance):
    assert parse_goal(text, distance) is None


def test_format_duration_and_pace():
    assert format_duration(6300) == "1:45:00"
    assert format_duration(1530) == "25:30"
    assert goal_pace_text(3000, 10000) == "5:00 /км"


# ---------- прирост VDOT ----------

def test_gain_grows_with_weeks_and_is_capped():
    assert max_vdot_gain(45, 8) < max_vdot_gain(45, 16)
    cap = 45 * MAX_GAIN_SHARE + VDOT_ESTIMATE_MARGIN
    assert max_vdot_gain(45, 100) == pytest.approx(cap)


def test_gain_slower_for_higher_level():
    assert max_vdot_gain(60, 12) < max_vdot_gain(40, 12)


# ---------- оценка цели ----------

def test_example_half_marathon_in_1_20_is_unrealistic():
    """Полумарафон за 1:20 требует VDOT ~58: при VDOT 45 за 12 недель не успеть."""
    a = assess_goal(45, 4800, HM, 12)
    assert a.verdict == GoalVerdict.UNREALISTIC and not a.accepted
    assert a.target_vdot == pytest.approx(calculate_vdot(HM, 4800), abs=0.1)
    assert a.best_realistic_s > 4800                 # лучший реальный результат медленнее цели
    assert a.best_realistic_s < a.predicted_now_s    # но быстрее, чем сейчас


def test_goal_slower_than_current_form_is_easy():
    a = assess_goal(45, predict_race_time(45, HM) + 300, HM, 12)
    assert a.verdict == GoalVerdict.EASY and a.accepted


def test_small_improvement_is_realistic_large_is_ambitious():
    weeks = 12
    gain = max_vdot_gain(45, weeks)
    realistic = assess_goal(45, predict_race_time(45 + gain * 0.4, HM), HM, weeks)
    ambitious = assess_goal(45, predict_race_time(45 + gain * 0.9, HM), HM, weeks)
    too_much = assess_goal(45, predict_race_time(45 + gain * 1.1, HM), HM, weeks)
    assert realistic.verdict == GoalVerdict.REALISTIC
    assert ambitious.verdict == GoalVerdict.AMBITIOUS
    assert too_much.verdict == GoalVerdict.UNREALISTIC


def test_more_weeks_make_goal_reachable():
    goal = predict_race_time(48, HM)
    assert assess_goal(44, goal, HM, 4).verdict == GoalVerdict.UNREALISTIC
    assert assess_goal(44, goal, HM, 20).accepted


def test_beyond_vdot_scale_is_unrealistic():
    a = assess_goal(80, predict_race_time(MAX_VDOT + 1, 10000), 10000, 52)
    assert a.verdict == GoalVerdict.UNREALISTIC


def test_suggested_goal_is_accepted_and_not_ambitious():
    for vdot in (32, 45, 60):
        for weeks in (4, 12, 30):
            goal = realistic_goal_s(vdot, HM, weeks)
            a = assess_goal(vdot, goal, HM, weeks)
            assert a.verdict == GoalVerdict.REALISTIC, (vdot, weeks)
            assert goal % 30 == 0


def test_texts_contain_code_numbers():
    a = assess_goal(45, 6000, HM, 12)
    prompt = goal_prompt_text(a, HM)
    assert "1:40:00" in prompt and f"VDOT {a.target_vdot:g}" in prompt
    line = render_goal_line(a, HM)
    assert "<b>1:40:00</b>" in line and format_duration(a.predicted_now_s) in line


# ---------- /show_plan ----------

@pytest.mark.parametrize("name,expected", [
    ("21.1 км (Полумарафон)", HM),
    ("42.2 км (Марафон)", M),
    ("10 км", 10000),
    ("21.1 км", HM),                 # старое название без пояснения
    ("15 км", 15000),
    ("Бег", None),
])
def test_race_distance_from_plan_name(name, expected):
    assert race_distance_m(name) == expected


def test_fastest_realistic_goal_is_accepted_and_boundary():
    for vdot in (35, 45, 55):
        for weeks in (0, 6, 16):
            fastest = fastest_realistic_s(vdot, HM, weeks)
            assert assess_goal(vdot, fastest, HM, weeks).accepted, (vdot, weeks)
            assert not assess_goal(vdot, fastest - 30, HM, weeks).accepted, (vdot, weeks)


def test_forecast_line():
    line = render_forecast_line(45, HM)
    assert format_duration(predict_race_time(45, HM)) in line and "VDOT 45.0" in line


def test_zero_margin_for_race_vdot_is_stricter():
    # VDOT по забегу точный: без запаса на «скрытую» форму возможный прирост меньше ровно на запас
    assert max_vdot_gain(45, 12) - max_vdot_gain(45, 12, margin=0.0) == pytest.approx(VDOT_ESTIMATE_MARGIN)
    assert fastest_realistic_s(45, HM, 12, margin=0.0) > fastest_realistic_s(45, HM, 12)
    assert realistic_goal_s(45, HM, 12, margin=0.0) >= realistic_goal_s(45, HM, 12)
    # Цель на границе с запасом без запаса уже нереалистична
    edge = fastest_realistic_s(45, HM, 12)
    assert assess_goal(45, edge, HM, 12).accepted
    assert not assess_goal(45, edge, HM, 12, margin=0.0).accepted
