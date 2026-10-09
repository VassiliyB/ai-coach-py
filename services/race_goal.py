# services/race_goal.py
"""Целевое время забега: разбор ввода и оценка реалистичности по VDOT. Чистые функции, без I/O.

Цель переводится в VDOT (формула Дэниелса-Гилберта) и сравнивается с текущим VDOT плюс прирост,
который реально получить за оставшиеся недели. Прирост зависит от уровня: чем выше VDOT, тем медленнее
он растёт, и ограничен долей от текущего значения.
"""
import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from services.coach_service import MAX_VDOT, calculate_vdot, format_pace, predict_race_time

# Прирост VDOT за неделю структурной подготовки в зависимости от текущего уровня (до порога, прирост)
VDOT_GAIN_PER_WEEK = ((35.0, 0.35), (45.0, 0.25), (55.0, 0.18), (math.inf, 0.12))
MAX_GAIN_SHARE = 0.12          # за один цикл VDOT растёт не больше чем на 12% от текущего
VDOT_ESTIMATE_MARGIN = 1.0     # VDOT из тренировочных пробежек занижен: запас на «скрытую» форму
AMBITIOUS_SHARE = 0.7          # цель требует больше 70% возможного прироста: амбициозно, но допустимо

MIN_PACE_SEC, MAX_PACE_SEC = 150, 720   # 2:30–12:00 /км: всё вне этого считаем ошибкой ввода

_PACE_RE = re.compile(r"^\s*(?:темп\s*)?(\d{1,2})[:.](\d{2})\s*(?:мин)?\s*/\s*(?:км|km)\s*$", re.IGNORECASE)
_PACE_WORD_RE = re.compile(r"^\s*темп\s*(\d{1,2})[:.](\d{2})\s*$", re.IGNORECASE)
_CLOCK_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$")
_HOURS_RE = re.compile(
    r"^\s*(?:(\d{1,2})\s*ч[а-я.]*)?\s*(?:(\d{1,3})\s*м[а-я.]*)?\s*(?:(\d{1,2})\s*с[а-я.]*)?\s*$", re.IGNORECASE
)


class GoalVerdict(str, Enum):
    EASY = "easy"                  # цель не быстрее текущего прогноза
    REALISTIC = "realistic"
    AMBITIOUS = "ambitious"        # достижимо, но на пределе
    UNREALISTIC = "unrealistic"    # не принимается


@dataclass(frozen=True)
class GoalAssessment:
    target_time_s: float
    target_vdot: float
    current_vdot: float
    max_gain: float               # сколько VDOT реально прибавить к забегу (с запасом на оценку)
    predicted_now_s: float        # прогноз по текущей форме
    best_realistic_s: float       # лучший реальный результат к забегу
    verdict: GoalVerdict

    @property
    def needed_gain(self) -> float:
        return self.target_vdot - self.current_vdot

    @property
    def accepted(self) -> bool:
        return self.verdict != GoalVerdict.UNREALISTIC


def format_duration(seconds: float) -> str:
    """'1:45:00' или '25:30' для результатов меньше часа."""
    total = int(round(seconds))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def goal_pace_text(time_s: float, distance_m: float) -> str:
    return f"{format_pace(time_s / (distance_m / 1000))} /км"


def _pace_ok(time_s: float, distance_m: float) -> bool:
    return MIN_PACE_SEC <= time_s / (distance_m / 1000) <= MAX_PACE_SEC


def parse_goal(text: str, distance_m: float) -> Optional[int]:
    """Целевое время в секундах из ввода пользователя. None, если ввод не распознан или нереален как темп.

    Понимает время ('1:45:00', '45:30', '1:45', '1 ч 45 мин') и темп ('4:58/км', 'темп 4:58').
    Два числа через двоеточие читаются как мм:сс, а если такой темп невозможен, как ч:мм.
    """
    text = (text or "").strip().lower().replace(",", ".")
    if not text or distance_m <= 0:
        return None

    pace = _PACE_RE.match(text) or _PACE_WORD_RE.match(text)
    if pace:
        minutes, seconds = int(pace.group(1)), int(pace.group(2))
        if seconds >= 60:
            return None
        time_s = round((minutes * 60 + seconds) * distance_m / 1000)
        return time_s if _pace_ok(time_s, distance_m) else None

    clock = _CLOCK_RE.match(text)
    if clock:
        a, b, c = int(clock.group(1)), int(clock.group(2)), clock.group(3)
        if b >= 60 or (c is not None and int(c) >= 60):
            return None
        if c is not None:
            candidates = [a * 3600 + b * 60 + int(c)]
        else:
            candidates = [a * 60 + b, a * 3600 + b * 60]   # мм:сс, затем ч:мм
        return next((t for t in candidates if _pace_ok(t, distance_m)), None)

    words = _HOURS_RE.match(text)
    if words and any(words.groups()):
        h, m, s = (int(g) if g else 0 for g in words.groups())
        time_s = h * 3600 + m * 60 + s
        return time_s if _pace_ok(time_s, distance_m) else None
    return None


def weekly_vdot_gain(vdot: float) -> float:
    return next(gain for limit, gain in VDOT_GAIN_PER_WEEK if vdot < limit)


def max_vdot_gain(current_vdot: float, weeks: int) -> float:
    """Сколько VDOT реально прибавить за weeks недель подготовки, с запасом на заниженную оценку формы."""
    gain = min(weekly_vdot_gain(current_vdot) * max(0, weeks), current_vdot * MAX_GAIN_SHARE)
    return gain + VDOT_ESTIMATE_MARGIN


def assess_goal(current_vdot: float, target_time_s: float, distance_m: float, weeks: int) -> GoalAssessment:
    """Оценка цели: какой VDOT она требует и успевает ли атлет до него дорасти за weeks недель."""
    target_vdot = calculate_vdot(distance_m, target_time_s)
    max_gain = max_vdot_gain(current_vdot, weeks)
    best_vdot = min(current_vdot + max_gain, MAX_VDOT)
    needed = target_vdot - current_vdot

    if target_vdot > MAX_VDOT or needed > max_gain:
        verdict = GoalVerdict.UNREALISTIC
    elif needed <= 0:
        verdict = GoalVerdict.EASY
    elif needed > max_gain * AMBITIOUS_SHARE:
        verdict = GoalVerdict.AMBITIOUS
    else:
        verdict = GoalVerdict.REALISTIC

    return GoalAssessment(
        target_time_s=target_time_s,
        target_vdot=round(target_vdot, 1),
        current_vdot=round(current_vdot, 1),
        max_gain=round(max_gain, 1),
        predicted_now_s=predict_race_time(current_vdot, distance_m),
        best_realistic_s=predict_race_time(best_vdot, distance_m),
        verdict=verdict,
    )


def realistic_goal_s(current_vdot: float, distance_m: float, weeks: int) -> int:
    """Предлагаемая цель: результат при приросте VDOT на AMBITIOUS_SHARE от возможного, округлено до 30 с вверх."""
    vdot = min(current_vdot + max_vdot_gain(current_vdot, weeks) * AMBITIOUS_SHARE, MAX_VDOT)
    return int(math.ceil(predict_race_time(vdot, distance_m) / 30) * 30)


# Дистанции мастера /plan: callback кнопки -> (название в плане, метры)
RACE_DISTANCES = {
    "dist_5km": ("5 км", 5000.0),
    "dist_10km": ("10 км", 10000.0),
    "dist_21km": ("21.1 км (Полумарафон)", 21097.5),
    "dist_42km": ("42.2 км (Марафон)", 42195.0),
}
_RACE_KM_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*км")

GOAL_FORMATS = (
    "Отправьте целевое время (<code>1:45:00</code>, <code>45:30</code>, <code>1 ч 45 мин</code>) "
    "или темп (<code>4:58/км</code>)."
)


def race_distance_m(target_race: str) -> Optional[float]:
    """Дистанция забега в метрах по названию из плана. None, если дистанцию не определить."""
    for name, meters in RACE_DISTANCES.values():
        if name == target_race:
            return meters
    match = _RACE_KM_RE.search(target_race or "")
    if not match:
        return None
    km = _num_km(match.group(1))
    known = {round(m / 1000, 1): m for _, m in RACE_DISTANCES.values()}   # 21.1 -> 21097.5
    return known.get(round(km, 1), km * 1000)


def _num_km(text: str) -> float:
    return float(text.replace(",", "."))


VERDICT_TEXT = {
    GoalVerdict.EASY: "не быстрее вашей текущей формы",
    GoalVerdict.REALISTIC: "реалистичная",
    GoalVerdict.AMBITIOUS: "амбициозная, но достижимая",
    GoalVerdict.UNREALISTIC: "нереалистичная",
}


def goal_prompt_text(assessment: GoalAssessment, distance_m: float) -> str:
    """Строка о цели для промпта макроплана: все числа посчитаны кодом."""
    return (
        f"Целевое время: {format_duration(assessment.target_time_s)} "
        f"(темп {goal_pace_text(assessment.target_time_s, distance_m)}), нужен VDOT {assessment.target_vdot:g} "
        f"при текущем {assessment.current_vdot:g}; цель {VERDICT_TEXT[assessment.verdict]}. "
        "Учитывай это при выборе объёма, но правила роста километража важнее цели."
    )


def render_goal_line(assessment: GoalAssessment, distance_m: float) -> str:
    """Строка для сообщения Telegram (без HTML-тегов из ввода: все значения посчитаны кодом)."""
    return (
        f"⏱ Цель: <b>{format_duration(assessment.target_time_s)}</b> "
        f"(темп {goal_pace_text(assessment.target_time_s, distance_m)}) · {VERDICT_TEXT[assessment.verdict]}\n"
        f"Прогноз по текущей форме: {format_duration(assessment.predicted_now_s)} (VDOT {assessment.current_vdot:g}), "
        f"для цели нужен VDOT {assessment.target_vdot:g}"
    )


def render_goal_rejection(assessment: GoalAssessment, weeks: int) -> str:
    """Почему цель не принята и какой результат достижим (HTML, все числа от кода)."""
    return (
        f"🚫 <b>Цель {format_duration(assessment.target_time_s)} нереалистична</b>\n\n"
        f"Для неё нужен VDOT {assessment.target_vdot:g}, сейчас {assessment.current_vdot:g}. "
        f"За {weeks} нед. реально прибавить около {assessment.max_gain:g}, "
        f"поэтому лучший достижимый результат к забегу примерно "
        f"<b>{format_duration(assessment.best_realistic_s)}</b>.\n"
        "Слишком быстрая цель ведёт к перетренированности и травмам."
    )


def render_forecast_line(current_vdot: float, distance_m: float) -> str:
    """Прогноз результата по текущей форме (HTML)."""
    predicted = predict_race_time(current_vdot, distance_m)
    return (
        f"Прогноз по текущей форме: <b>{format_duration(predicted)}</b> "
        f"({goal_pace_text(predicted, distance_m)}, VDOT {current_vdot:.1f})"
    )


def fastest_realistic_s(current_vdot: float, distance_m: float, weeks: int) -> int:
    """Самая быстрая цель, которую assess_goal ещё примет: лучший реальный результат, округлённый до 5 с вверх."""
    best_vdot = min(current_vdot + max_vdot_gain(current_vdot, weeks), MAX_VDOT)
    return int(math.ceil(predict_race_time(best_vdot, distance_m) / 5) * 5)


def render_goal_after_review(assessment: GoalAssessment, distance_m: float) -> str:
    """Цель после пересмотра VDOT: строка цели или предупреждение, что она стала нереалистичной."""
    if assessment.accepted:
        return render_goal_line(assessment, distance_m)
    return (
        f"⚠️ Цель <b>{format_duration(assessment.target_time_s)}</b> теперь нереалистична: для неё нужен "
        f"VDOT {assessment.target_vdot:g}, а к забегу реально около "
        f"<b>{format_duration(assessment.best_realistic_s)}</b>. Измените цель в /show_plan."
    )
