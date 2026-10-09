# services/race_result.py
"""Результат забега, введённый вручную (/race): разбор ввода, проверки, приоритет над VDOT по тренировкам.
Чистые функции, без I/O.

VDOT из тренировочных пробежек это нижняя оценка формы: на тренировке не бегут на пределе. Результат забега
по Дэниелсу самая точная оценка, поэтому он заменяет VDOT по тренировкам. Пока забег свежий
(RACE_VALID_DAYS), /sync не меняет VDOT, а в оценке цели нет запаса на «скрытую» форму.
Дальше форму пересматривает план раз в 4 недели (services.vdot_review).
"""
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional

from services.coach_service import MARATHON_M, MAX_VDOT, MIN_VDOT, calculate_vdot
from services.race_goal import VDOT_ESTIMATE_MARGIN, format_duration, goal_pace_text, parse_goal

RACE_VALID_DAYS = 60              # за 8+ недель форма заметно меняется: старый забег не оценка текущей
MIN_RACE_M, MAX_RACE_M = 1500.0, MARATHON_M   # формула Дэниелса-Гилберта рассчитана на 1500 м – марафон
HALF_MARATHON_M = 21097.5

# Округлённые километры -> точная дистанция: «21.1 км» и «42 км» обычно означают полумарафон и марафон
_KNOWN_KM = {21.0: HALF_MARATHON_M, 21.1: HALF_MARATHON_M, 42.0: MARATHON_M, 42.2: MARATHON_M}
_DISTANCE_NAMES = {
    "марафон": MARATHON_M,
    "полумарафон": HALF_MARATHON_M,
    "пм": HALF_MARATHON_M,
}
_DISTANCE_RE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(км|km|к|k|м|m)?$", re.IGNORECASE)
_DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{2}|\d{4}))?$")

RACE_HELP = (
    "Отправьте результат забега: <code>/race 10к 45:30</code>, можно с датой: "
    "<code>/race полумарафон 1:41:20 14.09</code>.\n"
    f"Дистанции от 1500 м до марафона, забег не старше {RACE_VALID_DAYS} дней. "
    "Без даты считается, что забег был сегодня."
)


@dataclass(frozen=True)
class RaceResult:
    distance_m: float
    time_s: int
    race_date: date

    @property
    def vdot(self) -> float:
        return round(calculate_vdot(self.distance_m, self.time_s), 1)

    def valid_until(self) -> date:
        return self.race_date + timedelta(days=RACE_VALID_DAYS)


def parse_distance(text: str) -> Optional[float]:
    """Дистанция в метрах: '10к', '5 км', '3000 м', '21.1', 'полумарафон'. Число без единиц до 50 это км."""
    text = (text or "").strip().lower().replace("ё", "е")
    if text in _DISTANCE_NAMES:
        return _DISTANCE_NAMES[text]
    match = _DISTANCE_RE.match(text)
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    unit = (match.group(2) or "").lower()
    if unit in ("м", "m") or (not unit and value > 50):
        meters = value
    else:
        meters = _KNOWN_KM.get(round(value, 1), value * 1000)
    return meters if MIN_RACE_M <= meters <= MAX_RACE_M else None


def parse_race_date(text: str, today: date) -> Optional[date]:
    """'14.09' (ближайшая прошедшая такая дата), '14.09.26' или '14.09.2026'."""
    match = _DATE_RE.match(text.strip())
    if not match:
        return None
    day, month, year = int(match.group(1)), int(match.group(2)), match.group(3)
    try:
        if year is None:
            parsed = date(today.year, month, day)
            return parsed if parsed <= today else date(today.year - 1, month, day)
        return date(int(year) + (2000 if len(year) == 2 else 0), month, day)
    except ValueError:
        return None


def parse_race(text: str, today: date) -> Optional[RaceResult]:
    """'<дистанция> <время или темп> [дата]' -> RaceResult. None, если ввод не распознан.

    Дистанция и время могут состоять из нескольких слов ('10 км 1 ч 2 мин'), поэтому перебираются
    все разбиения: первое, где обе части распознаны, и есть результат.
    """
    tokens = (text or "").split()
    race_date = today
    if len(tokens) >= 3:
        parsed_date = parse_race_date(tokens[-1], today)
        if parsed_date is not None:
            race_date, tokens = parsed_date, tokens[:-1]
    for split in range(1, len(tokens)):
        distance_m = parse_distance(" ".join(tokens[:split]))
        if distance_m is None:
            continue
        time_s = parse_goal(" ".join(tokens[split:]), distance_m)
        if time_s is not None:
            return RaceResult(distance_m, time_s, race_date)
    return None


def check_race(
    race: RaceResult, today: date, reviewed_on: Optional[date], saved: Optional[RaceResult] = None,
) -> Optional[str]:
    """Почему результат не принят (текст для пользователя) или None, если всё в порядке.

    reviewed_on: дата последней оценки формы (пересмотр или прошлый забег). Забег раньше неё устарел:
    после него уже была более свежая оценка, например снижение после перерыва. Исключение: дата
    сохранённого забега, его можно исправить.
    """
    if race.race_date > today:
        return "Дата забега в будущем. Укажите дату прошедшего забега."
    if (today - race.race_date).days > RACE_VALID_DAYS:
        return (
            f"Забег старше {RACE_VALID_DAYS} дней уже не отражает текущую форму. "
            "Подойдёт результат свежего забега или контрольной пробежки на пределе."
        )
    if reviewed_on and race.race_date < reviewed_on and not (saved and saved.race_date == reviewed_on):
        return (
            f"Форма уже оценивалась позже этого забега ({reviewed_on:%d.%m.%Y}): "
            "та оценка свежее, забег не учитывается."
        )
    if not (MIN_VDOT <= race.vdot <= MAX_VDOT):
        return (
            f"Результат даёт VDOT {race.vdot:g}, вне допустимого диапазона {MIN_VDOT:g}–{MAX_VDOT:g}. "
            "Проверьте ввод."
        )
    return None


def race_from_profile(profile: Optional[Any]) -> Optional[RaceResult]:
    distance_m = getattr(profile, "race_result_distance_m", None)
    time_s = getattr(profile, "race_result_time_s", None)
    race_date = getattr(profile, "race_result_date", None)
    if not (distance_m and time_s and race_date):
        return None
    return RaceResult(distance_m, int(time_s), race_date)


def active_race(profile: Optional[Any], today: date) -> Optional[RaceResult]:
    """Сохранённый забег, если он ещё свежий и задаёт оценку формы."""
    race = race_from_profile(profile)
    if race is None or (today - race.race_date).days > RACE_VALID_DAYS:
        return None
    return race


def goal_margin(profile: Optional[Any], today: date) -> float:
    """Запас на «скрытую» форму в оценке цели: у VDOT по свежему забегу его нет."""
    return 0.0 if active_race(profile, today) else VDOT_ESTIMATE_MARGIN


def format_distance(distance_m: float) -> str:
    if distance_m == MARATHON_M:
        return "марафон"
    if distance_m == HALF_MARATHON_M:
        return "полумарафон"
    if distance_m < 3000:
        return f"{distance_m:g} м"
    return f"{distance_m / 1000:g} км"


def describe_race(race: RaceResult) -> str:
    """'10 км за 45:30 (4:33 /км), 14.09.2026' (без HTML-тегов: все значения от кода)."""
    return (
        f"{format_distance(race.distance_m)} за {format_duration(race.time_s)} "
        f"({goal_pace_text(race.time_s, race.distance_m)}), {race.race_date:%d.%m.%Y}"
    )


def render_race_saved(race: RaceResult, old_vdot: Optional[float], zones_text: str, goal_note: Optional[str]) -> str:
    """Ответ на /race (HTML). zones_text: готовый блок зон; goal_note: готовый HTML о цели плана."""
    if old_vdot and abs(old_vdot - race.vdot) >= 0.1:
        vdot_line = f"VDOT: <b>{old_vdot:g} → {race.vdot:g}</b>"
    else:
        vdot_line = f"VDOT: <b>{race.vdot:g}</b>"
    lines = [
        "🏁 <b>Результат забега сохранён</b>",
        describe_race(race),
        vdot_line,
        "",
        zones_text,
    ]
    if goal_note:
        lines += ["", goal_note]
    lines += [
        "",
        f"<i>Результат забега точнее тренировок: до {race.valid_until():%d.%m} /sync не меняет VDOT, "
        "дальше форму пересматривает план раз в 4 недели.</i>",
    ]
    return "\n".join(lines)


def render_race_status(race: Optional[RaceResult], vdot: Optional[float], today: date) -> str:
    """/race без аргументов: откуда сейчас VDOT и как ввести результат (HTML)."""
    if race is not None and (today - race.race_date).days <= RACE_VALID_DAYS:
        head = (
            f"🏁 Темпы считаются по забегу: {describe_race(race)}, VDOT {race.vdot:g} "
            f"(учитывается до {race.valid_until():%d.%m})."
        )
        if vdot and abs(vdot - race.vdot) >= 0.1:
            head += f"\nПосле пересмотра формы текущий VDOT: <b>{vdot:g}</b>."
    elif vdot:
        head = f"Сейчас VDOT <b>{vdot:g}</b> по тренировкам: это заниженная оценка, результат забега точнее."
    else:
        head = "VDOT ещё не рассчитан: результат забега сразу задаст темпы тренировок."
    return f"{head}\n\n{RACE_HELP}"
