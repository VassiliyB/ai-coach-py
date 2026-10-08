# services/plan_paces.py
"""Подстановка темпов и пульса: зону дня определяет тип тренировки, а значения считает код."""
from typing import Dict, Optional, Tuple

from schemas.plan import PlannedDay
from services.coach_service import PaceRange, TrainingZones

_ZONE_ATTR = {
    "E": "easy",
    "M": "marathon",
    "T": "threshold",
    "I": "interval",
    "R": "repetition",
}

# Пульсовые диапазоны в долях от ЧССmax (из sports_knowledge.txt). Для зоны R пульс не задаётся:
# отрезки слишком короткие, пульс не успевает выйти на плато.
HR_FRACTIONS: Dict[str, Tuple[float, float]] = {
    "E": (0.65, 0.79),
    "M": (0.80, 0.85),
    "T": (0.88, 0.92),
    "I": (0.95, 1.00),
}


def pace_for_zone(zone: Optional[str], zones: Optional[TrainingZones]) -> Optional[PaceRange]:
    if not zone or zones is None:
        return None
    attr = _ZONE_ATTR.get(zone)
    return getattr(zones, attr) if attr else None


def pace_text(day: PlannedDay, zones: Optional[TrainingZones]) -> str:
    """Темп дня в виде '4:15 /км' или '5:30–6:10 /км'. Пустая строка, если темп неприменим или зон нет."""
    pace = pace_for_zone(day.zone, zones)
    return str(pace) if pace else ""


DURATION_ROUND_MIN = 5   # оценка длительности приблизительная: округляем до 5 минут


def _mid(pace: PaceRange) -> float:
    return (pace.fast + pace.slow) / 2


def km_for_minutes(minutes: float, zones: TrainingZones) -> float:
    """Сколько км пробегается за minutes в середине лёгкого диапазона (зона E)."""
    return minutes * 60 / _mid(zones.easy)


def estimate_duration_min(day: PlannedDay, zones: Optional[TrainingZones]) -> Optional[int]:
    """Оценка длительности тренировки в минутах, кратная 5. None без зон или для дня без бега.

    Рабочая часть идёт в темпе своей зоны, остальная дистанция (разминка, заминка, восстановление
    между отрезками) в середине лёгкого диапазона.
    """
    if zones is None or not day.distance_km:
        return None
    easy = _mid(zones.easy)
    work_km = day.quality_km or 0.0
    zone_pace = pace_for_zone(day.zone, zones)
    work_pace = _mid(zone_pace) if zone_pace else easy
    minutes = (work_km * work_pace + (day.distance_km - work_km) * easy) / 60
    return max(DURATION_ROUND_MIN, round(minutes / DURATION_ROUND_MIN) * DURATION_ROUND_MIN)


def hr_range_for_zone(zone: Optional[str], max_hr: Optional[int]) -> Optional[Tuple[int, int]]:
    if not zone or not max_hr or max_hr <= 0:
        return None
    fractions = HR_FRACTIONS.get(zone)
    if not fractions:
        return None
    return round(max_hr * fractions[0]), round(max_hr * fractions[1])


def hr_text(day: PlannedDay, max_hr: Optional[int]) -> str:
    """Пульсовой диапазон дня, например '130–158 уд/мин'. Пустая строка, если он неприменим."""
    hr = hr_range_for_zone(day.zone, max_hr)
    return f"{hr[0]}–{hr[1]} уд/мин" if hr else ""