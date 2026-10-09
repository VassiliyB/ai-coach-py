# services/plan_paces.py
"""Подстановка темпов и пульса: зону дня определяет тип тренировки, а значения считает код."""
from typing import Optional, Tuple

from schemas.plan import PlannedDay
from services.coach_service import PaceRange, TrainingZones
from services.heart_rate import HrRef, as_basis

_ZONE_ATTR = {
    "E": "easy",
    "M": "marathon",
    "T": "threshold",
    "I": "interval",
    "R": "repetition",
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


def hr_range_for_zone(zone: Optional[str], hr_basis: HrRef) -> Optional[Tuple[int, int]]:
    """Пульсовой диапазон зоны от ЧССmax или ПАНО (services.heart_rate); число это ЧССmax."""
    basis = as_basis(hr_basis)
    return basis.range_for_zone(zone) if basis and zone else None


def hr_text(day: PlannedDay, hr_basis: HrRef) -> str:
    """Пульсовой диапазон дня, например '130–158 уд/мин'. Пустая строка, если он неприменим."""
    hr = hr_range_for_zone(day.zone, hr_basis)
    return f"{hr[0]}–{hr[1]} уд/мин" if hr else ""
